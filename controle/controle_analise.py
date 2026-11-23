from controle.controle_mapa import TABELAS_REDES, colecao, redes_habilitadas
from controle.controle_terras_indigenas import exigir_recorte
from controle.validacao import ErroParametro, RecursoAusente, aceitar, ano, escolha, inteiro, janela
from db.conexao import conectar, tabelas_disponiveis
from modelo import modelo_estatisticas, modelo_garimpo


def consultar(parametros, analise):
    extras = ("alvo", "raio", "tipo") if analise == "proximidades" else ()
    aceitar(parametros, "ano", "formato", "bbox", "limite", "apos", "uf", "terra_id", *extras)
    referencia = ano(parametros, obrigatorio=True)
    formato = escolha(parametros, "formato", ("geojson", "csv"), "geojson")
    recorte = janela(parametros)
    if recorte["terra"] and recorte["uf"]:
        raise ErroParametro("Selecione uma terra indígena ou uma UF para a análise.")
    tipo = escolha(parametros, "tipo", ("detectada", "nova"), "detectada") if extras else "nova"
    filtro = {}
    if extras:
        filtro = {
            "alvo": escolha(parametros, "alvo", ("rios", "estradas", "pistas"), "rios"),
            "raio": inteiro(parametros, "raio", 1, 1_000_000, 1000),
            "tipo": tipo,
        }
        if not redes_habilitadas():
            return colecao([], recorte["limite"], ano=referencia, estado="alvo_ausente",
                           execucao=None, **filtro), formato
    with conectar() as conexao:
        exigir_recorte(conexao, recorte["terra"], recorte["uf"])
        disponivel = modelo_garimpo.classes_no_ano(conexao, referencia) is not None
        if not disponivel and modelo_garimpo.tem_anos(conexao):
            raise RecursoAusente("Ano de garimpo não carregado.")
        impedimento = modelo_garimpo.impedimento_temporal(conexao, referencia) if disponivel and tipo == "nova" else None
        camada = TABELAS_REDES[filtro["alvo"]] if extras else "manchas_indigenas"
        tabelas = {"execucoes_analise", "manchas_indigenas", camada}
        if extras:
            tabelas.add("analises_proximidade")
        presentes = tabelas_disponiveis(conexao, *tabelas)
        execucao = modelo_estatisticas.execucao(conexao, analise) if presentes == tabelas else None
        if execucao:
            anos_calculados = execucao["fontes"].get("mapbiomas", {})
            if (str(referencia) not in anos_calculados or
                    (tipo == "nova" and impedimento is None and str(referencia - 1) not in anos_calculados)):
                execucao = None
        camada_disponivel = not extras or (
            camada in presentes and (not execucao or camada in execucao["fontes"].get("bases", {}))
        )
        linhas = []
        if disponivel and execucao and camada_disponivel and impedimento is None:
            linhas = (
                modelo_estatisticas.proximidade(conexao, recorte, ano=referencia, **filtro)
                if extras else modelo_estatisticas.expansao(conexao, recorte, referencia)
            )
        if not disponivel:
            estado = "sem_serie"
        elif not camada_disponivel:
            estado = "alvo_ausente"
        elif impedimento:
            estado = impedimento
        elif not execucao:
            estado = "pendente"
        else:
            estado = "calculado"
    resultado = colecao(linhas, recorte["limite"], ano=referencia, estado=estado, execucao=execucao, **filtro)
    return resultado, formato
