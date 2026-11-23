from argparse import ArgumentParser

from psycopg import sql
from psycopg.types.json import Jsonb

from db.conexao import conectar
from processamento.carga import bloquear_escritas, invalidar_resultados

ALVOS = {"rios": "rios", "estradas": "estradas", "pistas": "pistas_pouso"}


def calcular(configuracao=None):
    contagens = {}
    with conectar(escrita=True, configuracao=configuracao) as conexao:
        bloquear_escritas(conexao)
        anterior = conexao.execute("""
            SELECT fontes FROM execucoes_analise WHERE analise = 'estatisticas'
        """).fetchone()
        if not anterior:
            raise ValueError("Calcule as estatísticas e manchas antes de calcular proximidades.")
        bases = {linha["camada"]: linha["fonte_id"] for linha in conexao.execute(
            "SELECT camada,fonte_id FROM bases_ativas WHERE camada = ANY(%s)", (list(ALVOS.values()),)
        )}
        if not bases:
            raise ValueError("Importe pelo menos uma camada de rios, estradas ou pistas.")
        invalidar_resultados(conexao, apenas_proximidades=True)
        for alvo, tabela in ALVOS.items():
            if tabela not in bases:
                continue
            pista = sql.SQL("AND r.classe = 'runway'") if alvo == "pistas" else sql.SQL("")
            consulta = sql.SQL("""
                INSERT INTO analises_proximidade (mancha_id,alvo,feicao_id,fonte_id,distancia_m)
                SELECT m.id,%s,exata.id,exata.fonte_id,exata.distancia
                FROM manchas_indigenas m
                CROSS JOIN LATERAL (
                    SELECT ST_Distance(r.geom::geography,m.geom::geography) AS limite
                    FROM {tabela} r WHERE true {pista}
                    ORDER BY r.geom::geography <-> m.geom::geography LIMIT 1
                ) candidata
                CROSS JOIN LATERAL (
                    SELECT r.id,r.fonte_id,ST_Distance(r.geom::geography,m.geom::geography) AS distancia
                    FROM {tabela} r WHERE ST_DWithin(r.geom::geography,m.geom::geography,candidata.limite + 0.01)
                    {pista}
                    ORDER BY distancia,r.id LIMIT 1
                ) exata
            """).format(tabela=sql.Identifier(tabela), pista=pista)
            contagens[alvo] = conexao.execute(consulta, (alvo,)).rowcount
        fontes = dict(anterior["fontes"])
        fontes["bases"] = dict(fontes["bases"], **bases)
        conexao.execute("INSERT INTO execucoes_analise (analise,fontes) VALUES ('proximidades',%s)", (Jsonb(fontes),))
        conexao.execute("ANALYZE analises_proximidade")
    return contagens


if __name__ == "__main__":
    cli = ArgumentParser(description="Materializar offline a distância mínima às redes disponíveis.")
    cli.parse_args()
    try:
        print("Proximidades calculadas.", calcular())
    except ValueError as erro:
        cli.error(str(erro))
