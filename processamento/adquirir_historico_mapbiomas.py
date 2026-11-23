import json
import logging
import os
from argparse import ArgumentParser, Namespace
from datetime import datetime
from importlib.util import find_spec
from pathlib import Path
from zoneinfo import ZoneInfo

from dotenv import dotenv_values
from psycopg import Error as ErroPostgreSQL

from db.conexao import RAIZ, conectar
from processamento.carga import assinatura_arquivo
from processamento.importar_historico_mapbiomas import (
    ANOS, LADO, LEGENDA, LIMITES, MANIFESTO_PILOTO, PASTA_MAPBIOMAS, PASTA_RETOMADA, PILOTO, PREFIXO, URL_LEGENDA,
    bloquear_preparacao, estimativa_preliminar, salvar_json, verificar_antes_do_ano,
)
from processamento.importar_mapbiomas import classes_da_legenda, importar, registros_da_legenda
from processamento.mapbiomas_armazenamento import conferir_banco
from processamento.mapbiomas_historico_db import conferir_origem, estado_ano

REGISTRO = logging.getLogger(__name__)
ENTRADA = RAIZ / "dados" / "geo_tif"
AQUISICAO = PASTA_RETOMADA / "aquisicao"
SQL_AREAS = RAIZ / "db" / "consultas" / "area_garimpo_total_uf.sql"
FUSO = ZoneInfo("America/Sao_Paulo")


def ler_json(caminho):
    return json.loads(caminho.read_text(encoding="utf-8")) if caminho.exists() else {}


def medir_vm(estimativa, *, exigir=True):
    with conectar(escrita=True) as conexao:
        conexao.execute("SET LOCAL statement_timeout = '30s'")
        espaco = conferir_banco(conexao, estimativa, exigir=exigir)
        tamanhos = conexao.execute("""
            SELECT pg_database_size(current_database()) AS banco_bytes, coalesce(sum(size),0)::bigint AS wal_bytes
            FROM pg_ls_waldir()
        """).fetchone()
    return dict(espaco, **tamanhos)


def consultar_ano(ano, modelo, grade, classes):
    with conectar() as conexao:
        situacao, registro = estado_ano(conexao, ano)
        if registro:
            conferir_origem(registro, modelo, {"grade": grade}, classes, URL_LEGENDA)
        return situacao, registro


def areas_por_uf(ano, registro, caminho):
    existente = ler_json(caminho)
    if existente.get("fonte_id") == registro["fonte_id"] and existente.get("sha256") == registro["sha256"]:
        return existente["areas"]
    with conectar() as conexao:
        conexao.execute("SET LOCAL statement_timeout = '20min'")
        conexao.execute("SELECT set_config('geogarimpo.ano',%s,true)", (str(ano),))
        areas = conexao.execute(SQL_AREAS.read_text(encoding="utf-8")).fetchall()
        ufs = conexao.execute("SELECT sigla FROM unidades_federativas").fetchall()
    territorios = {linha["sigla"] for linha in ufs} | {"Brasil"}
    if {linha["territorio"] for linha in areas} != territorios or len(areas) != len(territorios):
        raise ValueError(f"A apuração de área de {ano} não retornou o Brasil e todas as UFs.")
    total = next(linha["area_ha"] for linha in areas if linha["territorio"] == "Brasil")
    validacao = registro["metadados"]["validacao_publicacao"]
    if abs(total - validacao["area_vetor_ha"]) > max(0.01, total * 1e-7):
        raise ValueError(f"A área nacional dissolvida de {ano} diverge da área validada na carga.")
    salvar_json(caminho, dict(fonte_id=registro["fonte_id"], sha256=registro["sha256"], areas=areas))
    return areas


def processar_ano(ano, google, piloto, modelo, classes, legenda, codigos_validos, parametros, grade_google, previstas, item):
    from processamento.mapbiomas_google import adquirir_ano

    projeto, drive, sessao, fonte = google
    pasta = PASTA_RETOMADA / str(ano)
    grade = piloto["grade"]
    estado, registro = consultar_ano(ano, modelo, grade, classes)
    if estado not in {"ausente", "validado"}:
        raise ValueError(f"O ano {ano} já tem registro no estado {estado}. Verifique-o pelo importador histórico.")
    item["vm_antes"] = medir_vm(estimativa_preliminar(piloto, 0))
    if estado == "ausente":
        arquivo_estado = pasta / "aquisicao_google.json"
        arquivos, origem = adquirir_ano(projeto, drive, sessao, fonte, grade_google, ano, previstas,
                                       ENTRADA / str(ano), arquivo_estado, parametros.espera_segundos,
                                       repetir_exportacao=parametros.repetir_exportacao == ano)
        if "adquirido_em" not in origem:
            origem["adquirido_em"] = datetime.now(FUSO).date().isoformat()
            salvar_json(arquivo_estado, origem)
        manifesto = dict(modelo, nome=f"MapBiomas Brasil — Substância Minerada {ano}", adquirido_em=origem["adquirido_em"],
                         metodologia=f"Exportação da banda classification_{ano} via API Earth Engine, com download na API Drive. "
                                     "Grade nativa e vetorização por blocos com a legenda do piloto de 2025.")
        manifesto["metadados"] = dict(modelo["metadados"], banda_ativo=f"classification_{ano}",
                                     evidencia_aquisicao="Data da conclusão da conferência dos arquivos do Drive.",
                                     projeto_earth_engine=projeto, operacao_earth_engine=origem["operacao"],
                                     pasta_drive=origem["pasta_drive_id"])
        caminho_manifesto = pasta / "manifesto.json"
        with bloquear_preparacao(PASTA_RETOMADA):
            tamanho = sum(arquivo.stat().st_size for arquivo in arquivos)
            if verificar_antes_do_ano(ano, Namespace(reiniciar_incompleto=None), manifesto, classes, grade, piloto, tamanho):
                salvar_json(caminho_manifesto, manifesto)
                importar(Namespace(
                    arquivo=ENTRADA, arquivos=arquivos, metadados=caminho_manifesto, ano=ano, banda=1,
                    legenda_arquivo=legenda, classes=classes, classes_validas=codigos_validos, legenda=URL_LEGENDA,
                    retomar_em=pasta, prefixo_exportacao=f"{PREFIXO}{ano}", somente_preparar=False,
                    relatorio=pasta / "inspecao.json", limites_exportacao=LIMITES, substituir_ano=False,
                    grade_referencia=grade, reiniciar_incompleto=None,
                ))
        estado, registro = consultar_ano(ano, modelo, grade, classes)
    if estado != "validado":
        raise ValueError(f"O banco ainda não atesta a publicação de {ano}.")
    item.update(situacao="publicado", validacao=registro["metadados"]["validacao_publicacao"], fonte_id=registro["fonte_id"])
    item["vm_apos"] = medir_vm(estimativa_preliminar(piloto, 0), exigir=False)
    item["areas_por_territorio"] = areas_por_uf(ano, registro, pasta / "areas_uf.json")
    REGISTRO.info("%s publicado e confirmado. %s polígonos, %.2f ha.", ano,
                  item["validacao"]["poligonos"], item["validacao"]["area_vetor_ha"])


def executar(parametros):
    if find_spec("ee") is None or find_spec("googleapiclient") is None:
        raise ValueError("Instale as dependências earthengine-api e google-api-python-client de requirements.txt.")
    import ee

    from processamento.mapbiomas_google import ATIVO, abrir_google, conferir_ativo, detalhar_http

    if parametros.autenticar:
        ee.Authenticate(auth_mode="localhost:0")
        if not (parametros.executar or parametros.sondar):
            REGISTRO.info("Autorização OAuth concluída.")
            return
    piloto = ler_json(PILOTO)
    modelo = ler_json(MANIFESTO_PILOTO)
    legenda = PASTA_MAPBIOMAS / LEGENDA
    classes = classes_da_legenda(legenda)
    codigos_validos = {int(linha["class_id"]) for linha in registros_da_legenda(legenda)}
    if (piloto.get("ano") != 2025 or not piloto.get("publicado") or modelo.get("metadados", {}).get("ativo") != ATIVO
            or classes != piloto["classes_selecionadas"] or assinatura_arquivo(legenda) != piloto["sha256_legenda"]):
        raise ValueError("O piloto e a legenda locais devem corresponder à coleção oficial já publicada.")
    grade = piloto["grade"]
    previstas = {(linha, coluna) for linha in range(0, grade["altura"], LADO)
                 for coluna in range(0, grade["largura"], LADO)}
    _, registro = consultar_ano(2025, modelo, grade, classes)
    if not registro or registro["registros"] != piloto["poligonos"] or registro["sha256"] != piloto["sha256_lote"]:
        raise ValueError("O piloto publicado de 2025 diverge da referência local.")
    REGISTRO.info("2025 confirmado no PostgreSQL. %s polígonos; %s partes esperadas por ano histórico.",
                  registro["registros"], len(previstas))
    REGISTRO.info("Capacidade inicial da VM: %s", medir_vm(estimativa_preliminar(piloto, 0)))
    projeto_configurado = (parametros.projeto or os.getenv("EARTH_ENGINE_PROJECT") or os.getenv("GOOGLE_CLOUD_PROJECT")
                           or dotenv_values(RAIZ / ".env", encoding="utf-8-sig", interpolate=False).get("EARTH_ENGINE_PROJECT"))
    caminho = AQUISICAO / "relatorio.json"
    with bloquear_preparacao(AQUISICAO):
        relatorio = ler_json(caminho)
        relatorio.setdefault("anos", {})
        try:
            projeto, drive, sessao = abrir_google(projeto_configurado)
            with sessao:
                fonte, grade_google = conferir_ativo(grade, classes)
                REGISTRO.info("Earth Engine e Drive autorizados no projeto %s.", projeto)
                relatorio.pop("erro", None)
                relatorio.update(projeto=projeto, ativo=ATIVO, etapa="sondagem")
                salvar_json(caminho, relatorio)
                if not parametros.executar:
                    return
                for ano in range(1985, parametros.ate + 1):
                    chave = str(ano)
                    item = relatorio["anos"].setdefault(chave, {})
                    item.pop("erro", None)
                    relatorio.update(etapa=f"processando {ano}", ano_atual=ano)
                    salvar_json(caminho, relatorio)
                    try:
                        processar_ano(ano, (projeto, drive, sessao, fonte), piloto, modelo, classes,
                                      legenda, codigos_validos, parametros, grade_google, previstas, item)
                    except Exception as erro:
                        item["erro"] = detalhar_http(erro) if not isinstance(erro, ErroPostgreSQL) else type(erro).__name__
                        raise
                    finally:
                        salvar_json(caminho, relatorio)
                relatorio.update(etapa="concluida")
                relatorio.pop("ano_atual", None)
        except KeyboardInterrupt:
            relatorio.update(erro="Execução interrompida; retome o mesmo comando.")
            raise
        except Exception as erro:
            mensagem = detalhar_http(erro) if not isinstance(erro, ErroPostgreSQL) else type(erro).__name__
            relatorio.update(erro=mensagem)
            raise ValueError(mensagem) from None
        finally:
            pendentes = [ano for ano in ANOS if relatorio["anos"].get(str(ano), {}).get("situacao") != "publicado"]
            relatorio["pendentes_de_confirmacao"] = pendentes
            salvar_json(caminho, relatorio)


def principal():
    cli = ArgumentParser(description="Adquirir pelo Earth Engine e publicar a série histórica do MapBiomas em ordem anual.")
    modo = cli.add_mutually_exclusive_group()
    modo.add_argument("--sondar", action="store_true", help="Confere credenciais, ativo e capacidade sem iniciar exportações.")
    modo.add_argument("--executar", action="store_true", help="Importa 1985 primeiro e continua após a validação.")
    cli.add_argument("--autenticar", action="store_true", help="Abre a autorização OAuth oficial no navegador local.")
    cli.add_argument("--projeto", help="ID do projeto autorizado no Google Cloud.")
    cli.add_argument("--ate", type=int, default=2024, metavar="1985–2024")
    cli.add_argument("--repetir-exportacao", type=int, metavar="ANO", help="Repete uma operação que falhou após corrigir a causa.")
    cli.add_argument("--espera-segundos", type=int, default=43200, help="Limite de espera por exportação ou fila.")
    parametros = cli.parse_args()
    if not (parametros.sondar or parametros.executar or parametros.autenticar):
        cli.error("Escolha --sondar, --executar ou --autenticar.")
    if parametros.ate not in ANOS or parametros.espera_segundos < 60:
        cli.error("Informe --ate entre 1985 e 2024 e uma espera de pelo menos 60 segundos.")
    if parametros.repetir_exportacao is not None and (
        not parametros.executar or not 1985 <= parametros.repetir_exportacao <= parametros.ate
    ):
        cli.error("Selecione um ano da execução para --repetir-exportacao.")
    try:
        executar(parametros)
    except (ValueError, OSError) as erro:
        cli.error(str(erro))
    except ErroPostgreSQL as erro:
        cli.error(f"A verificação do PostgreSQL falhou ({type(erro).__name__}).")
    except KeyboardInterrupt:
        REGISTRO.warning("Retomada disponível com o mesmo comando.")
        return 130
    return 0


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
    raise SystemExit(principal())
