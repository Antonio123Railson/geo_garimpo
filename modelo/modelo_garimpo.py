from modelo.espacial import consultar_camadas


def anos(conexao):
    return conexao.execute("""
        SELECT a.ano, a.classes, f.id AS fonte_id, f.nome, f.url, f.colecao, f.versao, f.licenca, f.adquirido_em
        FROM anos_garimpo a JOIN fontes_dados f ON f.id = a.fonte_id
        ORDER BY a.ano
    """).fetchall()


def classes_no_ano(conexao, ano):
    linha = conexao.execute("SELECT classes FROM anos_garimpo WHERE ano = %s", (ano,)).fetchone()
    return None if linha is None else linha["classes"]


def tem_anos(conexao):
    return conexao.execute("SELECT EXISTS (SELECT 1 FROM anos_garimpo) AS existe").fetchone()["existe"]


def impedimento_temporal(conexao, ano):
    return conexao.execute("""
        SELECT CASE WHEN anterior.ano IS NULL THEN 'sem_ano_anterior'
                    WHEN atual.assinatura_grade <> anterior.assinatura_grade THEN 'mascaras_incompativeis'
               END AS impedimento
        FROM anos_garimpo atual LEFT JOIN anos_garimpo anterior ON anterior.ano = atual.ano - 1
        WHERE atual.ano = %s
    """, (ano,)).fetchone()["impedimento"]


def geometrias(conexao, janela, ano, classe):
    return consultar_camadas(conexao, "garimpos_anuais", ("ano", "classe", "fonte_id"), janela, ano=ano, classe=classe)
