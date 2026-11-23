from argparse import ArgumentParser
from functools import lru_cache

from pyproj import CRS, Transformer
from shapely import force_2d, make_valid, transform
from shapely.geometry import MultiLineString, MultiPoint, MultiPolygon
from shapely.ops import unary_union

from db.conexao import conectar


@lru_cache(maxsize=64)
def transformador(origem, destino):
    return Transformer.from_crs(origem, destino, always_xy=True)


def reprojetar(geometria, origem, destino="EPSG:4674"):
    return transform(geometria, transformador(origem, destino).transform, interleaved=False)


def componentes(geometria, tipos):
    if geometria.is_empty:
        return
    if geometria.geom_type in tipos:
        yield geometria
    elif hasattr(geometria, "geoms"):
        for parte in geometria.geoms:
            yield from componentes(parte, tipos)


def normalizar(geometria, tipo, ocorrencias=None):
    if geometria is None or geometria.is_empty:
        raise ValueError("Geometria vazia ou sem coordenadas.")
    geometria = force_2d(geometria)
    if not geometria.is_valid:
        geometria = make_valid(geometria)
        if ocorrencias is not None:
            ocorrencias["reparadas"] += 1
    if tipo == "pista":
        dimensoes = ("Polygon", "LineString", "Point")
        tipo = next((forma for forma in dimensoes if any(componentes(geometria, (forma,)))), "Point")
    partes = list(componentes(geometria, (tipo,)))
    if not partes:
        raise ValueError(f"Não há componentes válidos do tipo {tipo}.")
    if tipo == "Polygon":
        geometria = MultiPolygon(list(componentes(unary_union(partes), (tipo,))))
    elif tipo == "LineString":
        geometria = MultiLineString(partes)
    else:
        geometria = MultiPoint(partes)
    if geometria.is_empty or not geometria.is_valid:
        raise ValueError("A geometria permaneceu inválida após o reparo.")
    return geometria


def validar_limites(geometria):
    oeste, sul, leste, norte = geometria.bounds
    if not (-180 <= oeste <= leste <= 180 and -90 <= sul <= norte <= 90):
        raise ValueError("Coordenadas fora dos limites geográficos após a transformação.")


def area_hectares(geometria, origem="EPSG:4674"):
    if not CRS.from_user_input(origem).equals(CRS.from_epsg(6933)):
        geometria = reprojetar(geometria, origem, "EPSG:6933")
    return geometria.area / 10_000


def auditar():
    tabelas = ("terras_indigenas", "unidades_federativas", "garimpos_anuais", "rios",
               "estradas", "pistas_pouso", "manchas_indigenas")
    from psycopg import sql

    problemas = 0
    with conectar(escrita=True) as conexao:
        conexao.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY")
        for tabela in tabelas:
            linha = conexao.execute(sql.SQL("""
                SELECT count(*) AS total, count(*) FILTER (
                    WHERE ST_SRID(geom) <> 4674 OR ST_IsEmpty(geom) OR NOT ST_IsValid(geom)
                ) AS inconsistentes FROM {}
            """).format(sql.Identifier(tabela))).fetchone()
            print(f"{tabela} — {linha['total']} geometrias, {linha['inconsistentes']} inconsistentes.")
            problemas += linha["inconsistentes"]
        anos = [linha["ano"] for linha in conexao.execute("SELECT ano FROM anos_garimpo ORDER BY ano")]
        lacunas = sorted(set(range(1985, anos[-1] + 1)) - set(anos)) if anos else []
        print(f"Anos carregados: {anos or 'nenhum'}. Lacunas até o último ano: {lacunas or 'nenhuma identificada'}.")
        print("Bases ativas:", ", ".join(linha["camada"] for linha in conexao.execute(
            "SELECT camada FROM bases_ativas ORDER BY camada"
        )) or "nenhuma")
        consistencia = conexao.execute("""
            SELECT count(*) AS inconsistencias FROM estatisticas_anuais atual
            JOIN anos_garimpo grade_atual ON grade_atual.ano = atual.ano
            LEFT JOIN anos_garimpo grade_anterior ON grade_anterior.ano = atual.ano - 1
            LEFT JOIN estatisticas_anuais anterior ON anterior.ano = atual.ano - 1
                AND anterior.escopo = atual.escopo AND anterior.terra_id IS NOT DISTINCT FROM atual.terra_id
                AND anterior.uf IS NOT DISTINCT FROM atual.uf
            WHERE (atual.area_nova_ha IS NOT NULL) <> (anterior.id IS NOT NULL AND
                  coalesce(grade_atual.assinatura_grade = grade_anterior.assinatura_grade, false))
                OR atual.area_nova_ha > atual.area_detectada_ha + 0.001
                OR abs(atual.variacao_liquida_ha - (atual.area_detectada_ha - anterior.area_detectada_ha)) > 0.001
        """).fetchone()
        print(f"Linhas estatísticas temporalmente inconsistentes: {consistencia['inconsistencias']}.")
        problemas += consistencia["inconsistencias"]
    return problemas


if __name__ == "__main__":
    ArgumentParser(description="Auditar integridade geométrica e temporal da base.").parse_args()
    raise SystemExit(1 if auditar() else 0)
