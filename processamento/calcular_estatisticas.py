from argparse import ArgumentParser

from psycopg.types.json import Jsonb

from db.conexao import RAIZ, conectar
from processamento.carga import bloquear_escritas, invalidar_resultados

CONSULTAS = RAIZ / "db" / "consultas"


def executar_sql(conexao, nome, parametros):
    return conexao.execute((CONSULTAS / f"{nome}.sql").read_text(encoding="utf-8"), parametros)


def calcular(configuracao=None):
    with conectar(escrita=True, configuracao=configuracao) as conexao:
        bloquear_escritas(conexao)
        bases = {linha["camada"]: linha["fonte_id"] for linha in conexao.execute(
            "SELECT camada,fonte_id FROM bases_ativas WHERE camada IN ('terras_indigenas','ufs')"
        )}
        catalogo = conexao.execute("SELECT ano,fonte_id,assinatura_grade FROM anos_garimpo ORDER BY ano").fetchall()
        anos = {linha["ano"]: linha["fonte_id"] for linha in catalogo}
        assinaturas = {linha["ano"]: linha["assinatura_grade"] for linha in catalogo}
        if "terras_indigenas" not in bases or not anos:
            raise ValueError("Importe a base nacional da FUNAI e pelo menos um ano do MapBiomas.")
        invalidar_resultados(conexao)
        conexao.execute("""
            CREATE TEMP TABLE partes_terra ON COMMIT DROP AS
            SELECT t.id, parte.geom FROM terras_indigenas t
            CROSS JOIN LATERAL ST_Subdivide(t.geom,512) AS parte(geom)
        """)
        conexao.execute("CREATE INDEX ON partes_terra USING gist (geom)")
        conexao.execute("ANALYZE partes_terra")
        conexao.execute("""
            CREATE TEMP TABLE partes_uf ON COMMIT DROP AS
            SELECT u.sigla, parte.geom FROM unidades_federativas u
            CROSS JOIN LATERAL ST_Subdivide(u.geom,512) AS parte(geom)
        """)
        conexao.execute("CREATE INDEX ON partes_uf USING gist (geom)")
        conexao.execute("ANALYZE partes_uf")
        conexao.execute("CREATE TEMP TABLE grade_anterior (i integer,j integer,geom geometry) ON COMMIT DROP")
        for ano, fonte in anos.items():
            comparavel = assinaturas.get(ano - 1) == assinaturas[ano]
            executar_sql(conexao, "calcular_grade", (ano,))
            conexao.execute("CREATE UNIQUE INDEX ON grade_atual (i,j)")
            conexao.execute("ANALYZE grade_atual")
            executar_sql(conexao, "calcular_recortes", (comparavel,))
            metadados = Jsonb(dict(bases=bases, mapbiomas_ano=fonte, mapbiomas_anterior=anos.get(ano - 1),
                                  mascaras_comparaveis=comparavel))
            executar_sql(conexao, "materializar_estatisticas", dict(ano=ano, fontes=metadados, comparavel=comparavel))
            executar_sql(conexao, "materializar_manchas", (ano,))
            conexao.execute("DROP TABLE grade_anterior")
            conexao.execute("ALTER TABLE grade_atual RENAME TO grade_anterior")
            conexao.execute("DROP TABLE recortes_atual")
        conexao.execute("""
            INSERT INTO execucoes_analise (analise,fontes) VALUES ('estatisticas',%s)
        """, (Jsonb(dict(bases=bases, mapbiomas=anos)),))
        conexao.execute("ANALYZE estatisticas_anuais")
        conexao.execute("ANALYZE manchas_indigenas")
    return list(anos)


if __name__ == "__main__":
    cli = ArgumentParser(description="Recalcular offline as estatísticas e manchas nacionais.")
    cli.parse_args()
    try:
        print("Anos processados:", calcular())
    except ValueError as erro:
        cli.error(str(erro))
