from modelo.espacial import consultar_camadas


def estradas(conexao, janela):
    return consultar_camadas(conexao, "estradas", ("codigo", "nome", "classe", "fonte_id"), janela)


def pistas(conexao, janela):
    return consultar_camadas(conexao, "pistas_pouso", ("codigo", "nome", "classe", "fonte_id"), janela)
