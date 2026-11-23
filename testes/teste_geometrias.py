from collections import Counter

import pytest
from shapely.geometry import LineString, Polygon, box

from processamento.validar_dados import area_hectares, normalizar, reprojetar, validar_limites


def test_reparo_poligono_autointersectante():
    geometria = Polygon([(0, 0), (2, 2), (2, 0), (0, 2), (0, 0)])
    ocorrencias = Counter()
    reparada = normalizar(geometria, "Polygon", ocorrencias)
    assert reparada.is_valid
    assert len(reparada.geoms) == 2
    assert reparada.area == pytest.approx(2)
    assert ocorrencias["reparadas"] == 1


def test_rejeita_dimensao_incompativel():
    with pytest.raises(ValueError, match="componentes válidos"):
        normalizar(LineString([(0, 0), (1, 1)]), "Polygon")


def test_transformacao_sirgas_e_area_conhecida():
    poligono = box(-4_500_000, -1_400_000, -4_499_900, -1_399_900)
    geografico = reprojetar(poligono, "EPSG:6933")
    validar_limites(geografico)
    assert geografico.bounds[0] == pytest.approx(-46.64, abs=0.04)
    assert area_hectares(poligono, "EPSG:6933") == 1
    assert area_hectares(geografico) == pytest.approx(1, rel=1e-5)


def test_rejeita_coordenada_invalida():
    with pytest.raises(ValueError, match="fora dos limites"):
        validar_limites(box(300, 50, 310, 55))
