from flask import current_app
from psycopg import sql

from controle.controle_terras_indigenas import exigir_recorte
from controle.validacao import ErroParametro, aceitar, janela
from db.conexao import conectar, tabelas_disponiveis
from modelo import modelo_hidrografia, modelo_infraestrutura, modelo_terras_indigenas

PARAMETROS_JANELA = ("bbox", "zoom", "limite", "apos", "uf", "terra_id")
LIMITE_DESLOCAMENTO_LOCAL = 6000
CONSULTAS = {
    "terras": modelo_terras_indigenas.geometrias,
    "rios": modelo_hidrografia.geometrias,
    "estradas": modelo_infraestrutura.estradas,
    "pistas": modelo_infraestrutura.pistas,
}
TABELAS_REDES = {"rios": "rios", "estradas": "estradas", "pistas": "pistas_pouso"}


def redes_habilitadas():
    return current_app.config["REDES_HABILITADAS"]


def colecao(linhas, limite, *, deslocamento=None, **metadados):
    feicoes = []
    omitidas = 0
    for linha in linhas[:limite]:
        geometria = linha.get("geometria")
        if geometria is None:
            omitidas += 1
            continue
        feicoes.append({
            "type": "Feature", "id": linha["id"], "geometry": geometria,
            "properties": {chave: valor for chave, valor in linha.items() if chave not in ("id", "geometria")},
        })
    proximo = None
    if len(linhas) > limite:
        proximo = linhas[limite - 1]["id"] if deslocamento is None else deslocamento + limite
    return {
        "type": "FeatureCollection", "features": feicoes,
        "metadados": dict(metadados, proximo=proximo, omitidas=omitidas),
    }


def camada(parametros, nome):
    aceitar(parametros, *PARAMETROS_JANELA, *(("formato",) if nome == "terras" else ()))
    recorte = janela(parametros)
    local = nome != "terras"
    if local:
        oeste, sul, leste, norte = recorte["caixa"]
        if leste - oeste > 6 or norte - sul > 6:
            raise ErroParametro("Aproxime o mapa ou informe uma bbox de até seis graus por eixo.")
        if recorte["apos"] > LIMITE_DESLOCAMENTO_LOCAL:
            raise ErroParametro("Aproxime o mapa para continuar a consulta a partir de 6.000 feições.")
        if not redes_habilitadas():
            return colecao([], recorte["limite"], estado="alvo_ausente", representacao="cartografica")
    with conectar() as conexao:
        if local:
            if not tabelas_disponiveis(conexao, TABELAS_REDES[nome]):
                return colecao([], recorte["limite"], estado="alvo_ausente", representacao="cartografica")
            conexao.execute("SET LOCAL statement_timeout = '12s'")
        exigir_recorte(conexao, recorte["terra"], recorte["uf"])
        linhas = CONSULTAS[nome](conexao, recorte)
    resultado = colecao(linhas, recorte["limite"], deslocamento=recorte["apos"] if local else None,
                        representacao="cartografica")
    if local and (resultado["metadados"]["proximo"] or 0) > LIMITE_DESLOCAMENTO_LOCAL:
        resultado["metadados"]["proximo"] = None
    return resultado


def saude():
    with conectar() as conexao:
        return conexao.execute("""
            SELECT 'ok' AS estado, postgis_version() AS postgis,
                   EXISTS (SELECT 1 FROM anos_garimpo) AS tem_anos
        """).fetchone()


def fontes(parametros):
    aceitar(parametros)
    with conectar() as conexao:
        ativas = (
            sql.SQL("UNION SELECT fonte_id FROM bases_ativas")
            if tabelas_disponiveis(conexao, "bases_ativas") else sql.SQL("")
        )
        linhas = conexao.execute(sql.SQL("""
            SELECT f.id, f.tipo, f.nome, f.url, f.colecao, f.versao, f.licenca, f.adquirido_em,
                   f.metodologia, f.sha256, f.metadados
            FROM fontes_dados f
            WHERE f.id IN (SELECT fonte_id FROM anos_garimpo
                           UNION SELECT fonte_id FROM terras_indigenas
                           UNION SELECT fonte_id FROM unidades_federativas {})
            ORDER BY f.adquirido_em DESC, f.id DESC LIMIT 200
        """).format(ativas)).fetchall()
    return {"dados": linhas}

