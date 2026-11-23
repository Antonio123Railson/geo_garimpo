import json
from argparse import ArgumentParser
from pathlib import Path

from db.conexao import RAIZ, conectar

SECOES = ("camadas", "fontes", "classes", "cobertura_ufs", "bases_preservadas")
CONSULTA = RAIZ / "db" / "carga" / "05_validar_redes_nacionais.sql"


def auditar(antes=None):
    with conectar(escrita=True) as conexao:
        conexao.autocommit = True
        resultados = conexao.execute(CONSULTA.read_text(encoding="utf-8"))
        conjuntos = []
        while True:
            if resultados.description:
                conjuntos.append(resultados.fetchall())
            if not resultados.nextset():
                break
    relatorio = dict(zip(SECOES, conjuntos, strict=True))
    relatorio["ufs_por_camada"] = {
        "rios": sum(linha["rios"] for linha in relatorio["cobertura_ufs"]),
        "estradas": sum(linha["estradas"] for linha in relatorio["cobertura_ufs"]),
        "pistas_pouso": sum(bool(linha["pistas"] or linha["aerodromos"] or linha["faixas_pouso"])
                            for linha in relatorio["cobertura_ufs"]),
    }
    if antes is not None:
        relatorio["preservadas"] = all(
            antes[linha["camada"]] == {chave: linha[chave] for chave in ("registros", "assinatura")}
            for linha in relatorio["bases_preservadas"]
        )
    return relatorio


def principal():
    cli = ArgumentParser(description="Auditar rios, estradas e pistas carregados no PostgreSQL.")
    cli.add_argument("--antes", type=Path, help="Assinaturas das bases registradas antes da carga.")
    cli.add_argument("--saida", type=Path, required=True)
    argumentos = cli.parse_args()
    antes = json.loads(argumentos.antes.read_text(encoding="utf-8")) if argumentos.antes else None
    relatorio = auditar(antes)
    argumentos.saida.parent.mkdir(parents=True, exist_ok=True)
    argumentos.saida.write_text(json.dumps(relatorio, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    for linha in relatorio["camadas"]:
        print(f"{linha['camada']}: {linha['registros']:,} registros; SRIDs {linha['srids']}.")
    erros = ("invalidas", "vazias", "srid_incorreto", "tipos_incorretos", "fontes_incorretas")
    esperadas = {linha["camada"]: linha["metadados"].get("inseridas") for linha in relatorio["fontes"]}
    falhas = any(
        not linha["registros"] or linha["registros"] != esperadas.get(linha["camada"])
        or linha["srids"] != [4674] or any(linha[chave] for chave in erros)
        for linha in relatorio["camadas"]
    )
    return int(falhas or (antes is not None and not relatorio["preservadas"]))


if __name__ == "__main__":
    raise SystemExit(principal())
