from datetime import date

from controle.controle_terras_indigenas import exigir_recorte
from controle.validacao import MAXIMO_ID, UFS, ErroParametro, RecursoAusente, aceitar, ano, escolha, inteiro
from db.conexao import conectar, tabelas_disponiveis
from modelo import modelo_estatisticas, modelo_garimpo, modelo_terras_indigenas


def evolucao(parametros, terra_id=None):
    aceitar(parametros, "inicio", "fim", *(("uf",) if terra_id is None else ()))
    inicio = ano(parametros, "inicio") or 1985
    fim = ano(parametros, "fim") or date.today().year
    if inicio > fim:
        raise ErroParametro("O ano inicial deve ser anterior ou igual ao final.")
    terra = inteiro({"terra_id": terra_id}, "terra_id", 1, MAXIMO_ID)
    uf = escolha(parametros, "uf", UFS)
    escopo = "terra" if terra is not None else "uf" if uf else "brasil"
    with conectar() as conexao:
        exigir_recorte(conexao, terra, uf)
        linhas = []
        if tabelas_disponiveis(conexao, "estatisticas_anuais"):
            linhas = modelo_estatisticas.evolucao(conexao, escopo=escopo, terra=terra, uf=uf, inicio=inicio, fim=fim)
        territorio = modelo_terras_indigenas.obter(conexao, terra) if terra else None
    return {"escopo": escopo, "territorio": territorio, "uf": uf, "dados": linhas,
            "estado": "calculado" if linhas else "pendente"}


def ranking(parametros):
    aceitar(parametros, "ano", "escopo", "limite")
    referencia = ano(parametros, obrigatorio=True)
    escopo = escolha(parametros, "escopo", ("terra", "uf"), "terra")
    limite = inteiro(parametros, "limite", 1, 100, 20)
    with conectar() as conexao:
        if modelo_garimpo.classes_no_ano(conexao, referencia) is None and modelo_garimpo.tem_anos(conexao):
            raise RecursoAusente("Ano de garimpo não carregado.")
        linhas = (
            modelo_estatisticas.ranking(conexao, referencia, escopo, limite)
            if tabelas_disponiveis(conexao, "estatisticas_anuais") else []
        )
    return {"ano": referencia, "escopo": escopo, "dados": linhas}
