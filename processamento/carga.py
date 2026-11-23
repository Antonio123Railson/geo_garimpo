import hashlib
import json
from collections import Counter
from datetime import date
from pathlib import Path
from urllib.parse import urlsplit

import geopandas as gpd
import pandas as pd
import pyogrio
from psycopg import sql
from psycopg.types.json import Jsonb

from processamento.validar_dados import normalizar, validar_limites

TRAVA_CARGA = 78120926


def argumentos_fonte(analisador):
    analisador.add_argument("arquivo", type=Path, help="Arquivo local da fonte.")
    analisador.add_argument("--metadados", required=True, type=Path, help="Manifesto JSON da aquisição.")
    analisador.add_argument("--confirmar-cobertura-nacional", required=True, action="store_true",
                            help="Confirma a conferência do recorte nacional do arquivo.")


def argumentos_vetor(analisador):
    argumentos_fonte(analisador)
    analisador.add_argument("--camada", help="Nome da camada no arquivo.")
    analisador.add_argument("--campo-codigo", required=True)
    analisador.add_argument("--campo-nome")


def ler_manifesto(caminho):
    campos = ("nome", "url", "colecao", "versao", "licenca", "adquirido_em", "metodologia")
    with caminho.open(encoding="utf-8-sig") as arquivo:
        valores = json.load(arquivo)
    if not isinstance(valores, dict):
        raise ValueError("O manifesto deve ser um objeto JSON.")
    for campo in campos:
        if not isinstance(valores.get(campo), str) or not valores[campo].strip():
            raise ValueError(f"Preencha o campo {campo} no manifesto.")
        valores[campo] = valores[campo].strip()
    url = urlsplit(valores["url"])
    if url.scheme not in {"http", "https"} or not url.hostname or url.username or url.password or any(
        caractere.isspace() for caractere in valores["url"]
    ):
        raise ValueError("Informe uma URL pública de aquisição HTTP ou HTTPS.")
    valores["adquirido_em"] = date.fromisoformat(valores["adquirido_em"])
    if valores["adquirido_em"] > date.today():
        raise ValueError("A data de aquisição não pode estar no futuro.")
    extras = valores.get("metadados", {})
    if not isinstance(extras, dict):
        raise ValueError("O campo metadados deve ser um objeto JSON.")
    valores["metadados"] = extras
    return valores


def assinatura_arquivo(caminho):
    arquivos = [caminho]
    if caminho.suffix.lower() == ".shp":
        arquivos = sorted(
            parte for parte in caminho.parent.iterdir()
            if parte.stem == caminho.stem and parte.suffix.lower() in (".shp", ".shx", ".dbf", ".prj", ".cpg")
        )
        if not {".shp", ".shx", ".dbf"} <= {parte.suffix.lower() for parte in arquivos}:
            raise ValueError("Mantenha .shp, .shx e .dbf juntos.")
    resumo = hashlib.sha256()
    for arquivo in arquivos:
        if len(arquivos) > 1:
            resumo.update(f"{arquivo.suffix.lower()}:{arquivo.stat().st_size}:".encode())
        with arquivo.open("rb") as fluxo:
            for bloco in iter(lambda: fluxo.read(2**20), b""):
                resumo.update(bloco)
    return resumo.hexdigest()


def registrar_fonte(conexao, tipo, manifesto, assinatura, detalhes):
    parametros = dict(manifesto, tipo=tipo, sha256=assinatura)
    parametros["metadados"] = Jsonb(dict(manifesto["metadados"], **detalhes))
    linha = conexao.execute("""
        INSERT INTO fontes_dados (tipo,nome,url,colecao,versao,licenca,adquirido_em,metodologia,sha256,metadados)
        VALUES (%(tipo)s,%(nome)s,%(url)s,%(colecao)s,%(versao)s,%(licenca)s,%(adquirido_em)s,
                %(metodologia)s,%(sha256)s,%(metadados)s)
        ON CONFLICT (tipo,url,colecao,versao,adquirido_em,sha256,metadados) DO UPDATE
          SET nome = excluded.nome, licenca = excluded.licenca, metodologia = excluded.metodologia
        RETURNING id
    """, parametros).fetchone()
    return linha["id"]


def bloquear_escritas(conexao):
    conexao.execute("SELECT pg_advisory_xact_lock(%s)", (TRAVA_CARGA,))


def invalidar_resultados(conexao, apenas_proximidades=False):
    conexao.execute("DELETE FROM analises_proximidade")
    conexao.execute("DELETE FROM execucoes_analise WHERE analise = 'proximidades'")
    if not apenas_proximidades:
        conexao.execute("DELETE FROM manchas_indigenas")
        conexao.execute("DELETE FROM estatisticas_anuais")
        conexao.execute("DELETE FROM execucoes_analise WHERE analise = 'estatisticas'")


def lotes_vetoriais(arquivo, camada, campos, *, codificacao=None):
    with pyogrio.open_arrow(arquivo, layer=camada, columns=list(dict.fromkeys(campos)),
                            encoding=codificacao, use_pyarrow=True, batch_size=20_000) as (metadados, leitor):
        crs = metadados["crs"]
        if not crs:
            raise ValueError("O arquivo não informa o SRC. Corrija a definição na origem.")
        nome_geom = metadados["geometry_name"] or "wkb_geometry"
        faltantes = set(campos) - set(leitor.schema.names)
        if faltantes:
            raise ValueError(f"Campos ausentes no arquivo: {', '.join(sorted(faltantes))}.")
        for lote in leitor:
            dados = lote.to_pandas()
            geometrias = gpd.GeoSeries.from_wkb(dados.pop(nome_geom), crs=crs).to_crs(4674)
            yield dados, geometrias


def texto(valor, campo, obrigatorio=False):
    resultado = str(valor).strip() if not pd.isna(valor) else ""
    if len(resultado) > 240 or "\x00" in resultado or (obrigatorio and not resultado):
        raise ValueError(f"Valor obrigatório ausente ou texto inválido no campo {campo}.")
    return resultado or None


def copiar_vetores(conexao, argumentos, *, tipo_geom, campo_atributo=None, classes=None):
    campos = [campo for campo in (argumentos.campo_codigo, argumentos.campo_nome, campo_atributo) if campo]
    conexao.execute("""
        CREATE TEMP TABLE vetores_carga (
            codigo text, nome text, atributo text, wkb bytea
        ) ON COMMIT DROP
    """)
    ocorrencias = Counter()
    with conexao.cursor().copy("COPY vetores_carga (codigo,nome,atributo,wkb) FROM STDIN") as carga:
        for dados, geometrias in lotes_vetoriais(
            argumentos.arquivo, argumentos.camada, campos,
            codificacao=getattr(argumentos, "codificacao", None),
        ):
            valores = dados.to_dict(orient="list")
            for indice, geometria in enumerate(geometrias):
                atributo = texto(valores[campo_atributo][indice], campo_atributo) if campo_atributo else None
                if classes is not None and atributo not in classes:
                    ocorrencias["filtradas"] += 1
                    continue
                codigo = texto(valores[argumentos.campo_codigo][indice], argumentos.campo_codigo, True)
                nome = texto(valores[argumentos.campo_nome][indice], argumentos.campo_nome) if argumentos.campo_nome else None
                try:
                    geometria = normalizar(geometria, tipo_geom, ocorrencias)
                    validar_limites(geometria)
                except ValueError as erro:
                    raise ValueError(f"Feição {codigo}: {erro}") from erro
                carga.write_row((codigo, nome, atributo, geometria.wkb))
                ocorrencias["carregadas"] += 1
    if not ocorrencias["carregadas"]:
        raise ValueError("Nenhuma feição elegível encontrada no arquivo.")
    return dict(ocorrencias)


def ativar_base(conexao, camada, fonte):
    conexao.execute("""
        INSERT INTO bases_ativas (camada, fonte_id) VALUES (%s, %s)
        ON CONFLICT (camada) DO UPDATE SET fonte_id = excluded.fonte_id
    """, (camada, fonte))


def publicar_rede(conexao, tabela, fonte):
    identificador = sql.Identifier(tabela)
    atributo = sql.SQL(", classe") if tabela != "rios" else sql.SQL("")
    valor_atributo = sql.SQL(", atributo") if tabela != "rios" else sql.SQL("")
    conexao.execute(sql.SQL("DELETE FROM {}").format(identificador))
    conexao.execute(sql.SQL("""
        INSERT INTO {tabela} (codigo,nome,fonte_id,geom{atributo})
        SELECT codigo,nome,%s,ST_GeomFromWKB(wkb,4674){valor_atributo}
        FROM vetores_carga
    """).format(tabela=identificador, atributo=atributo, valor_atributo=valor_atributo), (fonte,))
    ativar_base(conexao, tabela, fonte)
    conexao.execute(sql.SQL("ANALYZE {}").format(identificador))
