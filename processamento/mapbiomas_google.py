import hashlib
import json
import logging
import math
import re
from pathlib import Path
from time import monotonic, sleep
from uuid import uuid4

import ee
import httplib2
from google.auth.transport.requests import AuthorizedSession
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError
from requests import RequestException

from processamento.importar_historico_mapbiomas import ANOS, LADO, LIMITES, PADRAO, PREFIXO, salvar_json
from processamento.mapbiomas_armazenamento import conferir_local

REGISTRO = logging.getLogger(__name__)
ATIVO = "projects/mapbiomas-public/assets/brazil/lulc/collection11/mapbiomas_brazil_collection11_mining_substances_v1"
DRIVE = "https://www.googleapis.com/drive/v3/files"
ATIVAS = {"PENDING", "READY", "RUNNING", "CANCELLING", "CANCEL_REQUESTED"}
TRANSITORIOS = {408, 429, 500, 502, 503, 504}
BLOCO_DOWNLOAD = 4 * 1024**2


class FalhaTemporaria(RuntimeError):
    pass


def repetir(funcao):
    for tentativa in range(5):
        try:
            return funcao()
        except (RequestException, TimeoutError, FalhaTemporaria):
            if tentativa == 4:
                raise ValueError("A comunicação com o Drive falhou após cinco tentativas. Retome o mesmo ano.") from None
            sleep(2 ** (tentativa + 1))


def identificar_projeto(credenciais):
    projeto = getattr(credenciais, "quota_project_id", None) or getattr(credenciais, "project_id", None)
    if projeto:
        return projeto
    url = "https://cloudresourcemanager.googleapis.com/v1/projects"
    with AuthorizedSession(credenciais) as sessao:
        resposta = sessao.get(url, params={"filter": "lifecycleState:ACTIVE", "pageSize": 100}, timeout=20)
        if resposta.ok:
            pagina = resposta.json()
            projetos = [item["projectId"] for item in pagina.get("projects", []) if item.get("projectId")]
            if len(projetos) == 1 and not pagina.get("nextPageToken"):
                return projetos[0]
    raise ValueError("Informe em --projeto o ID do projeto autorizado que aparece no seletor do Earth Engine.")


def abrir_google(projeto=None):
    credenciais = ee.data.get_persistent_credentials()
    projeto = projeto or identificar_projeto(credenciais)
    ee.Initialize(credentials=credenciais, project=projeto, http_transport=httplib2.Http(timeout=60))
    drive = build("drive", "v3", credentials=credenciais, cache_discovery=False)
    quota = drive.about().get(fields="storageQuota(limit,usage)").execute(num_retries=4).get("storageQuota", {})
    if quota.get("limit") and quota.get("usage") and int(quota["usage"]) >= int(quota["limit"]):
        raise ValueError("A cota de armazenamento do Google Drive está esgotada.")
    return projeto, drive, AuthorizedSession(credenciais)


def conferir_ativo(grade_piloto, classes):
    if not classes or not all(0 <= classe <= 32767 for classe in classes) or grade_piloto["nodata"] != -32768:
        raise ValueError("As classes ou o NoData são incompatíveis com a exportação Int16.")
    fonte = ee.Image(ATIVO)
    bandas = {banda["id"]: banda for banda in fonte.getInfo()["bands"]}
    nomes = [f"classification_{ano}" for ano in (*ANOS, 2025)]
    faltantes = [nome for nome in nomes if nome not in bandas]
    if faltantes:
        raise ValueError(f"Bandas ausentes no ativo oficial: {faltantes}.")
    referencia = bandas[nomes[-1]]
    transformacao = referencia["crs_transform"]
    if (len(transformacao) != 6 or not all(math.isfinite(n) for n in transformacao)
            or transformacao[1] != 0 or transformacao[3] != 0 or transformacao[0] <= 0 or transformacao[4] >= 0):
        raise ValueError("A projeção do ativo diverge do alinhamento retangular esperado.")
    afim = grade_piloto["transformacao"]
    if referencia["crs"] != grade_piloto["crs"] or any(
        not math.isclose(transformacao[i], afim[i], rel_tol=0, abs_tol=1e-12) for i in (0, 1, 3, 4)
    ):
        raise ValueError("O SRC ou a resolução do ativo diverge do piloto de 2025.")
    for i, passo in ((2, 0), (5, 4)):
        deslocamento = (afim[i] - transformacao[i]) / transformacao[passo]
        if not math.isclose(deslocamento, round(deslocamento), rel_tol=0, abs_tol=1e-5):
            raise ValueError("O ativo e o piloto divergem no alinhamento da origem da grade.")
    nativas_int16 = set()
    for nome in nomes:
        banda = bandas[nome]
        if banda["crs"] != referencia["crs"] or banda["crs_transform"] != transformacao:
            raise ValueError(f"A banda {nome} tem grade diferente da referência oficial de 2025.")
        tipo = banda.get("data_type", {})
        if tipo.get("precision") != "int":
            raise ValueError(f"A banda {nome} não é categórica inteira.")
        if tipo.get("min", -32769) >= -32768 and tipo.get("max", 32768) <= 32767:
            nativas_int16.add(nome)
    REGISTRO.info("Ativo oficial conferido. %s bandas, SRC %s e legenda com %s classes de garimpo.",
                  len(nomes), referencia["crs"], len(classes))
    return fonte, dict(referencia, bandas_int16=nativas_int16)


def arquivos_drive(drive, consulta, campos):
    token = None
    while True:
        resposta = drive.files().list(q=consulta, fields=f"nextPageToken,files({campos})", pageSize=1000,
                                     pageToken=token, spaces="drive").execute(num_retries=4)
        yield from resposta.get("files", [])
        token = resposta.get("nextPageToken")
        if not token:
            return


def id_pasta_drive(drive, nome):
    # O nome é montado a partir de uma constante e um ano inteiro.
    consulta = f"trashed = false and mimeType = 'application/vnd.google-apps.folder' and name = '{nome}'"
    pastas = list(arquivos_drive(drive, consulta, "id"))
    if len(pastas) > 1:
        raise ValueError(f"Há mais de uma pasta chamada {nome} no Drive. Identifique a pasta de exportação antes de retomar.")
    return pastas[0]["id"] if pastas else None


def inventario_drive(drive, id_pasta, ano, previstas):
    if id_pasta is None:
        return {}, set(previstas)
    if not re.fullmatch(r"[\w-]+", id_pasta, re.ASCII):
        raise ValueError("O Drive devolveu um identificador de pasta inválido.")
    consulta = f"trashed = false and '{id_pasta}' in parents and name contains '{PREFIXO}{ano}'"
    itens = {}
    for item in arquivos_drive(drive, consulta, "id,name,size,md5Checksum,capabilities(canDownload)"):
        nome = item["name"]
        partes = PADRAO.fullmatch(nome)
        if not partes or int(partes[1]) != ano or nome != Path(nome).name:
            continue
        posicao = (int(partes[2]), int(partes[3]))
        if posicao not in previstas:
            raise ValueError(f"O lote {ano} contém uma posição fora da grade.")
        if not item.get("size") or int(item["size"]) <= 0 or not re.fullmatch(r"[0-9a-f]{32}", item.get("md5Checksum", "")):
            continue
        if item.get("capabilities", {}).get("canDownload") is False:
            raise ValueError(f"O Drive não concedeu permissão de download para {nome}.")
        anterior = itens.get(posicao)
        if anterior and (anterior["size"], anterior["md5Checksum"]) != (item["size"], item["md5Checksum"]):
            raise ValueError(f"O Drive contém arquivos diferentes na mesma posição do ano {ano}.")
        if not anterior or item["id"] < anterior["id"]:
            itens[posicao] = item
    return itens, previstas - itens.keys()


def estado_operacao(operacao):
    metadados = operacao.get("metadata", {})
    if operacao.get("done"):
        return "FAILED" if operacao.get("error") else "SUCCEEDED"
    return metadados.get("state", "PENDING")


def iniciar_exportacao(fonte, grade, ano, nome, pasta_drive, estado, caminho, aguardar):
    operacoes = ee.data.listOperations()
    correspondentes = [o for o in operacoes if o.get("metadata", {}).get("description") == nome]
    reaproveitaveis = [o for o in correspondentes if estado_operacao(o) in ATIVAS | {"SUCCEEDED"}]
    if len(reaproveitaveis) > 1:
        raise ValueError(f"Mais de uma exportação reaproveitável encontrada para {ano}.")
    if reaproveitaveis:
        estado["operacao"] = reaproveitaveis[0]["name"]
        salvar_json(caminho, estado)
        return
    aguardar("outras exportações ativas no projeto", lambda: not any(
        estado_operacao(o) in ATIVAS for o in ee.data.listOperations()
    ))
    if "pedido" not in estado:
        estado["pedido"] = ee.data.newTaskId()[0]
        salvar_json(caminho, estado)
    nome_banda = f"classification_{ano}"
    banda = fonte.select(nome_banda)
    if nome_banda not in grade["bandas_int16"]:
        # Reserva -32767 para detectar valores fora de Int16 sem confundi-los com a máscara.
        banda = banda.where(banda.lt(-32768).Or(banda.gt(32767)), -32767)
    imagem = banda.toInt16().unmask(-32768, False)
    tarefa = ee.batch.Export.image.toDrive(
        image=imagem, description=nome, folder=pasta_drive, fileNamePrefix=f"{PREFIXO}{ano}",
        region=ee.Geometry.Rectangle(list(LIMITES), "EPSG:4326", False), crs=grade["crs"],
        crsTransform=grade["crs_transform"], maxPixels=10**12, fileDimensions=LADO, shardSize=1024,
        skipEmptyTiles=False, fileFormat="GeoTIFF", formatOptions={"cloudOptimized": True, "noData": -32768},
    )
    # O identificador persistido permite repetir com segurança uma requisição interrompida.
    estado["operacao"] = ee.data.exportImage(estado["pedido"], tarefa.config)["name"]
    salvar_json(caminho, estado)
    REGISTRO.info("%s: exportação enviada ao Earth Engine.", ano)


def aguardar_exportacao(ano, nome, pasta_drive, fonte, grade, estado, caminho, aguardar):
    for tentativa in range(3):
        numero = estado.get("tentativa_exportacao", 1)
        sufixo = "" if numero == 1 else f"_t{numero}"
        destino = pasta_drive + sufixo
        if "operacao" not in estado:
            iniciar_exportacao(fonte, grade, ano, nome + sufixo, destino, estado, caminho, aguardar)

        def consultar():
            operacao = ee.data.getOperation(estado["operacao"])
            situacao = estado_operacao(operacao)
            if situacao != estado.get("estado_exportacao"):
                estado["estado_exportacao"] = situacao
                salvar_json(caminho, estado)
                REGISTRO.info("%s: exportação %s.", ano, situacao)
            return operacao if situacao not in ATIVAS else None

        operacao = aguardar(f"exportação de {ano}", consultar)
        if estado_operacao(operacao) == "SUCCEEDED":
            return destino
        codigo = operacao.get("error", {}).get("code")
        if codigo in {4, 8, 10, 13, 14} and estado.get("novas_tentativas", 0) < 2:
            estado["novas_tentativas"] = estado.get("novas_tentativas", 0) + 1
            renovar_operacao(estado, caminho)
            sleep(30 * (2 ** tentativa))
            continue
        # Exibe a mensagem do serviço para distinguir cota, configuração e permissão.
        mensagem = operacao.get("error", {}).get("message", "sem mensagem do serviço")
        raise ValueError(f"A exportação de {ano} encerrou em {estado_operacao(operacao)} ({codigo}): {mensagem}")
    raise ValueError(f"As tentativas de exportação de {ano} foram esgotadas.")


def renovar_operacao(estado, caminho):
    estado["ultima_operacao"] = estado.pop("operacao")
    estado["tentativa_exportacao"] = estado.get("tentativa_exportacao", 1) + 1
    estado.pop("pedido", None)
    salvar_json(caminho, estado)


def espera_progressiva(segundos, etapa, consultar):
    prazo = monotonic() + segundos
    intervalo = 8
    while True:
        resultado = consultar()
        if resultado:
            return resultado
        restante = prazo - monotonic()
        if restante <= 0:
            raise ValueError(f"A espera por {etapa} chegou ao limite. Retome o mesmo comando.")
        sleep(min(intervalo, restante))
        intervalo = min(60, intervalo * 2)


def md5_disco(caminho):
    with caminho.open("rb") as arquivo:
        return hashlib.file_digest(arquivo, lambda: hashlib.md5(usedforsecurity=False)).hexdigest()


def obter_tiff(sessao, pasta, item):
    nome, identificador, total, md5 = item["name"], item["id"], int(item["size"]), item["md5Checksum"]
    if nome != Path(nome).name or not re.fullmatch(r"[\w-]+", identificador, re.ASCII):
        raise ValueError("O Drive devolveu um nome ou identificador inválido.")
    destino = pasta / nome
    parcial = pasta / f"{nome}.{md5}.parcial"
    if destino.exists():
        if destino.stat().st_size != total or md5_disco(destino) != md5:
            raise ValueError(f"O arquivo local {nome} diverge do Drive e foi preservado.")
        return destino

    def transferir():
        inicio = parcial.stat().st_size if parcial.exists() else 0
        if inicio > total or (inicio == total and md5_disco(parcial) != md5):
            parcial.rename(pasta / f"{parcial.name}.{uuid4().hex}.invalido")
            inicio = 0
        if inicio == total:
            return
        cabecalhos = {"Accept-Encoding": "identity"}
        if inicio:
            cabecalhos["Range"] = f"bytes={inicio}-"
        with sessao.get(f"{DRIVE}/{identificador}", params={"alt": "media"}, headers=cabecalhos,
                        stream=True, timeout=(15, 90)) as resposta:
            status = resposta.status_code
            if status in TRANSITORIOS:
                raise FalhaTemporaria()
            if (inicio and (status != 206 or resposta.headers.get("Content-Range") != f"bytes {inicio}-{total - 1}/{total}")):
                raise ValueError(f"O Drive não aceitou a retomada no deslocamento {inicio} de {nome} (HTTP {status}).")
            if not inicio and status != 200:
                raise ValueError(f"O Drive recusou o download de {nome} (HTTP {status}).")
            with parcial.open("ab") as fluxo:
                for bloco in resposta.iter_content(chunk_size=BLOCO_DOWNLOAD):
                    fluxo.write(bloco)
        if parcial.stat().st_size != total or md5_disco(parcial) != md5:
            raise FalhaTemporaria()

    repetir(transferir)
    parcial.rename(destino)
    return destino


def adquirir_ano(projeto, drive, sessao, fonte, grade, ano, previstas, pasta, caminho, espera, *, repetir_exportacao=False):
    nome = f"{PREFIXO}{ano}_api_drive"
    pasta_drive = f"GeoGarimpo_C11_v1_automacao_{ano}"
    estado = json.loads(caminho.read_text(encoding="utf-8")) if caminho.exists() else {}
    identidade = {"ano": ano, "projeto": projeto, "ativo": ATIVO, "pasta_drive": pasta_drive}
    if estado and any(estado.get(chave) != valor for chave, valor in identidade.items()):
        raise ValueError(f"O estado de retomada de {ano} pertence a outro projeto ou produto.")
    estado.update(identidade)
    salvar_json(caminho, estado)
    verificados = estado.get("arquivos", [])
    if len(verificados) == len(previstas) and estado.get("estado_exportacao") == "SUCCEEDED":
        posicoes = set()
        for item in verificados:
            partes = PADRAO.fullmatch(item["name"])
            if not partes or int(partes[1]) != ano or not (pasta / item["name"]).is_file():
                break
            posicoes.add((int(partes[2]), int(partes[3])))
        if posicoes == previstas:
            arquivos = [obter_tiff(sessao, pasta, item) for item in verificados]
            REGISTRO.info("%s: lote local completo reconferido por tamanho e MD5.", ano)
            return arquivos, estado
    if repetir_exportacao and estado.get("operacao"):
        operacao = ee.data.getOperation(estado["operacao"])
        if estado_operacao(operacao) in {"FAILED", "CANCELLED", "CANCELED"}:
            estado["novas_tentativas"] = 0
            renovar_operacao(estado, caminho)

    def aguardar(etapa, consulta):
        return espera_progressiva(espera, etapa, consulta)

    destino = aguardar_exportacao(ano, nome, pasta_drive, fonte, grade, estado, caminho, aguardar)

    def localizar():
        id_pasta = id_pasta_drive(drive, destino)
        itens, ausentes = inventario_drive(drive, id_pasta, ano, previstas)
        return (id_pasta, itens) if not ausentes else None

    id_pasta, itens = espera_progressiva(min(espera, 1800), f"{len(previstas)} GeoTIFFs de {ano} no Drive", localizar)
    conferir_local(pasta, sum(int(item["size"]) for item in itens.values()))
    pasta.mkdir(parents=True, exist_ok=True)
    arquivos = []
    for indice, item in enumerate((itens[posicao] for posicao in sorted(itens)), 1):
        arquivos.append(obter_tiff(sessao, pasta, item))
        if indice % 10 == 0 or indice == len(itens):
            REGISTRO.info("%s: %s/%s arquivos conferidos por tamanho e MD5.", ano, indice, len(itens))
    estado.update(pasta_drive_id=id_pasta, arquivos=[itens[posicao] for posicao in sorted(itens)])
    salvar_json(caminho, estado)
    return arquivos, estado


def detalhar_http(erro):
    if isinstance(erro, HttpError):
        status = erro.resp.status
        detalhes = erro.error_details
        motivos = [d.get("reason") for d in detalhes if isinstance(d, dict) and d.get("reason")] if isinstance(detalhes, list) else []
        return f"Google Drive HTTP {status}" + (f" ({', '.join(motivos)})" if motivos else "")
    return str(erro)
