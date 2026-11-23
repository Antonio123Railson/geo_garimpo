import hashlib
import json
import logging
import re
from contextlib import contextmanager
from itertools import combinations
from pathlib import Path
from xml.etree import ElementTree as XML

import rasterio
from rasterio.enums import MaskFlags
from rasterio.io import MemoryFile
from rasterio.transform import Affine
from rasterio.warp import transform_bounds

from processamento.carga import assinatura_arquivo

REGISTRO = logging.getLogger(__name__)
TIPOS_GDAL = {
    "uint8": "Byte", "int8": "Int8", "uint16": "UInt16", "int16": "Int16",
    "uint32": "UInt32", "int32": "Int32", "uint64": "UInt64", "int64": "Int64",
}


def inventariar(arquivos):
    arquivos = sorted(set(arquivos))
    REGISTRO.info("Calculando SHA-256 de %s arquivos da grade.", len(arquivos))
    inventario = [{"arquivo": arquivo.name, "sha256": assinatura_arquivo(arquivo)} for arquivo in arquivos]
    assinatura = inventario[0]["sha256"] if len(inventario) == 1 else hashlib.sha256(
        json.dumps(inventario, sort_keys=True, ensure_ascii=False).encode("utf-8")
    ).hexdigest()
    return assinatura, inventario


def conferir_exportacao(partes, prefixo, lado):
    padrao = re.compile(rf"{re.escape(prefixo)}-(\d{{10}})-(\d{{10}})([^.]*)\.tiff?", re.IGNORECASE)
    grupos = {}
    for arquivo in partes:
        identificacao = padrao.fullmatch(arquivo.name)
        if identificacao is None:
            raise ValueError(f"O arquivo {arquivo.name} não pertence à exportação {prefixo}.")
        linha, coluna, sufixo = identificacao.groups()
        grupos.setdefault((int(linha), int(coluna)), []).append((arquivo, sufixo))
    selecionados, duplicatas = [], []
    for posicao, grupo in sorted(grupos.items()):
        grupo.sort(key=lambda item: (bool(item[1]), item[0].name))
        principal, _ = grupo[0]
        selecionados.append(principal)
        if len(grupo) > 1:
            assinatura = assinatura_arquivo(principal)
            for arquivo, _ in grupo[1:]:
                if assinatura_arquivo(arquivo) != assinatura:
                    raise ValueError(f"Os arquivos da posição {posicao} têm conteúdos diferentes.")
                duplicatas.append({"arquivo": arquivo.name, "sha256": assinatura, "original": principal.name})
    return selecionados, duplicatas, {
        arquivo: (*posicao, lado)
        for posicao, grupo in grupos.items() for arquivo, _ in grupo
    }


def descrever_partes(arquivos):
    partes = []
    dependencias = set()
    for arquivo in arquivos:
        with rasterio.open(arquivo) as raster:
            if (raster.driver != "GTiff" or raster.count != 1 or not raster.crs or raster.nodata is None
                    or set(raster.mask_flag_enums[0]) != {MaskFlags.nodata}
                    or raster.dtypes[0] not in TIPOS_GDAL):
                raise ValueError(f"{arquivo.name} deve ser GeoTIFF de banda única, com SRC e NoData declarado.")
            chave = (raster.crs, raster.transform[:2] + raster.transform[3:5],
                     raster.dtypes[0], raster.nodata, raster.descriptions[0])
            if not partes:
                grade = chave
                origem = raster.transform
            if chave != grade:
                raise ValueError(f"{arquivo.name} diverge do SRC, da grade, do tipo de pixel ou do NoData das partes.")
            x, y = (~origem) @ (raster.transform.c, raster.transform.f)
            coluna, linha = round(x), round(y)
            if abs(x - coluna) > 1e-5 or abs(y - linha) > 1e-5:
                raise ValueError(f"{arquivo.name} está desalinhado da grade de pixels.")
            partes.append((arquivo, coluna, linha, raster.width, raster.height))
            dependencias.update(Path(nome).resolve() for nome in raster.files)
    return grade, origem, partes, dependencias


def montar_mosaico(arquivos, posicoes=None):
    (crs, _, tipo, nodata, descricao), origem, partes, dependencias = descrever_partes(arquivos)
    oeste, norte = min(p[1] for p in partes), min(p[2] for p in partes)
    largura = max(x + w for _, x, _, w, _ in partes) - oeste
    altura = max(y + h for _, _, y, _, h in partes) - norte
    if posicoes:
        for arquivo, x, y, w, h in partes:
            linha, coluna, lado = posicoes[arquivo]
            if ((y - norte, x - oeste) != (linha, coluna) or linha % lado or coluna % lado
                    or w != min(lado, largura - coluna) or h != min(lado, altura - linha)):
                raise ValueError(f"O deslocamento ou tamanho de {arquivo.name} diverge da grade da exportação.")
    for (_, x, y, w, h), (_, a, b, c, d) in combinations(partes, 2):
        if max(x, a) < min(x + w, a + c) and max(y, b) < min(y + h, b + d):
            raise ValueError("Há pixels sobrepostos entre as partes da exportação.")
    if sum(w * h for _, _, _, w, h in partes) != largura * altura:
        raise ValueError("Há lacunas na exportação. Reúna todas as partes do ano antes da carga.")

    transformacao = origem @ Affine.translation(oeste, norte)
    arvore = XML.Element("VRTDataset", rasterXSize=str(largura), rasterYSize=str(altura))
    XML.SubElement(arvore, "SRS").text = crs.to_wkt()
    XML.SubElement(arvore, "GeoTransform").text = ",".join(map(str, transformacao.to_gdal()))
    banda = XML.SubElement(arvore, "VRTRasterBand", dataType=TIPOS_GDAL[tipo], band="1")
    XML.SubElement(banda, "NoDataValue").text = str(nodata)
    if descricao:
        XML.SubElement(banda, "Description").text = descricao
    for arquivo, x, y, w, h in partes:
        fonte = XML.SubElement(banda, "SimpleSource")
        XML.SubElement(fonte, "SourceFilename", relativeToVRT="0").text = str(arquivo.resolve())
        XML.SubElement(fonte, "SourceBand").text = "1"
        XML.SubElement(fonte, "SrcRect", xOff="0", yOff="0", xSize=str(w), ySize=str(h))
        XML.SubElement(fonte, "DstRect", xOff=str(x - oeste), yOff=str(y - norte), xSize=str(w), ySize=str(h))
    return XML.tostring(arvore, encoding="utf-8"), dependencias


def conferir_extensao(raster, limites):
    oeste, sul, leste, norte = limites
    if not (-180 <= oeste < leste <= 180 and -90 <= sul < norte <= 90):
        raise ValueError("Os limites de exportação devem ser oeste, sul, leste e norte em longitude/latitude.")
    if not raster.crs:
        raise ValueError("O raster não contém SRC.")
    a, b, c, d = transform_bounds(raster.crs, "EPSG:4326", *raster.bounds)
    folga_x = 2 * (c - a) / raster.width
    folga_y = 2 * (d - b) / raster.height
    if a > oeste + folga_x or b > sul + folga_y or c < leste - folga_x or d < norte - folga_y:
        raise ValueError("Os arquivos não cobrem os limites declarados da exportação.")


@contextmanager
def abrir_raster(entrada, limites=None, *, prefixo=None, lado=16384, partes_explicitas=None):
    entrada = entrada.resolve()
    if partes_explicitas is not None or entrada.is_dir():
        if limites is None:
            raise ValueError("Informe --limites-exportacao ao carregar uma pasta de GeoTIFFs.")
        partes = sorted(partes_explicitas if partes_explicitas is not None else (
            p for p in entrada.iterdir() if p.is_file() and p.suffix.lower() in {".tif", ".tiff"}
        ))
        if not partes:
            raise ValueError("A pasta não contém GeoTIFFs.")
        duplicatas, posicoes = [], None
        if prefixo:
            partes, duplicatas, posicoes = conferir_exportacao(partes, prefixo, lado)
        vrt, arquivos = montar_mosaico(partes, posicoes)
        with MemoryFile(vrt, ext=".vrt") as memoria, memoria.open() as raster:
            conferir_extensao(raster, limites)
            assinatura, inventario = inventariar(arquivos)
            if duplicatas:
                REGISTRO.info("%s cópias idênticas ignoradas na montagem.", len(duplicatas))
            yield raster, assinatura, inventario + duplicatas
        return

    with rasterio.open(entrada) as raster:
        if raster.driver != "GTiff":
            raise ValueError("Informe um GeoTIFF ou uma pasta com as partes da exportação.")
        if limites is not None:
            conferir_extensao(raster, limites)
        assinatura, inventario = inventariar(Path(nome).resolve() for nome in raster.files)
        yield raster, assinatura, inventario
