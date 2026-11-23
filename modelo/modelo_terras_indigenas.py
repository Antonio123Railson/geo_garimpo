from modelo.espacial import consultar_camadas


def geometrias(conexao, janela):
    return consultar_camadas(conexao, "terras_indigenas", ("codigo", "nome", "situacao", "fonte_id"), janela)


def catalogo(conexao, limite, apos):
    return conexao.execute("""
        SELECT id, codigo, nome, situacao,
               ARRAY[ST_XMin(geom), ST_YMin(geom), ST_XMax(geom), ST_YMax(geom)] AS bbox
        FROM terras_indigenas WHERE id > %s ORDER BY id LIMIT %s
    """, (apos, limite + 1)).fetchall()


def obter(conexao, terra_id):
    return conexao.execute("SELECT id, codigo, nome FROM terras_indigenas WHERE id = %s", (terra_id,)).fetchone()


def tem_uf(conexao, uf):
    return conexao.execute("SELECT 1 FROM unidades_federativas WHERE sigla = %s", (uf,)).fetchone() is not None
