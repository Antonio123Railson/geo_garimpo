from controle.controle_mapa import PARAMETROS_JANELA, colecao
from controle.controle_terras_indigenas import exigir_recorte
from controle.validacao import RecursoAusente, aceitar, ano, inteiro, janela
from db.conexao import conectar
from modelo import modelo_garimpo


def anos(parametros):
    aceitar(parametros)
    with conectar() as conexao:
        itens = modelo_garimpo.anos(conexao)
    return {
        "anos": [item["ano"] for item in itens], "fontes": itens,
        "primeiro": itens[0]["ano"] if itens else None, "ultimo": itens[-1]["ano"] if itens else None,
    }


def geometrias(parametros):
    aceitar(parametros, "ano", "classe", *PARAMETROS_JANELA)
    referencia = ano(parametros, obrigatorio=True)
    classe = inteiro(parametros, "classe", 0, 2_147_483_647)
    recorte = janela(parametros)
    with conectar() as conexao:
        exigir_recorte(conexao, recorte["terra"], recorte["uf"])
        classes = modelo_garimpo.classes_no_ano(conexao, referencia)
        if classes is None and modelo_garimpo.tem_anos(conexao):
            raise RecursoAusente("Ano de garimpo não carregado.")
        if classes is not None and classe is not None and classe not in classes:
            raise RecursoAusente("Classe não carregada para esse ano.")
        linhas = modelo_garimpo.geometrias(conexao, recorte, referencia, classe) if classes else []
    return colecao(linhas, recorte["limite"], ano=referencia, ano_disponivel=classes is not None,
                   representacao="cartografica")
