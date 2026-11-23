from psycopg import sql


def parametros_janela(janela):
    oeste, sul, leste, norte = janela["caixa"]
    return dict(janela, oeste=oeste, sul=sul, leste=leste, norte=norte, limite=janela["limite"] + 1)


def consultar_camadas(conexao, tabela, campos, janela, *, ano=None, classe=None):
    parametros = parametros_janela(janela)
    parametros.update(ano=ano, classe=classe, tolerancia=0.05 / (2 ** max(0, janela["zoom"] - 4)))
    redes = tabela in {"rios", "estradas", "pistas_pouso"}
    oeste, sul, leste, norte = janela["caixa"]
    caixa_pequena = (leste - oeste) * (norte - sul) <= 0.5
    uf_ampla = tabela == "garimpos_anuais" and janela["uf"] and not janela["terra"] and not caixa_pequena
    filtros = []
    if not redes:
        filtros.append(sql.SQL("d.id > %(apos)s"))
    if tabela == "garimpos_anuais":
        filtros.append(sql.SQL("d.ano = %(ano)s"))
        if classe is not None:
            filtros.append(sql.SQL("d.classe = %(classe)s"))
    if tabela == "terras_indigenas" and janela["terra"]:
        filtros.append(sql.SQL("d.id = %(terra)s"))

    recortes = []
    if janela["terra"] and tabela != "terras_indigenas":
        recortes.append(sql.SQL("(SELECT geom FROM terras_indigenas WHERE id = %(terra)s)"))
    if janela["uf"]:
        recortes.append(sql.SQL("(SELECT geom FROM unidades_federativas WHERE sigla = %(uf)s)"))

    caixa = sql.SQL("ST_Transform(ST_MakeEnvelope(%(oeste)s, %(sul)s, %(leste)s, %(norte)s, 4326), 4674)")
    recorte = sql.SQL("j.geom")
    for territorio in recortes:
        recorte = sql.SQL("ST_Intersection({}, {})").format(recorte, territorio)
    fonte_recorte = fonte_selecao = sql.SQL("")
    if recortes:
        fonte_recorte = sql.SQL("CROSS JOIN recorte r")
        if uf_ampla:
            # A seleção por GiST de uma UF ampla pode enumerar e ordenar
            # milhares de feições antes do LIMIT. A paginação pode usar (ano, id).
            filtros.append(sql.SQL("""
                CASE WHEN d.geom && (SELECT geom FROM recorte)
                     THEN ST_Intersects((SELECT geom FROM recorte), d.geom) ELSE false END
            """))
        else:
            fonte_selecao = fonte_recorte
            filtros.append(sql.SQL("ST_Intersects(r.geom, d.geom)"))
        geometria = sql.SQL("CASE WHEN ST_Covers(r.geom, s.geom) THEN s.geom ELSE ST_Intersection(s.geom, r.geom) END")
    else:
        filtros.append(sql.SQL("CASE WHEN d.geom @ j.geom THEN true ELSE ST_Intersects(d.geom, j.geom) END"))
        geometria = sql.SQL("CASE WHEN s.geom @ j.geom THEN s.geom ELSE ST_Intersection(s.geom, j.geom) END")

    ordem = sql.SQL("d.id")
    if tabela == "garimpos_anuais" and caixa_pequena:
        # Em recortes pequenos, ordenar candidatos espaciais evita a leitura de
        # milhares de IDs fora da janela pelo índice (ano, id).
        ordem = sql.SQL("d.id + 0")
    if redes:
        ordem = sql.SQL("""d.geom <-> ST_Centroid(j.geom)""")
    consulta = sql.SQL("""
        WITH janela AS NOT MATERIALIZED (
            SELECT {caixa} AS geom
        ), recorte AS MATERIALIZED (
            SELECT {recorte} AS geom FROM janela j
        ), selecionadas AS MATERIALIZED (
            SELECT d.id, {campos}, d.geom, {ordem} AS ordem
            FROM {tabela} d CROSS JOIN janela j {fonte_selecao}
            WHERE d.geom && j.geom AND {filtros}
            ORDER BY ordem, d.id LIMIT %(limite)s {deslocamento}
        ), formas AS MATERIALIZED (
            SELECT s.id, {campos}, s.ordem, ST_SimplifyPreserveTopology(
                ST_CollectionExtract({geometria}, ST_Dimension(s.geom) + 1), %(tolerancia)s
            ) AS geom
            FROM selecionadas s CROSS JOIN janela j {fonte_recorte}
        )
        SELECT id, {campos}, CASE WHEN ST_NPoints(geom) BETWEEN 1 AND 2000 THEN
            ST_AsGeoJSON(ST_ForcePolygonCCW(ST_Transform(geom, 4326)), 9)::json
        END AS geometria
        FROM formas ORDER BY ordem, id
    """).format(
        caixa=caixa, recorte=recorte, tabela=sql.Identifier(tabela),
        campos=sql.SQL(", ").join(map(sql.Identifier, campos)),
        fonte_recorte=fonte_recorte, fonte_selecao=fonte_selecao,
        filtros=sql.SQL(" AND ").join(filtros),
        ordem=ordem, deslocamento=sql.SQL("OFFSET %(apos)s") if redes else sql.SQL(""),
        geometria=geometria,
    )
    return conexao.execute(consulta, parametros).fetchall()
