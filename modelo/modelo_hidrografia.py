from modelo.espacial import consultar_camadas


def geometrias(conexao, janela):
    return consultar_camadas(conexao, "rios", ("codigo", "nome", "fonte_id"), janela)
