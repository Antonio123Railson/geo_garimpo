import json
from argparse import ArgumentParser
from collections import Counter
from pathlib import Path

import pyarrow.parquet as pq
import pyogrio
import shapely

from processamento.carga import assinatura_arquivo, ler_manifesto
from processamento.redes_lotes import (
    VERSAO_PROCESSAMENTO, gravar_json, gravar_lote, mascara_nacional, publicar, verificar_publicacao,
)

CAMADA_BHO = "geoft_bho_trecho_drenagem"
TAMANHO_BHO = 50_000


def preparar_bho(arquivo, pasta, mascara):
    info = pyogrio.read_info(arquivo, layer=CAMADA_BHO)
    if (info["geometry_type"], info["crs"], info["fid_column"]) != ("MultiLineString", "EPSG:4674", "fid"):
        raise ValueError("A camada da BHO6 precisa informar MultiLineString, EPSG:4674 e identificador fid.")
    resumo = Counter()
    with pyogrio.open_arrow(arquivo, layer=CAMADA_BHO, columns=["drn_pk", "nooriginal"],
                            return_fids=True, use_pyarrow=True, batch_size=TAMANHO_BHO) as (meta, leitor):
        for numero, lote in enumerate(leitor):
            destino = pasta / f"{numero:06d}.parquet"
            if not destino.is_file():
                drn = lote["drn_pk"].to_pylist()
                ids = lote["fid"].to_pylist()
                codigos = [str(codigo) if codigo is not None else f"fid:{fid}" for codigo, fid in zip(drn, ids)]
                nomes = [nome.strip() or None if nome else None for nome in lote["nooriginal"].to_pylist()]
                geometrias = shapely.from_wkb(lote[meta["geometry_name"]].to_pylist())
                gravar_lote(pasta, numero, codigos, nomes, [None] * lote.num_rows, geometrias,
                            meta["crs"], mascara, "LineString", {"identificadores_fid": drn.count(None)})
            resumo.update(json.loads(pq.read_schema(destino).metadata[b"contagens"]))
            if numero % 10 == 0:
                print(f"BHO6: {resumo['lidas']:,} trechos conferidos, {resumo['preparadas']:,} no Brasil.", flush=True)
    if resumo["lidas"] != info["features"]:
        raise ValueError("A leitura da BHO6 não abrangeu a quantidade de feições declarada pelo GeoPackage.")
    return dict(resumo, camada=CAMADA_BHO, campo_codigo="drn_pk", campo_nome="nooriginal",
                src_origem=info["crs"])


def executar(argumentos):
    manifesto = ler_manifesto(argumentos.metadados)
    if argumentos.classes_estradas and argumentos.fonte != "osm":
        raise ValueError("O filtro de classes de estradas aplica-se apenas à fonte OSM.")
    tamanho = manifesto["metadados"].get("bytes_arquivo")
    if tamanho is not None and argumentos.arquivo.stat().st_size != tamanho:
        raise ValueError("O tamanho do arquivo diverge do manifesto da aquisição.")
    print(f"Calculando SHA-256 de {argumentos.arquivo.name}.", flush=True)
    assinatura = assinatura_arquivo(argumentos.arquivo)
    tabelas = ("rios",) if argumentos.fonte == "ana" else ("pistas_pouso", "estradas")
    ativas = {tabela: verificar_publicacao(tabela, assinatura) for tabela in tabelas}
    mascara, assinatura_recorte = mascara_nacional()
    raiz = argumentos.retomar_em / f"v{VERSAO_PROCESSAMENTO}-{assinatura}-{assinatura_recorte}"
    for tabela in tabelas:
        (raiz / tabela).mkdir(parents=True, exist_ok=True)
    conclusao = raiz / "preparacao.json"
    if conclusao.is_file():
        detalhes = json.loads(conclusao.read_text(encoding="utf-8"))
    elif all(ativas.values()):
        detalhes = {tabela: ativa["metadados"] for tabela, ativa in ativas.items()}
    else:
        if argumentos.fonte == "ana":
            detalhes = {"rios": preparar_bho(argumentos.arquivo, raiz / "rios", mascara)}
        else:
            from processamento.redes_osm import preparar_osm

            detalhes = preparar_osm(argumentos.arquivo, raiz, mascara, manifesto["versao"])
    if argumentos.fonte == "osm" and not ativas["pistas_pouso"] and "airstrip" not in detalhes["pistas_pouso"]["classes"]:
        from processamento.redes_osm import preparar_osm

        detalhes.update(preparar_osm(argumentos.arquivo, raiz, mascara, manifesto["versao"], somente_aerovias=True))
    for camada in detalhes.values():
        camada.update(assinatura_recorte=assinatura_recorte, abrangencia="nacional",
                      recorte="Interseção com a malha das 27 UFs; feições que cruzam a fronteira são preservadas.",
                      versao_processamento=VERSAO_PROCESSAMENTO)
    if not all(ativas.values()):
        gravar_json(conclusao, detalhes)
    if argumentos.somente_preparar:
        return detalhes
    for tabela in tabelas:
        pasta = raiz / tabela
        if tabela == "pistas_pouso":
            pasta = pasta / "categorias-2"
        camada = detalhes[tabela]
        if tabela == "estradas" and argumentos.classes_estradas:
            classes = list(dict.fromkeys(argumentos.classes_estradas))
            classes_extraidas = camada.get("classes_extraidas", camada["classes"])
            if set(classes) - set(classes_extraidas):
                raise ValueError("A seleção de estradas contém categorias ausentes na extração.")
            camada = dict(camada, classes_extraidas=classes_extraidas, classes=classes)
        publicar(tabela, manifesto, assinatura, camada, pasta)
    return detalhes


def analisador():
    cli = ArgumentParser(description="Preparar e carregar as redes nacionais da ANA e do OpenStreetMap.")
    cli.add_argument("fonte", choices=("ana", "osm"))
    cli.add_argument("arquivo", type=Path, help="GeoPackage BHO6 ou extrato nacional OSM em PBF.")
    cli.add_argument("--metadados", required=True, type=Path)
    cli.add_argument("--retomar-em", type=Path, default=Path("dados/processados/redes_nacionais"))
    cli.add_argument("--somente-preparar", action="store_true")
    cli.add_argument("--classes-estradas", nargs="+", help="Restringe as classes OSM publicadas; mantém a extração local.")
    return cli


if __name__ == "__main__":
    cli = analisador()
    try:
        executar(cli.parse_args())
    except (ValueError, OSError) as erro:
        cli.error(str(erro))
