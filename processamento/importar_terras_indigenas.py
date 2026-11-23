import logging
from argparse import ArgumentParser

from psycopg import sql

from controle.validacao import UFS
from db.conexao import conectar
from processamento.carga import (
    argumentos_vetor, assinatura_arquivo, ativar_base, bloquear_escritas, copiar_vetores,
    invalidar_resultados, ler_manifesto, registrar_fonte,
)

REGISTRO = logging.getLogger(__name__)


def validar_ufs_publicadas(conexao, fonte):
    resultado = conexao.execute("""
        SELECT array_agg(sigla ORDER BY sigla) AS siglas,
               bool_and(fonte_id = %s AND ST_SRID(geom) = 4674
                        AND GeometryType(geom) = 'MULTIPOLYGON'
                        AND ST_IsValid(geom) AND NOT ST_IsEmpty(geom)) AS validas
        FROM unidades_federativas
    """, (fonte,)).fetchone()
    if resultado["siglas"] != sorted(UFS) or not resultado["validas"]:
        raise ValueError("A publicação das 27 UFs da mesma fonte não pôde ser validada.")


def validar_terras_publicadas(conexao, fonte):
    resultado = conexao.execute("""
        WITH origem AS (
            SELECT codigo, min(nome) AS nome, min(atributo) AS situacao
            FROM vetores_carga GROUP BY codigo
        )
        SELECT count(*) FILTER (WHERE t.codigo IS NOT NULL) AS publicadas,
               count(*) FILTER (WHERE t.codigo IS NULL OR o.codigo IS NULL
                   OR t.nome IS DISTINCT FROM o.nome OR t.situacao IS DISTINCT FROM o.situacao
                   OR t.fonte_id <> %s OR ST_SRID(t.geom) <> 4674 OR ST_NDims(t.geom) <> 2
                   OR GeometryType(t.geom) <> 'MULTIPOLYGON' OR ST_IsEmpty(t.geom)
                   OR NOT ST_IsValid(t.geom)) AS divergencias
        FROM origem o FULL JOIN terras_indigenas t USING (codigo)
    """, (fonte,)).fetchone()
    if not resultado["publicadas"] or resultado["divergencias"]:
        raise ValueError("As terras publicadas divergem do arquivo de origem ou da fonte registrada.")
    return resultado["publicadas"]


def importar(argumentos, configuracao=None):
    manifesto = ler_manifesto(argumentos.metadados)
    assinatura = assinatura_arquivo(argumentos.arquivo)
    eh_uf = argumentos.tipo == "uf"
    camada = "ufs" if eh_uf else "terras_indigenas"
    tabela = "unidades_federativas" if eh_uf else "terras_indigenas"
    somente_inicial = argumentos.somente_inicial
    with conectar(escrita=True, configuracao=configuracao) as conexao:
        bloquear_escritas(conexao)
        if somente_inicial:
            conexao.execute(sql.SQL("""
                LOCK TABLE {}, bases_ativas, estatisticas_anuais, manchas_indigenas,
                    analises_proximidade, execucoes_analise IN SHARE ROW EXCLUSIVE MODE
            """).format(sql.Identifier(tabela)))
        ocorrencias = copiar_vetores(conexao, argumentos, tipo_geom="Polygon", campo_atributo=argumentos.campo_situacao)
        REGISTRO.info("%s feições convertidas na tabela temporária.", ocorrencias["carregadas"])
        conexao.execute("UPDATE vetores_carga SET nome = codigo WHERE nome IS NULL AND %s", (eh_uf,))
        if conexao.execute("""
            SELECT codigo FROM vetores_carga GROUP BY codigo
            HAVING count(DISTINCT nome) <> 1 OR count(*) FILTER (WHERE nome IS NULL) > 0
                   OR count(DISTINCT atributo) > 1
            LIMIT 1
        """).fetchone():
            raise ValueError("Há códigos com nome ausente ou atributos conflitantes no arquivo.")
        if eh_uf:
            siglas = {linha["codigo"] for linha in conexao.execute("SELECT DISTINCT codigo FROM vetores_carga")}
            if siglas != set(UFS):
                raise ValueError("A malha do IBGE precisa conter exatamente as 27 siglas de UF.")
        detalhes = {
            "abrangencia": "nacional", "camada": argumentos.camada, "campo_codigo": argumentos.campo_codigo,
            "campo_nome": argumentos.campo_nome, "campo_situacao": argumentos.campo_situacao, **ocorrencias,
        }
        if argumentos.codificacao:
            detalhes["codificacao"] = argumentos.codificacao
        fonte = registrar_fonte(conexao, "ibge" if eh_uf else "funai", manifesto, assinatura, detalhes)
        ativa = conexao.execute("SELECT fonte_id FROM bases_ativas WHERE camada = %s", (camada,)).fetchone()
        if ativa and ativa["fonte_id"] == fonte:
            if somente_inicial and eh_uf:
                validar_ufs_publicadas(conexao, fonte)
            elif somente_inicial:
                ocorrencias["publicadas"] = validar_terras_publicadas(conexao, fonte)
            REGISTRO.info("A mesma aquisição já está ativa.")
            return ocorrencias
        if somente_inicial:
            ocupada = conexao.execute(sql.SQL("""
                SELECT EXISTS (SELECT 1 FROM {})
                    OR EXISTS (SELECT 1 FROM estatisticas_anuais)
                    OR EXISTS (SELECT 1 FROM manchas_indigenas)
                    OR EXISTS (SELECT 1 FROM analises_proximidade)
                    OR EXISTS (SELECT 1 FROM execucoes_analise) AS ocupada
            """).format(sql.Identifier(tabela))).fetchone()["ocupada"]
            if ativa or ocupada:
                raise ValueError("A carga inicial encontrou territórios ou análises existentes.")
        else:
            invalidar_resultados(conexao)
        conexao.execute("""
            CREATE TEMP TABLE territorios_carga ON COMMIT DROP AS
            SELECT codigo, min(nome) AS nome, min(atributo) AS situacao,
                   ST_Multi(ST_CollectionExtract(ST_UnaryUnion(ST_Collect(ST_GeomFromWKB(wkb,4674))),3)) AS geom
            FROM vetores_carga GROUP BY codigo
        """)
        if eh_uf:
            conexao.execute("""
                INSERT INTO unidades_federativas (sigla,nome,fonte_id,geom)
                SELECT codigo,nome,%s,geom FROM territorios_carga
                ON CONFLICT (sigla) DO UPDATE SET nome=excluded.nome, fonte_id=excluded.fonte_id, geom=excluded.geom
            """, (fonte,))
        else:
            if not somente_inicial:
                conexao.execute("""
                    DELETE FROM terras_indigenas t
                    WHERE NOT EXISTS (SELECT 1 FROM territorios_carga c WHERE t.codigo = c.codigo)
                """)
            conexao.execute("""
                INSERT INTO terras_indigenas (codigo,nome,situacao,fonte_id,geom)
                SELECT codigo,nome,situacao,%s,geom FROM territorios_carga
                ON CONFLICT (codigo) DO UPDATE SET nome=excluded.nome, situacao=excluded.situacao,
                    fonte_id=excluded.fonte_id, geom=excluded.geom
            """, (fonte,))
        ativar_base(conexao, camada, fonte)
        if somente_inicial and eh_uf:
            validar_ufs_publicadas(conexao, fonte)
        elif somente_inicial:
            ocorrencias["publicadas"] = validar_terras_publicadas(conexao, fonte)
        conexao.execute("ANALYZE unidades_federativas" if eh_uf else "ANALYZE terras_indigenas")
    return ocorrencias


def analisador():
    cli = ArgumentParser(description="Importar a base nacional de terras indígenas ou de UFs.")
    argumentos_vetor(cli)
    cli.add_argument("--tipo", choices=("terra", "uf"), default="terra")
    cli.add_argument("--campo-situacao")
    cli.add_argument("--codificacao", help="Codificação de caracteres declarada no arquivo vetorial.")
    cli.add_argument("--somente-inicial", action="store_true",
                     help="Aceita somente a primeira carga ou a repetição da aquisição já ativa.")
    return cli


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    cli = analisador()
    try:
        print("Carga concluída.", importar(cli.parse_args()))
    except (ValueError, OSError) as erro:
        cli.error(str(erro))
