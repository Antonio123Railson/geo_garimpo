from argparse import ArgumentParser

from db.conexao import conectar
from processamento.carga import (
    argumentos_vetor, assinatura_arquivo, bloquear_escritas, copiar_vetores, invalidar_resultados,
    ler_manifesto, publicar_rede, registrar_fonte,
)


def importar(argumentos, configuracao=None):
    manifesto = ler_manifesto(argumentos.metadados)
    assinatura = assinatura_arquivo(argumentos.arquivo)
    eh_pista = argumentos.tipo == "pistas"
    tabela = "pistas_pouso" if eh_pista else "estradas"
    classes = sorted(set(argumentos.classes))
    with conectar(escrita=True, configuracao=configuracao) as conexao:
        bloquear_escritas(conexao)
        ocorrencias = copiar_vetores(
            conexao, argumentos, tipo_geom="pista" if eh_pista else "LineString",
            campo_atributo=argumentos.campo_classe, classes=classes,
        )
        fonte = registrar_fonte(conexao, "osm", manifesto, assinatura, {
            "abrangencia": "nacional", "camada": argumentos.camada, "classes": classes,
            "campo_codigo": argumentos.campo_codigo, "campo_nome": argumentos.campo_nome,
            "campo_classe": argumentos.campo_classe, **ocorrencias,
        })
        ativa = conexao.execute("SELECT fonte_id FROM bases_ativas WHERE camada = %s", (tabela,)).fetchone()
        if not ativa or ativa["fonte_id"] != fonte:
            invalidar_resultados(conexao, apenas_proximidades=True)
            publicar_rede(conexao, tabela, fonte)
    return ocorrencias


def analisador():
    cli = ArgumentParser(description="Importar um extrato vetorial nacional de estradas ou pistas do OpenStreetMap.")
    argumentos_vetor(cli)
    cli.add_argument("--tipo", required=True, choices=("estradas", "pistas"))
    cli.add_argument("--campo-classe", required=True)
    cli.add_argument("--classes", nargs="+", required=True)
    return cli


if __name__ == "__main__":
    cli = analisador()
    try:
        print("Carga concluída.", importar(cli.parse_args()))
    except (ValueError, OSError) as erro:
        cli.error(str(erro))
