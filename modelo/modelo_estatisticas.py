from db.conexao import RAIZ
from modelo.espacial import parametros_janela

CONSULTAS = RAIZ / "db" / "consultas"
EVOLUCAO = (CONSULTAS / "evolucao_anual.sql").read_text(encoding="utf-8")
EXPANSAO = (CONSULTAS / "expansao_garimpo.sql").read_text(encoding="utf-8")
PROXIMIDADE = (CONSULTAS / "proximidade_rios.sql").read_text(encoding="utf-8")


def evolucao(conexao, **parametros):
    return conexao.execute(EVOLUCAO, parametros).fetchall()


def ranking(conexao, ano, escopo, limite):
    return conexao.execute("""
        SELECT e.terra_id, e.uf, coalesce(t.nome, u.nome) AS nome, e.area_detectada_ha,
               e.area_detectada_ha / 100 AS area_detectada_km2
        FROM estatisticas_anuais e
        LEFT JOIN terras_indigenas t ON t.id = e.terra_id
        LEFT JOIN unidades_federativas u ON u.sigla = e.uf
        WHERE e.ano = %s AND e.escopo = %s AND e.area_detectada_ha > 0
        ORDER BY e.area_detectada_ha DESC, e.id LIMIT %s
    """, (ano, escopo, limite)).fetchall()


def execucao(conexao, analise):
    return conexao.execute(
        "SELECT calculado_em, fontes FROM execucoes_analise WHERE analise = %s", (analise,)
    ).fetchone()


def expansao(conexao, janela, ano):
    return conexao.execute(EXPANSAO, dict(parametros_janela(janela), ano=ano)).fetchall()


def proximidade(conexao, janela, **parametros):
    return conexao.execute(PROXIMIDADE, dict(parametros_janela(janela), **parametros)).fetchall()
