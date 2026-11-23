from shutil import copyfile

import numpy as np
import pytest
import rasterio
from rasterio.transform import from_origin

from processamento import mapbiomas_blocos as blocos
from processamento.mapbiomas_grade import abrir_raster


def escrever_tif(caminho, valores, oeste=-60):
    with rasterio.open(
        caminho, "w", driver="GTiff", crs="EPSG:4326",
        width=valores.shape[1], height=valores.shape[0], count=1,
        dtype="int16", transform=from_origin(oeste, -5, 0.001, 0.001), nodata=-32768,
    ) as raster:
        raster.write(valores.astype("int16"), 1)


def test_retomar_janelas_sem_duplicar_geometrias(tmp_path, monkeypatch):
    caminho = tmp_path / "controle.tif"
    valores = np.full((4, 4), 0, dtype="int16")
    valores[0, 0], valores[1, 1], valores[2, 2], valores[3, 3] = 1102, 1202, 2102, -32768
    escrever_tif(caminho, valores)
    monkeypatch.setattr(blocos, "TAMANHO_JANELA", 2)
    classes = [1102, 1202]
    ler = blocos.ler_janela
    with abrir_raster(caminho) as (raster, assinatura, inventario):
        grade = blocos.descrever_grade(raster, 1, classes)
        pasta = blocos.pasta_de_retomada(tmp_path, assinatura, grade, 1, classes, True, inventario)

        def interromper(a, b, c, janela, d):
            if janela.row_off == 2:
                raise RuntimeError("Interrupção de teste")
            return ler(a, b, c, janela, d)

        monkeypatch.setattr(blocos, "ler_janela", interromper)
        with pytest.raises(RuntimeError, match="Interrupção"):
            blocos.varrer(raster, 1, classes, grade, pasta=pasta, fiscalizar_legenda=True)
        assert len(list(pasta.glob("*.gz"))) == 2

        lidas = []

        def observar(a, b, c, janela, d):
            lidas.append(janela.row_off)
            return ler(a, b, c, janela, d)

        monkeypatch.setattr(blocos, "ler_janela", observar)
        retomado = blocos.varrer(raster, 1, classes, grade, pasta=pasta, fiscalizar_legenda=True)
        assert lidas == [2, 2]
        publicado = []
        for indice in range(retomado[1]["blocos"]):
            blocos.consumir_bloco(pasta / f"{indice:06d}.gz", emitir=publicado.append)
        direto = []
        assert retomado == blocos.varrer(raster, 1, classes, grade, emitir=direto.append)
        assert publicado == direto and [classe for classe, _ in publicado] == classes
        assert retomado[1]["pixels_validos"] == 15


def test_mosaico_recusa_lacuna_e_preserva_grade(tmp_path):
    a = np.array([[0, 1102], [-32768, 0]])
    b = np.array([[1202, 0], [0, 2102]])
    escrever_tif(tmp_path / "oeste.tif", a)
    escrever_tif(tmp_path / "leste.tif", b, oeste=-59.996)
    limites = (-60, -5.002, -59.996, -5)
    with pytest.raises(ValueError, match="lacunas"), abrir_raster(tmp_path, limites):
        pass
    escrever_tif(tmp_path / "leste.tif", b, oeste=-59.998)
    with abrir_raster(tmp_path, limites) as (raster, _, inventario):
        assert raster.transform == from_origin(-60, -5, 0.001, 0.001)
        assert len(inventario) == 2
        pixels = raster.read(1, masked=True)
        assert np.array_equal(pixels.data, np.hstack((a, b)))
        assert pixels.mask.sum() == 1



def test_exportacao_ignora_copia_identica_e_rejeita_conflito(tmp_path):
    prefixo = "piloto"
    oeste = tmp_path / "piloto-0000000000-0000000000.tif"
    leste = tmp_path / "piloto-0000000000-0000000002.tif"
    copia = tmp_path / "piloto-0000000000-0000000000 - Copia.tif"
    escrever_tif(oeste, np.full((2, 2), 1102))
    escrever_tif(leste, np.full((2, 2), 1202), oeste=-59.998)
    copyfile(oeste, copia)
    limites = (-60, -5.002, -59.996, -5)
    with abrir_raster(tmp_path, limites, prefixo=prefixo, lado=2) as (raster, _, inventario):
        assert raster.width == 4
        assert inventario[-1] == {"arquivo": copia.name, "sha256": inventario[0]["sha256"], "original": oeste.name}
        assert copia.exists()

    escrever_tif(copia, np.full((2, 2), 2102))
    with pytest.raises(ValueError, match="conteúdos diferentes"), abrir_raster(tmp_path, limites, prefixo=prefixo, lado=2):
        pass
