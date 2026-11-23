from controle.validacao import MAXIMO_ID, RecursoAusente, aceitar, escolha, inteiro
from db.conexao import conectar
from modelo import modelo_terras_indigenas


def exigir_recorte(conexao, terra, uf):
    if terra is not None and not modelo_terras_indigenas.obter(conexao, terra):
        raise RecursoAusente("Terra indígena não encontrada.")
    if uf is not None and not modelo_terras_indigenas.tem_uf(conexao, uf):
        raise RecursoAusente("Limite dessa UF ainda não importado. Carregue a malha do IBGE.")


def listar(parametros):
    formato = escolha(parametros, "formato", ("lista", "geojson"), "geojson")
    if formato == "geojson":
        from controle.controle_mapa import camada

        return camada(parametros, "terras")
    aceitar(parametros, "formato", "limite", "apos")
    limite = inteiro(parametros, "limite", 1, 400, 200)
    apos = inteiro(parametros, "apos", 0, MAXIMO_ID, 0)
    with conectar() as conexao:
        linhas = modelo_terras_indigenas.catalogo(conexao, limite, apos)
    return {"dados": linhas[:limite], "proximo": linhas[limite - 1]["id"] if len(linhas) > limite else None}
