import json
import logging
import os
import re
from argparse import ArgumentParser, Namespace
from collections import defaultdict
from contextlib import contextmanager
from datetime import date
from pathlib import Path
from time import perf_counter

from psycopg import Error as ErroPostgreSQL

from db.conexao import RAIZ, conectar
from processamento.carga import assinatura_arquivo
from processamento.importar_mapbiomas import classes_da_legenda, importar
from processamento.mapbiomas_armazenamento import MIB, conferir_banco, conferir_local, estimar
from processamento.mapbiomas_blocos import publicar_arquivo
from processamento.mapbiomas_historico_db import conferir_origem, estado_ano

REGISTRO = logging.getLogger(__name__)
ANOS = range(1985, 2025)
PREFIXO = "geogarimpo_mapbiomas_c11_v1_"
PADRAO = re.compile(rf"{PREFIXO}(\d{{4}})-(\d{{10}})-(\d{{10}})[^.]*\.tiff?", re.IGNORECASE)
LADO = 16384
LIMITES = (-74.1, -34.0, -28.5, 5.5)
LEGENDA = "legend_code_mapbiomas_mining_brazil_collection_11_mined_substances.zip"
URL_LEGENDA = f"https://brasil.mapbiomas.org/wp-content/uploads/sites/3/2026/10/{LEGENDA}"
PASTA_MAPBIOMAS = RAIZ / "dados" / "brutos" / "mapbiomas" / "colecao11"
PILOTO = RAIZ / "dados" / "processados" / "mapbiomas" / "2025" / "inspecao.json"
MANIFESTO_PILOTO = RAIZ / "dados" / "metadados" / "mapbiomas_2025.json"
PASTA_RETOMADA = RAIZ / "dados" / "processados" / "mapbiomas" / "historico"


def inventariar(entrada, grade):
    if not entrada.is_dir():
        raise ValueError(f"Informe uma pasta existente de GeoTIFFs: {entrada}.")
    arquivos, posicoes = defaultdict(list), defaultdict(set)
    for caminho in sorted(entrada.rglob("*")):
        encontrados = PADRAO.fullmatch(caminho.name)
        if encontrados is None or not caminho.is_file():
            continue
        ano, linha, coluna = map(int, encontrados.groups())
        arquivos[ano].append(caminho)
        posicoes[ano].add((linha, coluna))
    previstas = {(linha, coluna) for linha in range(0, grade["altura"], LADO)
                 for coluna in range(0, grade["largura"], LADO)}
    return arquivos, posicoes, previstas


@contextmanager
def bloquear_preparacao(pasta):
    pasta.mkdir(parents=True, exist_ok=True)
    with (pasta / ".carga-historica.lock").open("a+b") as arquivo:
        if not arquivo.tell():
            arquivo.write(b"0")
            arquivo.flush()
        arquivo.seek(0)
        try:
            if os.name == "nt":
                import msvcrt

                msvcrt.locking(arquivo.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl

                fcntl.flock(arquivo, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as erro:
            raise ValueError("Outra preparação histórica está ativa neste workspace.") from erro
        yield


def salvar_json(caminho, dados):
    caminho.parent.mkdir(parents=True, exist_ok=True)
    publicar_arquivo(caminho, lambda fluxo: fluxo.write(json.dumps(dados, ensure_ascii=False, indent=2).encode("utf-8")))


def estimativa_preliminar(piloto, bytes_entrada):
    return estimar(max(piloto["poligonos"], bytes_entrada // 1500), max(128 * MIB, bytes_entrada // 2))


def verificar_antes_do_ano(ano, parametros, manifesto, classes, grade, piloto, tamanho):
    with conectar(escrita=True) as conexao:
        conexao.execute("SET LOCAL statement_timeout = '30s'")
        situacao, registro = estado_ano(conexao, ano)
        if registro:
            conferir_origem(registro, manifesto, {"grade": grade}, classes, URL_LEGENDA)
        if situacao == "validado":
            validacao = registro["metadados"]["validacao_publicacao"]
            REGISTRO.info("%s ignorado, com %s polígonos validados, %.2f ha.",
                          ano, validacao["poligonos"], validacao["area_vetor_ha"])
            return False
        if (situacao == "incompleto") != (parametros.reiniciar_incompleto == ano) or situacao == "sem_validacao":
            raise ValueError(f"O estado de {ano} é {situacao}. Verifique a origem e a autorização antes de prosseguir.")
        espaco = conferir_banco(conexao, estimativa_preliminar(piloto, tamanho))
        REGISTRO.info("Espaço da VM para %s: %s", ano, espaco)
    return True


def executar(parametros):
    piloto = json.loads(parametros.referencia.read_text(encoding="utf-8"))
    grade = piloto["grade"]
    if (piloto["ano"] != 2025 or piloto.get("publicado") is not True or grade["nodata"] != -32768
            or grade["crs"] != "EPSG:4326"):
        raise ValueError("A referência deve ser a inspeção publicada de 2025 com EPSG:4326 e NoData -32768.")
    classes = classes_da_legenda(parametros.legenda)
    if assinatura_arquivo(parametros.legenda) != piloto["sha256_legenda"] or classes != piloto["classes_selecionadas"]:
        raise ValueError("A legenda local diverge da edição e da seleção oficiais usadas em 2025.")
    arquivos, posicoes, previstas = inventariar(parametros.entrada, grade)
    selecionados = [parametros.ano] if parametros.ano is not None else list(ANOS)
    completos = [ano for ano in selecionados if posicoes[ano] == previstas]
    parciais = {ano: len(posicoes[ano]) for ano in selecionados if arquivos[ano] and ano not in completos}
    REGISTRO.info("Plano. %s classes oficiais de garimpo; %s posições esperadas por ano.", len(classes), len(previstas))
    REGISTRO.info("Lotes completos pelo inventário: %s. Lotes parciais: %s. Ausentes: %s.", completos, parciais,
                  [ano for ano in selecionados if not arquivos[ano]])
    REGISTRO.info("Arquivos de 2025 preservados: %s. Limite por execução: %s ano(s).",
                  len(arquivos[2025]), parametros.limite_anos)
    if parametros.verificar_espaco:
        with conectar(escrita=True) as conexao:
            REGISTRO.info("Sondagem da VM com referência ao piloto: %s",
                          conferir_banco(conexao, estimativa_preliminar(piloto, 0)))
    if not (parametros.executar or parametros.somente_preparar):
        return
    if not completos:
        raise ValueError("Nenhum lote histórico contém todas as posições esperadas. Complete a exportação primeiro.")
    modelo = json.loads(MANIFESTO_PILOTO.read_text(encoding="utf-8"))
    preparados = 0
    with bloquear_preparacao(PASTA_RETOMADA):
        for ano in completos:
            inicio = perf_counter()
            pasta = parametros.retomar_em / str(ano)
            tamanho = sum(arquivo.stat().st_size for arquivo in arquivos[ano])
            manifesto = dict(modelo, nome=f"MapBiomas Brasil — Substância Minerada {ano}",
                             adquirido_em=parametros.adquirido_em.isoformat(),
                             metodologia=f"Banda classification_{ano}, exportação na grade do piloto de 2025. "
                                         "Seleção da legenda oficial e vetorização em janelas sem simplificação.")
            manifesto["metadados"] = dict(modelo["metadados"], banda_ativo=f"classification_{ano}",
                                          evidencia_aquisicao="Data informada para o lote neste processamento.")
            if parametros.executar and not verificar_antes_do_ano(ano, parametros, manifesto, classes, grade, piloto, tamanho):
                continue
            conferir_local(pasta, tamanho)
            caminho_manifesto = pasta / "manifesto.json"
            salvar_json(caminho_manifesto, manifesto)
            REGISTRO.info("Início de %s. %s arquivos; %s MiB.", ano, len(arquivos[ano]), tamanho // MIB)
            try:
                resultado = importar(Namespace(
                    arquivo=parametros.entrada, arquivos=arquivos[ano], metadados=caminho_manifesto,
                    ano=ano, banda=1, legenda_arquivo=parametros.legenda, classes=classes, legenda=URL_LEGENDA,
                    retomar_em=pasta, prefixo_exportacao=f"{PREFIXO}{ano}", somente_preparar=parametros.somente_preparar,
                    relatorio=pasta / "inspecao.json", limites_exportacao=LIMITES, substituir_ano=False,
                    grade_referencia=grade, reiniciar_incompleto=parametros.reiniciar_incompleto,
                ))
            except (ValueError, OSError, ErroPostgreSQL):
                REGISTRO.error("A etapa de %s foi interrompida após %.1f s. Os blocos concluídos podem ser retomados.",
                               ano, perf_counter() - inicio)
                raise
            REGISTRO.info("%s: %s polígonos, %.2f ha, %.1f s.", ano, resultado["poligonos"],
                          resultado["area_raster_ha"], perf_counter() - inicio)
            preparados += 1
            if preparados >= parametros.limite_anos:
                break


def principal():
    cli = ArgumentParser(description="Planejar, preparar ou carregar o MapBiomas histórico por ano.")
    cli.add_argument("--entrada", type=Path, default=RAIZ / "dados" / "geo_tif")
    cli.add_argument("--retomar-em", type=Path, default=PASTA_RETOMADA)
    cli.add_argument("--referencia", type=Path, default=PILOTO)
    cli.add_argument("--legenda", type=Path, default=PASTA_MAPBIOMAS / LEGENDA)
    modo = cli.add_mutually_exclusive_group()
    modo.add_argument("--executar", action="store_true")
    modo.add_argument("--somente-preparar", action="store_true")
    modo.add_argument("--verificar-espaco", action="store_true")
    cli.add_argument("--ano", type=int, metavar="1985–2024")
    cli.add_argument("--limite-anos", type=int, default=1)
    cli.add_argument("--adquirido-em", type=date.fromisoformat, metavar="AAAA-MM-DD")
    cli.add_argument("--reiniciar-incompleto", type=int, metavar="ANO")
    parametros = cli.parse_args()
    if parametros.ano is not None and parametros.ano not in ANOS:
        cli.error("A carga histórica aceita somente os anos 1985 a 2024.")
    if not 1 <= parametros.limite_anos <= len(ANOS):
        cli.error("O limite de anos deve estar entre 1 e 40.")
    if (parametros.executar or parametros.somente_preparar) and (
        parametros.adquirido_em is None or parametros.adquirido_em > date.today()
    ):
        cli.error("Informe a data real de aquisição dos arquivos com --adquirido-em.")
    if parametros.reiniciar_incompleto is not None and (
        not parametros.executar or parametros.ano != parametros.reiniciar_incompleto
    ):
        cli.error("--reiniciar-incompleto exige --executar e --ano com o mesmo ano histórico.")
    try:
        executar(parametros)
    except (ValueError, OSError) as erro:
        cli.error(str(erro))
    except ErroPostgreSQL as erro:
        cli.error(f"A verificação do PostgreSQL falhou ({type(erro).__name__}). Confira a disponibilidade e o espaço da VM.")


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    principal()
