from argparse import ArgumentParser

from db.conexao import conectar
from processamento.carga import (
    argumentos_vetor, assinatura_arquivo, bloquear_escritas, copiar_vetores, invalidar_resultados,
    ler_manifesto, publicar_rede, registrar_fonte,
)


def importar(argumentos, configuracao=None):
    manifesto = ler_manifesto(argumentos.metadados)
    assinatura = assinatura_arquivo(argumentos.arquivo)
    with conectar(escrita=True, configuracao=configuracao) as conexao:
        bloquear_escritas(conexao)
        ocorrencias = copiar_vetores(conexao, argumentos, tipo_geom="LineString")
        fonte = registrar_fonte(conexao, "ana", manifesto, assinatura, {
            "abrangencia": "nacional", "camada": argumentos.camada,
            "campo_codigo": argumentos.campo_codigo, "campo_nome": argumentos.campo_nome, **ocorrencias,
        })
        ativa = conexao.execute("SELECT fonte_id FROM bases_ativas WHERE camada = 'rios'").fetchone()
        if not ativa or ativa["fonte_id"] != fonte:
            invalidar_resultados(conexao, apenas_proximidades=True)
            publicar_rede(conexao, "rios", fonte)
    return ocorrencias


def analisador():
    cli = ArgumentParser(description="Importar a rede nacional de trechos de drenagem da ANA.")
    argumentos_vetor(cli)
    return cli


if __name__ == "__main__":
    cli = analisador()
    try:
        print("Carga concluída.", importar(cli.parse_args()))
    except (ValueError, OSError) as erro:
        cli.error(str(erro))
