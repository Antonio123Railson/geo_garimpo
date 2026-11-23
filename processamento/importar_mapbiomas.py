import csv
import json
import logging
import math
import re
from argparse import ArgumentParser
from datetime import date
from io import StringIO
from pathlib import Path
from urllib.parse import urlsplit
from zipfile import ZipFile

import rasterio
from psycopg.types.json import Jsonb

from db.conexao import conectar
from processamento.carga import (
    argumentos_fonte, assinatura_arquivo, bloquear_escritas, invalidar_resultados, ler_manifesto, registrar_fonte,
)
from processamento.mapbiomas_blocos import consumir_bloco, descrever_grade, pasta_de_retomada, publicar_arquivo, varrer
from processamento.mapbiomas_armazenamento import conferir_banco, estimar
from processamento.mapbiomas_grade import abrir_raster
from processamento.mapbiomas_historico_db import conferir_origem, estado_ano, grade_compativel, validar_publicacao

REGISTRO = logging.getLogger(__name__)


def registros_da_legenda(arquivo):
    if arquivo.suffix.lower() == ".zip":
        with ZipFile(arquivo) as pacote:
            nomes = [nome for nome in pacote.namelist() if nome.lower().endswith(".csv")]
            if len(nomes) != 1:
                raise ValueError("O ZIP da legenda deve conter exatamente um CSV.")
            conteudo = pacote.read(nomes[0]).decode("utf-8-sig")
    else:
        conteudo = arquivo.read_text(encoding="utf-8-sig")
    linhas = csv.DictReader(StringIO(conteudo))
    if not {"class_id", "class_name_pt"} <= set(linhas.fieldnames or ()):
        raise ValueError("O CSV da legenda deve trazer class_id e class_name_pt.")
    return list(linhas)


def classes_da_legenda(arquivo):
    classes = set()
    for linha in registros_da_legenda(arquivo):
        codigo = int(linha["class_id"])
        garimpo = " | garimpo - " in linha["class_name_pt"].casefold()
        if garimpo != (codigo // 100 in (11, 12)):
            raise ValueError(f"O código {codigo} diverge da classificação de atividade na legenda.")
        if garimpo:
            classes.add(codigo)
    return sorted(classes)


def importar(argumentos, configuracao=None):
    grade_referencia = getattr(argumentos, "grade_referencia", None)
    if grade_referencia is not None and not 1985 <= argumentos.ano <= 2024:
        raise ValueError("O carregador histórico aceita apenas 1985 a 2024.")
    if not 1985 <= argumentos.ano <= date.today().year:
        raise ValueError("Ano fora do intervalo admitido.")
    arquivo_legenda = argumentos.legenda_arquivo
    classes = classes_da_legenda(arquivo_legenda) if arquivo_legenda else sorted(set(argumentos.classes))
    if not classes or any(codigo < 0 or codigo > 2_147_483_647 for codigo in classes):
        raise ValueError("Informe classes categóricas inteiras válidas.")
    legenda = urlsplit(argumentos.legenda)
    if legenda.scheme not in {"http", "https"} or not legenda.hostname or legenda.username or legenda.password:
        raise ValueError("Informe a URL pública da legenda correspondente à coleção.")
    manifesto = ler_manifesto(argumentos.metadados)
    if argumentos.somente_preparar and argumentos.retomar_em is None:
        raise ValueError("--somente-preparar requer --retomar-em.")
    with (
        rasterio.Env(GDAL_CACHEMAX=128 * 1024 * 1024),
        abrir_raster(argumentos.arquivo, argumentos.limites_exportacao,
                     prefixo=argumentos.prefixo_exportacao,
                     partes_explicitas=getattr(argumentos, "arquivos", None)) as (raster, assinatura, inventario),
    ):
        grade = descrever_grade(raster, argumentos.banda, classes)
        nome_banda = raster.descriptions[argumentos.banda - 1]
        if (nome_banda and re.fullmatch(r"classification_\d{4}", nome_banda)
                and nome_banda != f"classification_{argumentos.ano}"):
            raise ValueError(f"A banda {nome_banda} diverge do ano solicitado.")
        grade_publica = {
            **grade, "crs": raster.crs.to_string(), "nodata": raster.nodatavals[argumentos.banda - 1],
            "bounds": list(raster.bounds), "resolucao": list(raster.res), "descricao_banda": nome_banda,
        }
        if grade_referencia is not None and (
            argumentos.retomar_em is None or not arquivo_legenda or raster.count != 1 or raster.dtypes[0] != "int16"
            or nome_banda != f"classification_{argumentos.ano}" or not grade_compativel(grade_publica, grade_referencia)
        ):
            raise ValueError("A banda, a grade, o NoData ou os parâmetros do lote histórico divergem do piloto.")
        pasta = None
        if argumentos.retomar_em is not None:
            pasta = pasta_de_retomada(
                argumentos.retomar_em, assinatura, grade, argumentos.banda, classes, bool(arquivo_legenda), inventario,
            )
            assinatura_grade, contagens = varrer(
                raster, argumentos.banda, classes, grade, pasta=pasta, fiscalizar_legenda=bool(arquivo_legenda),
            )
            classes_validas = getattr(argumentos, "classes_validas", None)
            if classes_validas is not None:
                desconhecidas = sorted(set(map(int, contagens["pixels_por_codigo"])) - classes_validas)
                if desconhecidas:
                    raise ValueError(f"A banda contém códigos fora da legenda oficial: {desconhecidas}.")
        detalhes = {
            "grade": grade_publica,
            "quantidade_arquivos_raster": sum("original" not in item for item in inventario),
            "quantidade_copias_identicas": sum("original" in item for item in inventario),
        }
        if arquivo_legenda:
            detalhes["sha256_legenda"] = assinatura_arquivo(arquivo_legenda)
        if argumentos.somente_preparar:
            registrar_relatorio(argumentos, assinatura, assinatura_grade, inventario, classes, detalhes, contagens)
            return contagens
        with conectar(escrita=True, configuracao=configuracao) as conexao:
            bloquear_escritas(conexao)
            if grade_referencia is not None:
                espaco = conferir_banco(conexao, estimar(contagens["poligonos"], contagens["bytes_wkb"]))
                REGISTRO.info("Espaço da VM antes da cópia anual: %s", espaco)
            conexao.execute("CREATE TEMP TABLE garimpo_carga (classe integer, wkb bytea) ON COMMIT DROP")
            with conexao.cursor().copy("COPY garimpo_carga (classe,wkb) FROM STDIN") as carga:
                if pasta is None:
                    assinatura_grade, contagens = varrer(
                        raster, argumentos.banda, classes, grade, emitir=carga.write_row,
                        fiscalizar_legenda=bool(arquivo_legenda),
                    )
                else:
                    for indice in range(contagens["blocos"]):
                        consumir_bloco(pasta / f"{indice:06d}.gz", emitir=carga.write_row)
            resumo = dict(detalhes, assinatura_grade=assinatura_grade, **contagens)
            validacao = publicar(conexao, argumentos, manifesto, assinatura, classes, resumo, inventario)
            if validacao is not None:
                contagens["validacao_publicacao"] = validacao
        registrar_relatorio(argumentos, assinatura, assinatura_grade, inventario, classes, detalhes, contagens)
        return contagens


def registrar_relatorio(argumentos, assinatura, assinatura_grade, inventario, classes, detalhes, contagens):
    if argumentos.relatorio is None:
        return
    arquivo = argumentos.relatorio
    arquivo.parent.mkdir(parents=True, exist_ok=True)
    dados = dict(detalhes, **contagens, ano=argumentos.ano, assinatura_grade=assinatura_grade,
                 sha256_lote=assinatura, inventario=inventario, classes_selecionadas=classes,
                 publicado=not argumentos.somente_preparar)
    publicar_arquivo(arquivo, lambda fluxo: fluxo.write(json.dumps(dados, ensure_ascii=False, indent=2).encode("utf-8")))


def publicar(conexao, argumentos, manifesto, assinatura, classes, resumo, inventario):
    ano = argumentos.ano
    historico = getattr(argumentos, "grade_referencia", None) is not None
    assinatura_grade = resumo["assinatura_grade"]
    quantidade_preparada = conexao.execute("SELECT count(*) AS total FROM garimpo_carga").fetchone()["total"]
    if quantidade_preparada != resumo["poligonos"]:
        raise ValueError("A quantidade de polígonos preparados diverge dos blocos processados.")
    outras = conexao.execute("""
        SELECT a.ano,a.classes,a.assinatura_grade,f.tipo,f.colecao,f.versao,f.metadados
        FROM anos_garimpo a JOIN fontes_dados f ON f.id = a.fonte_id
        WHERE a.ano <> %s
    """, (ano,)).fetchall()
    esperado = dict(classes=classes, assinatura_grade=assinatura_grade, colecao=manifesto["colecao"],
                    versao=manifesto["versao"], legenda=argumentos.legenda)
    if historico:
        if not any(linha["ano"] == 2025 for linha in outras):
            raise ValueError("A referência de 2025 precisa estar publicada no banco para carregar a série.")
        for linha in outras:
            conferir_origem(linha, manifesto, resumo, classes, argumentos.legenda)
        situacao, registro = estado_ano(conexao, ano)
        if registro:
            conferir_origem(registro, manifesto, resumo, classes, argumentos.legenda)
            if situacao == "validado" and registro["sha256"] == assinatura:
                REGISTRO.info("O ano %s já está validado com a mesma aquisição.", ano)
                return registro["metadados"]["validacao_publicacao"]
            if (situacao != "incompleto" or getattr(argumentos, "reiniciar_incompleto", None) != ano
                    or registro["sha256"] != assinatura):
                raise ValueError(f"O ano {ano} já existe ({situacao}). Sua publicação foi preservada.")
        atual = registro
    else:
        atual = verificar_publicado(conexao, argumentos, assinatura, manifesto, esperado, quantidade_preparada)
        if atual is True:
            return
        for linha in outras:
            comparacao = {chave: linha[chave] for chave in esperado if chave != "legenda"}
            if dict(comparacao, legenda=linha["metadados"].get("legenda")) != esperado:
                raise ValueError("Série incompatível. Use a mesma coleção, versão, legenda, classes, grade e máscara de validade.")
    atributos_fonte = {k: v for k, v in resumo.items() if k != "pixels_por_codigo"}
    fonte = registrar_fonte(conexao, "mapbiomas", manifesto, assinatura, {
        "ano": ano, "banda": argumentos.banda, "classes": classes, "legenda": argumentos.legenda,
        "abrangencia": "nacional", **atributos_fonte,
    })
    # O JSONB faz parte de uma chave B-tree. Lotes pequenos preservam todos os hashes dentro do limite da chave.
    tamanho_lote_inventario = 8
    for indice in range(0, len(inventario), tamanho_lote_inventario):
        registrar_fonte(conexao, "mapbiomas", manifesto, assinatura, {
            "papel": "inventario_raster", "ano": ano, "fonte_anual_id": fonte,
            "parte_inventario": indice // tamanho_lote_inventario + 1,
            "arquivos": inventario[indice:indice + tamanho_lote_inventario],
        })
    if not historico:
        invalidar_resultados(conexao)
    if atual:
        conexao.execute("DELETE FROM anos_garimpo WHERE ano = %s", (ano,))
    conexao.execute("""
        INSERT INTO anos_garimpo (ano,fonte_id,classes,assinatura_grade) VALUES (%s,%s,%s,%s)
    """, (ano, fonte, classes, assinatura_grade))
    inseridas = conexao.execute("""
        INSERT INTO garimpos_anuais (ano,fonte_id,classe,geom)
        SELECT %s,%s,classe,ST_GeomFromWKB(wkb,4674) FROM garimpo_carga
    """, (ano, fonte)).rowcount
    if inseridas != quantidade_preparada:
        raise ValueError("A publicação não inseriu todos os polígonos preparados.")
    validacao = validar_publicacao(conexao, ano, fonte, classes, resumo) if historico else None
    if validacao is not None:
        conexao.execute("UPDATE fontes_dados SET metadados = metadados || %s WHERE id = %s",
                        (Jsonb({"validacao_publicacao": validacao}), fonte))
    area_raster = resumo["area_raster_ha"]
    if not historico and area_raster is not None:
        area_vetor = conexao.execute("""
            SELECT coalesce(sum(ST_Area(ST_Transform(geom, 6933))) / 10000, 0) AS hectares
            FROM garimpos_anuais WHERE ano = %s
        """, (ano,)).fetchone()["hectares"]
        if not math.isclose(area_raster, area_vetor, rel_tol=1e-7, abs_tol=0.01):
            raise ValueError(f"As áreas raster e vetorial divergem ({area_raster:.6f} ha e {area_vetor:.6f} ha).")
        REGISTRO.info("Área raster %.6f ha, área vetorizada %.6f ha.", area_raster, area_vetor)
    conexao.execute("ANALYZE garimpos_anuais")
    return validacao


def verificar_publicado(conexao, argumentos, assinatura, manifesto, esperado, quantidade_preparada):
    ano = argumentos.ano
    atual = conexao.execute("""
        SELECT a.classes,a.assinatura_grade,f.colecao,f.versao,f.metadados->>'legenda' AS legenda,
               f.sha256,f.tipo,f.url,f.metadados->>'banda' AS banda
        FROM anos_garimpo a JOIN fontes_dados f ON f.id = a.fonte_id WHERE a.ano = %s
    """, (ano,)).fetchone()
    mesma_aquisicao = dict(esperado, sha256=assinatura, tipo="mapbiomas", url=manifesto["url"], banda=str(argumentos.banda))
    if atual == mesma_aquisicao:
        total = conexao.execute("SELECT count(*) AS total FROM garimpos_anuais WHERE ano = %s", (ano,)).fetchone()["total"]
        if total != quantidade_preparada:
            raise ValueError("A publicação anterior diverge da quantidade preparada.")
        REGISTRO.info("O ano %s já está completo com a mesma aquisição.", ano)
        return True
    if atual and not argumentos.substituir_ano:
        raise ValueError("O ano já existe com outra aquisição. A substituição exige --substituir-ano.")
    return atual


def analisador():
    cli = ArgumentParser(description="Importar uma banda anual nacional de garimpo do MapBiomas.")
    argumentos_fonte(cli)
    cli.add_argument("--ano", type=int, required=True)
    cli.add_argument("--banda", type=int, default=1)
    selecao = cli.add_mutually_exclusive_group(required=True)
    selecao.add_argument("--classes", nargs="+", type=int, help="Códigos da legenda selecionada.")
    selecao.add_argument("--legenda-arquivo", type=Path, help="CSV ou ZIP oficial para selecionar solo exposto e revegetação.")
    cli.add_argument("--legenda", required=True)
    cli.add_argument("--retomar-em", type=Path, help="Diretório local de recuperação dos blocos processados.")
    cli.add_argument("--prefixo-exportacao", help="Prefixo dos arquivos particionados do Earth Engine.")
    cli.add_argument("--somente-preparar", action="store_true", help="Prepara todos os blocos sem conectar ao PostgreSQL.")
    cli.add_argument("--relatorio", type=Path, help="Destino JSON da inspeção e do processamento.")
    cli.add_argument("--limites-exportacao", nargs=4, type=float, metavar=("OESTE", "SUL", "LESTE", "NORTE"),
                     help="Limites em longitude/latitude conferidos para este lote. Obrigatórios para uma pasta.")
    cli.add_argument("--substituir-ano", action="store_true", help="Autoriza trocar um ano publicado por nova aquisição.")
    return cli


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    cli = analisador()
    try:
        argumentos = cli.parse_args()
        resultado = importar(argumentos)
        print("Preparação concluída." if argumentos.somente_preparar else "Carga concluída.",
              {k: v for k, v in resultado.items() if k != "pixels_por_codigo"})
    except (ValueError, OSError) as erro:
        cli.error(str(erro))
