import json
from collections import Counter

import osmium
import pyarrow.parquet as pq
import shapely

from processamento.redes_lotes import gravar_lote

RODOVIAS = (
    "motorway", "trunk", "primary", "secondary", "tertiary", "motorway_link", "trunk_link", "primary_link",
    "secondary_link", "tertiary_link", "unclassified", "residential", "living_street", "service", "track",
)
AEROVIAS = ("aerodrome", "airstrip", "runway")
TAMANHO_OSM = 30_000


def replicacao(arquivo):
    with osmium.io.Reader(str(arquivo), osmium.osm.NOTHING) as leitor:
        cabecalho = leitor.header()
        data = cabecalho.get("osmosis_replication_timestamp")
        if not data or cabecalho.has_multiple_object_versions:
            raise ValueError("O PBF não corresponde a um extrato de estado atual com data de replicação.")
        return data


class Preparador:
    def __init__(self, pasta, mascara, tipo):
        self.pasta = pasta
        self.mascara = mascara
        self.tipo = tipo
        self.fabrica = osmium.geom.WKBFactory()
        self.numero = 0
        self.lote = []
        self.resumo = Counter()
        self.eventos = Counter()

    def receber(self, codigo, nome, classe, criar, objeto):
        try:
            geometria = criar(objeto)
        except (osmium.InvalidLocationError, RuntimeError):
            self.eventos["sem_geometria_osm"] += 1
            return
        self.lote.append((codigo, nome, classe, geometria))
        if len(self.lote) >= TAMANHO_OSM:
            self.salvar()

    def salvar(self):
        if not self.lote:
            return
        destino = self.pasta / f"{self.numero:06d}.parquet"
        if not destino.is_file():
            codigos, nomes, classes, geometrias = zip(*self.lote)
            gravar_lote(self.pasta, self.numero, codigos, nomes, classes, shapely.from_wkb(geometrias),
                        "EPSG:4326", self.mascara, self.tipo, {})
        contagens = json.loads(pq.read_schema(destino).metadata[b"contagens"])
        if contagens["lidas"] != len(self.lote):
            raise ValueError("Lote OSM existente diverge da quantidade de objetos relidos.")
        self.resumo.update(contagens)
        if self.numero % 10 == 0:
            print(f"{self.pasta.name}: {self.resumo['lidas']:,} feições OSM conferidas.", flush=True)
        self.lote.clear()
        self.numero += 1

    def concluir(self, classes, chave):
        self.salvar()
        return dict(self.resumo + self.eventos, classes=list(classes), campo_classe=chave, src_origem="EPSG:4326",
                    identificador="osm:n<id>, osm:w<id>, osm:r<id>")


def preparar_osm(arquivo, raiz, mascara, versao, *, somente_aerovias=False):
    data = replicacao(arquivo)
    if data != versao:
        raise ValueError(f"A versão do manifesto deve coincidir com a data de replicação do PBF ({data}).")
    estradas = None if somente_aerovias else Preparador(raiz / "estradas", mascara, "LineString")
    pasta_pistas = raiz / "pistas_pouso" / "categorias-2"
    pasta_pistas.mkdir(parents=True, exist_ok=True)
    pistas = Preparador(pasta_pistas, mascara, "pista")
    pares_aerovias = tuple(("aeroway", classe) for classe in AEROVIAS) + (("area:aeroway", "runway"),)
    pares_rodovias = tuple(("highway", classe) for classe in RODOVIAS) if estradas else ()
    filtro_areas = osmium.filter.TagFilter(*pares_aerovias)
    indices = osmium.index.map_types()
    indice = "sparse_file_array" if "sparse_file_array" in indices else "sparse_mem_array"
    processador = (osmium.FileProcessor(str(arquivo)).with_locations(indice)
                   .with_areas(filtro_areas).with_filter(osmium.filter.TagFilter(*pares_rodovias, *pares_aerovias)))
    print(f"Lendo extrato OSM {data}, com índice de nós {indice}.", flush=True)
    for objeto in processador:
        tags = objeto.tags
        nome = tags.get("name") or tags.get("ref")
        pista_em_area = tags.get("area:aeroway") == "runway"
        aerovia = "runway" if pista_em_area else tags.get("aeroway")
        if objeto.is_way():
            codigo = f"osm:w{objeto.id}"
            rodovia = tags.get("highway")
            if estradas and rodovia in RODOVIAS and tags.get("area") != "yes":
                estradas.receber(codigo, nome, rodovia, estradas.fabrica.create_linestring, objeto)
            if aerovia in AEROVIAS:
                pistas.receber(codigo, nome, aerovia, pistas.fabrica.create_linestring, objeto)
        elif objeto.is_area() and aerovia in AEROVIAS:
            via = objeto.from_way()
            if via and (tags.get("area") == "no" or (aerovia == "runway" and not pista_em_area and tags.get("area") != "yes")):
                continue
            codigo = f"osm:{'w' if via else 'r'}{objeto.orig_id()}"
            pistas.receber(codigo, nome, aerovia, pistas.fabrica.create_multipolygon, objeto)
        elif objeto.is_node() and aerovia in ("aerodrome", "airstrip"):
            pistas.receber(f"osm:n{objeto.id}", nome, aerovia, pistas.fabrica.create_point, objeto)
        elif objeto.is_relation() and aerovia in AEROVIAS:
            pistas.eventos["relacoes_osm_lidas"] += 1
    detalhes = {"pistas_pouso": pistas.concluir(AEROVIAS, "aeroway e area:aeroway")}
    if estradas:
        detalhes["estradas"] = estradas.concluir(RODOVIAS, "highway")
    return detalhes
