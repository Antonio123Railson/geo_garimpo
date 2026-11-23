import json
import math
from argparse import ArgumentParser
from operator import itemgetter
from pathlib import Path

from db.conexao import RAIZ, conectar
from processamento.mapbiomas_blocos import publicar_arquivo

CONFERENCIA = RAIZ / "db" / "carga" / "04_validar_mapbiomas.sql"


def consultar():
    grupos = []
    with conectar(escrita=True) as conexao:
        conexao.autocommit = True
        resultado = conexao.execute(CONFERENCIA.read_text(encoding="utf-8"))
        while True:
            if resultado.description is not None:
                grupos.append(resultado.fetchall())
            if not resultado.nextset():
                break
        inventario = conexao.execute("""
            SELECT jsonb_array_elements(f.metadados->'arquivos') AS arquivo
            FROM public.fontes_dados f JOIN public.anos_garimpo a
              ON f.metadados->>'fonte_anual_id' = a.fonte_id::text
            WHERE a.ano = 2025 AND f.metadados->>'papel' = 'inventario_raster'
        """).fetchall()
    campos = ("cobertura_temporal", "anos", "classes_2025", "bases", "inventario", "area_2025",
              "intersecao_tis_2025", "distribuicao_uf_2025")
    return dict(zip(campos, grupos, strict=True)), [linha["arquivo"] for linha in inventario]


def conferir(relatorio, inventario, inspecao=None):
    piloto = next((ano for ano in relatorio["anos"] if ano["ano"] == 2025), None)
    if piloto is None:
        raise ValueError("A publicação de 2025 não foi encontrada.")
    campos_erros = ("invalidas_ou_vazias", "tipo_ou_srid_incorreto", "referencias_incorretas",
                    "geometrias_binarias_repetidas", "classes_fora_da_selecao")
    erros = {chave: piloto[chave] for chave in campos_erros if piloto[chave]}
    if erros:
        raise ValueError(f"Foram encontradas inconsistências no PostGIS: {erros}.")
    if piloto["srids"] != [4674] or not piloto["poligonos"]:
        raise ValueError("O piloto não contém polígonos em EPSG:4674.")
    if sum(linha["poligonos_na_classe"] for linha in relatorio["classes_2025"]) != piloto["poligonos"]:
        raise ValueError("A contagem por classe diverge da contagem anual.")
    if len(relatorio["distribuicao_uf_2025"]) != relatorio["bases"][0]["unidades_federativas"]:
        raise ValueError("A distribuição não contém todas as UFs cadastradas.")

    area = relatorio["area_2025"][0]
    tolerancia = max(0.01, area["hectares_garimpo_mapeado"] * 1e-7)
    if not math.isclose(area["diferenca_soma_e_uniao_ha"], 0, abs_tol=tolerancia):
        raise ValueError("A soma das feições diverge da área deduplicada.")
    if area["diferenca_vetor_e_raster_ha"] is None or not math.isclose(
        area["diferenca_vetor_e_raster_ha"], 0, abs_tol=tolerancia,
    ):
        raise ValueError("A área vetorial deduplicada diverge da área dos pixels selecionados.")

    if inspecao is not None:
        if (piloto["assinatura_grade"] != inspecao["assinatura_grade"] or piloto["sha256"] != inspecao["sha256_lote"]
                or piloto["poligonos"] != inspecao["poligonos"] or piloto["classes"] != inspecao["classes_selecionadas"]):
            raise ValueError("A publicação diverge dos blocos conferidos na inspeção.")
        por_arquivo = itemgetter("arquivo")
        if sorted(inventario, key=por_arquivo) != sorted(inspecao["inventario"], key=por_arquivo):
            raise ValueError("O inventário SHA-256 registrado diverge da inspeção local.")


def main():
    cli = ArgumentParser(description="Conferir a publicação MapBiomas 2025 sem alterar tabelas definitivas.")
    cli.add_argument("--inspecao", type=Path, help="Relatório JSON original do importador.")
    cli.add_argument("--saida", type=Path, help="Destino do relatório JSON de validação.")
    argumentos = cli.parse_args()
    try:
        relatorio, inventario = consultar()
        inspecao = json.loads(argumentos.inspecao.read_text(encoding="utf-8")) if argumentos.inspecao else None
        conferir(relatorio, inventario, inspecao)
        if argumentos.saida:
            argumentos.saida.parent.mkdir(parents=True, exist_ok=True)
            publicar_arquivo(argumentos.saida, lambda fluxo: fluxo.write(
                json.dumps(relatorio, ensure_ascii=False, indent=2).encode("utf-8")
            ))
        print(json.dumps(relatorio, ensure_ascii=False, indent=2))
    except ValueError as erro:
        cli.error(str(erro))


if __name__ == "__main__":
    main()
