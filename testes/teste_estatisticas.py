import json

import geopandas as gpd
import numpy as np
import pytest
import rasterio
from rasterio.transform import from_origin
from shapely.geometry import LineString, Point, box, shape

from controle.validacao import UFS
from db.conexao import conectar
from processamento import (
    calcular_estatisticas, calcular_proximidades, importar_hidrografia, importar_mapbiomas, importar_osm,
    importar_terras_indigenas,
)

X = -4_800_000
Y = -1_500_000
CODIGO_TESTE = 7


def arquivo_vetor(pasta, nome, linhas):
    caminho = pasta / f"{nome}.gpkg"
    gpd.GeoDataFrame(linhas, crs=6933).to_file(caminho, layer=nome)
    return caminho


def arquivo_anual(pasta, ano, pixels, mascara_alterada=False):
    caminho = pasta / f"teste_{ano}.tif"
    valores = np.zeros((4, 4), dtype="uint8")
    for i, j in pixels:
        valores[i, j] = CODIGO_TESTE
    if mascara_alterada:
        valores[3, 3] = 255
    with rasterio.open(caminho, "w", driver="GTiff", height=4, width=4, count=1, dtype="uint8",
                       crs="EPSG:6933", transform=from_origin(X, Y, 100, 100), nodata=255) as arquivo:
        arquivo.write(valores, 1)
    return caminho


@pytest.fixture(scope="module")
def dados_calculados(criar_banco_teste, tmp_path_factory):
    configuracao = criar_banco_teste()
    pasta = tmp_path_factory.mktemp("geometrias_exclusivas_de_teste")
    metadados = pasta / "metadados_teste.json"
    metadados.write_text(json.dumps({
        "nome": "Artefato geométrico de teste", "url": "https://example.invalid/teste",
        "colecao": "teste", "versao": "teste", "licenca": "fixture local", "adquirido_em": "2020-01-01",
        "metodologia": "Geometrias controladas para teste automatizado.",
    }), encoding="utf-8")
    comuns = ["--metadados", str(metadados), "--confirmar-cobertura-nacional"]
    vetor = [*comuns, "--campo-codigo", "codigo", "--campo-nome", "nome"]
    terras = arquivo_vetor(pasta, "terras_teste", [
        {"codigo": "T-A", "nome": "Área de teste A", "geometry": box(X-50, Y-450, X+250, Y+50)},
        {"codigo": "T-B", "nome": "Área de teste B", "geometry": box(X+150, Y-450, X+450, Y+50)},
    ])
    importar_terras_indigenas.importar(importar_terras_indigenas.analisador().parse_args([str(terras), *vetor]), configuracao)
    limites_uf = {
        "SP": box(X-50, Y-450, X+150, Y+50),
        "MG": box(X+150, Y-450, X+450, Y+50),
    }
    ufs = arquivo_vetor(pasta, "ufs_teste", [
        {"codigo": sigla, "nome": sigla, "geometry": limites_uf.get(
            sigla, box(X+500+100*i, Y-450, X+550+100*i, Y+50)
        )}
        for i, sigla in enumerate(UFS)
    ])
    importar_terras_indigenas.importar(importar_terras_indigenas.analisador().parse_args([
        str(ufs), *vetor, "--tipo", "uf",
    ]), configuracao)
    periodos = {1985: [(0, 0), (0, 1)], 1986: [(0, 1), (0, 3), (2, 1)], 1988: []}
    for ano, pixels in periodos.items():
        arquivo = arquivo_anual(pasta, ano, pixels)
        importar_mapbiomas.importar(importar_mapbiomas.analisador().parse_args([
            str(arquivo), *comuns, "--ano", str(ano), "--classes", str(CODIGO_TESTE),
            "--legenda", "https://example.invalid/legenda",
        ]), configuracao)

    invalido = arquivo_anual(pasta, 1987, [(0, 0)], mascara_alterada=True)
    with pytest.raises(ValueError, match="Série incompatível"):
        importar_mapbiomas.importar(importar_mapbiomas.analisador().parse_args([
            str(invalido), *comuns, "--ano", "1987", "--classes", str(CODIGO_TESTE),
            "--legenda", "https://example.invalid/legenda",
        ]), configuracao)

    rios = arquivo_vetor(pasta, "rios_teste", [
        {"codigo": "R1", "nome": "Linha de teste", "geometry": LineString([(X, Y-50), (X+400, Y-50)])},
        {"codigo": "R2", "nome": "Outra linha", "geometry": LineString([(X, Y-900), (X+400, Y-900)])},
    ])
    importar_hidrografia.importar(importar_hidrografia.analisador().parse_args([str(rios), *vetor]), configuracao)
    pistas = arquivo_vetor(pasta, "pistas_teste", [
        {"codigo": "P1", "nome": "Ponto de teste", "classe": "runway", "geometry": Point(X+160, Y-60)},
    ])
    importar_osm.importar(importar_osm.analisador().parse_args([
        str(pistas), *vetor, "--tipo", "pistas", "--campo-classe", "classe", "--classes", "runway",
    ]), configuracao)
    assert calcular_estatisticas.calcular(configuracao) == [1985, 1986, 1988]
    assert set(calcular_proximidades.calcular(configuracao)) == {"rios", "pistas"}
    return configuracao


@pytest.fixture
def cliente_calculado(cliente, dados_calculados, monkeypatch):
    cliente.application.config["REDES_HABILITADAS"] = True
    monkeypatch.setattr("db.conexao.dotenv_values", lambda *args, **kwargs: dados_calculados)
    return cliente


def test_evolucao_e_sobreposicao(cliente_calculado):
    assert cliente_calculado.get("/api/anos").json["anos"] == [1985, 1986, 1988]
    resposta = cliente_calculado.get("/api/estatisticas/evolucao")
    assert resposta.status_code == 200
    primeiro, segundo, sem_anterior = resposta.json["dados"]
    assert primeiro["area_detectada_ha"] == pytest.approx(2, rel=1e-5)
    assert primeiro["area_nova_ha"] is None
    assert segundo["area_detectada_ha"] == pytest.approx(3, rel=1e-5)
    assert segundo["area_detectada_km2"] == pytest.approx(.03, rel=1e-5)
    assert segundo["area_nova_ha"] == pytest.approx(2, rel=1e-5)
    assert segundo["variacao_liquida_ha"] == pytest.approx(1, rel=1e-5)
    assert segundo["crescimento_pct"] == pytest.approx(50, rel=1e-5)
    assert sem_anterior["area_detectada_ha"] == 0
    assert sem_anterior["variacao_liquida_ha"] is None
    ranking = cliente_calculado.get("/api/estatisticas/ranking?ano=1986&escopo=terra").json["dados"]
    assert sum(linha["area_detectada_ha"] for linha in ranking) > segundo["area_detectada_ha"]
    terra = ranking[0]["terra_id"]
    assert len(cliente_calculado.get(f"/api/estatisticas/terra-indigena/{terra}").json["dados"]) == 3


def test_geojson_e_paginacao_espacial(cliente_calculado):
    terras = cliente_calculado.get("/api/terras-indigenas?limite=1")
    assert terras.status_code == 200
    colecao = terras.json
    assert len(colecao["features"]) == 1
    feicao = colecao["features"][0]
    cursor = colecao["metadados"]["proximo"]
    assert cursor == feicao["id"]
    geometria = shape(feicao["geometry"])
    assert geometria.is_valid and geometria.bounds[0] < -40
    outra = cliente_calculado.get(f"/api/terras-indigenas?limite=1&apos={cursor}").json
    assert outra["features"][0]["id"] != cursor
    janela = cliente_calculado.get(f"/api/garimpos?ano=1986&terra_id={cursor}&zoom=15").json
    assert janela["features"]
    assert cliente_calculado.get("/api/garimpos?ano=1987").status_code == 404
    assert cliente_calculado.get("/api/garimpos?ano=1986&classe=999").status_code == 404


def test_uf_recorta_geometrias_e_calcula_area_zonal(cliente_calculado):
    resposta = cliente_calculado.get("/api/estatisticas/evolucao?uf=SP&inicio=1986&fim=1986")
    assert resposta.status_code == 200
    assert resposta.json["dados"][0]["area_detectada_ha"] == pytest.approx(1, rel=1e-5)
    ranking = cliente_calculado.get("/api/estatisticas/ranking?ano=1986&escopo=uf").json["dados"]
    assert [linha["uf"] for linha in ranking] == ["MG", "SP"]
    assert sum(linha["area_detectada_ha"] for linha in ranking) == pytest.approx(3, rel=1e-5)
    mapa = cliente_calculado.get("/api/garimpos?ano=1986&uf=SP&zoom=15")
    assert mapa.status_code == 200 and len(mapa.json["features"]) == 2


def test_proximidades_e_exportacao(cliente_calculado, dados_calculados):
    expansao = cliente_calculado.get("/api/analise/expansao?ano=1986&limite=1")
    assert expansao.status_code == 200 and len(expansao.json["features"]) == 1
    cursor = expansao.json["metadados"]["proximo"]
    outra = cliente_calculado.get(f"/api/analise/expansao?ano=1986&limite=1&apos={cursor}&formato=csv")
    assert outra.status_code == 200
    assert len(outra.text.splitlines()) == 2
    assert cliente_calculado.get("/api/analise/expansao?ano=1988").json["metadados"]["estado"] == "sem_ano_anterior"
    rios = cliente_calculado.get("/api/analise/proximidade?ano=1986&raio=1").json["features"]
    assert len(rios) == 2
    assert all(feicao["properties"]["distancia_m"] == pytest.approx(0) for feicao in rios)
    assert cliente_calculado.get("/api/analise/proximidade?ano=1986&alvo=pistas&raio=1").json["features"]
    assert cliente_calculado.get("/api/analise/proximidade?ano=1986&alvo=estradas").json["metadados"]["estado"] == "alvo_ausente"
    with conectar(configuracao=dados_calculados) as conexao:
        maiores = conexao.execute("""
            SELECT max(abs(p.distancia_m - (
                SELECT min(ST_Distance(r.geom::geography,m.geom::geography)) FROM rios r
            ))) AS erro FROM analises_proximidade p JOIN manchas_indigenas m ON m.id=p.mancha_id
            WHERE p.alvo='rios'
        """).fetchone()
    assert maiores["erro"] == pytest.approx(0)
