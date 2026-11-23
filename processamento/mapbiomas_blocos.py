import gzip
import hashlib
import json
import logging
from base64 import b64decode, b64encode
from collections import Counter
from pathlib import Path
from tempfile import NamedTemporaryFile

import numpy as np
from rasterio.features import shapes
from rasterio.enums import MaskFlags
from rasterio.windows import Window
from shapely.geometry import shape

from processamento.validar_dados import normalizar, reprojetar, transformador, validar_limites

REGISTRO = logging.getLogger(__name__)
TAMANHO_JANELA = 1024


def descrever_grade(raster, banda, classes):
    if not raster.crs:
        raise ValueError("O raster não contém SRC.")
    if not 1 <= banda <= raster.count or np.dtype(raster.dtypes[banda - 1]).kind not in ("u", "i"):
        raise ValueError("Selecione uma banda categórica de valores inteiros existente.")
    if raster.nodatavals[banda - 1] in classes:
        raise ValueError("Os códigos de garimpo não podem incluir o valor NoData.")
    return {
        "crs": raster.crs.to_wkt(), "transformacao": list(raster.transform),
        "largura": raster.width, "altura": raster.height,
    }


def janelas(raster):
    for y in range(0, raster.height, TAMANHO_JANELA):
        for x in range(0, raster.width, TAMANHO_JANELA):
            yield Window(x, y, min(TAMANHO_JANELA, raster.width - x), min(TAMANHO_JANELA, raster.height - y))


def ler_janela(raster, banda, classes, janela, fiscalizar_legenda):
    if raster.mask_flag_enums[banda - 1] == [MaskFlags.nodata]:
        dados = raster.read(banda, window=janela)
        validos = dados != raster.nodatavals[banda - 1]
    else:
        mosaico = raster.read(banda, window=janela, masked=True)
        dados = mosaico.data
        validos = ~np.ma.getmaskarray(mosaico)
    total = int(np.count_nonzero(validos))
    histograma = {}
    if total:
        faixa = dados if total == dados.size else dados[validos]
        minimo, maximo = int(faixa.min()), int(faixa.max())
        if minimo == maximo:
            histograma = {str(minimo): total}
        else:
            valores, frequencias = np.unique(faixa, return_counts=True)
            histograma = {str(int(v)): int(n) for v, n in zip(valores, frequencias)}
    presentes = [int(c) for c in histograma]
    codigos_alvo = [c for c in presentes if c in classes]
    alvo = validos & np.isin(dados, codigos_alvo) if codigos_alvo else np.zeros_like(validos)
    if fiscalizar_legenda:
        codigos = sorted(c for c in presentes if 1100 <= c <= 1299 and c not in classes)
        if codigos:
            raise ValueError(f"Códigos da família Garimpo ausentes da legenda selecionada: {codigos}.")
    return (
        np.packbits(validos, axis=None).tobytes(), total,
        sum(histograma[str(c)] for c in codigos_alvo), dados, alvo, histograma,
    )


def area_pixel_por_linha(raster):
    """Hectares por pixel para grades retangulares no cálculo equivalente EPSG:6933."""
    afim = raster.transform
    if afim.b or afim.d:
        return None
    epsg = raster.crs.to_epsg()
    if epsg == 6933:
        return np.full(raster.height, abs(afim.a * afim.e) / 10_000)
    if epsg not in (4326, 4674):
        return None
    transformar = transformador(raster.crs.to_string(), "EPSG:6933").transform
    oeste, leste = transformar([afim.c, afim.c + afim.a], [0, 0])[0]
    bordas = afim.f + np.arange(raster.height + 1) * afim.e
    _, y = transformar(np.full_like(bordas, afim.c), bordas)
    return abs(leste - oeste) * np.abs(np.diff(y)) / 10_000


def vetorizar_janela(raster, janela, dados, alvo):
    if not alvo.any():
        return
    valores = np.where(alvo, dados, 0).astype("int32")
    for bruto, classe in shapes(valores, mask=alvo, transform=raster.window_transform(janela)):
        geometria = reprojetar(shape(bruto), raster.crs.to_string())
        geometria = normalizar(geometria, "Polygon")
        validar_limites(geometria)
        yield int(classe), geometria.wkb


def pasta_de_retomada(destino, assinatura, grade, banda, classes, fiscalizar_legenda, inventario):
    configuracao = {
        "formato": 2, "raster_sha256": assinatura, "grade": grade, "banda": banda, "classes": classes,
        "tamanho_janela": TAMANHO_JANELA, "fiscalizar_legenda": fiscalizar_legenda,
    }
    chave = hashlib.sha256(json.dumps(configuracao, sort_keys=True).encode()).hexdigest()
    pasta = Path(destino) / chave
    pasta.mkdir(parents=True, exist_ok=True)
    origem = pasta / "origem.json"
    if not origem.exists():
        publicar_arquivo(origem, lambda f: f.write(json.dumps(
            {**configuracao, "arquivos": inventario}, ensure_ascii=False, indent=2
        ).encode("utf-8")))
    REGISTRO.info("Retomada por blocos em %s.", pasta)
    return pasta


def publicar_arquivo(destino, escrever):
    with NamedTemporaryFile(dir=destino.parent, suffix=".parcial", delete=False) as temporario:
        caminho = Path(temporario.name)
        try:
            escrever(temporario)
            temporario.close()
            caminho.replace(destino)
        finally:
            temporario.close()
            caminho.unlink(missing_ok=True)


def gravar_bloco(caminho, mascara, validos, detectados, histograma, area, geometrias):
    def escrever(fluxo):
        with gzip.GzipFile(fileobj=fluxo, mode="wb", compresslevel=3, mtime=0) as bloco:
            cabecalho = [b64encode(mascara).decode(), validos, detectados, histograma, area]
            bloco.write(json.dumps(cabecalho).encode() + b"\n")
            for classe, geometria in geometrias:
                bloco.write(f"{classe}\t{geometria.hex()}\n".encode("ascii"))

    publicar_arquivo(caminho, escrever)


def consumir_bloco(caminho, emitir=None):
    with gzip.open(caminho, "rb") as fluxo:
        codificada, validos, detectados, histograma, area = json.loads(fluxo.readline())
        mascara = b64decode(codificada, validate=True)
        geometrias = bytes_wkb = 0
        for linha in fluxo:
            codigo, wkb = linha.split(b"\t")
            if emitir is not None:
                emitir((int(codigo), bytes.fromhex(wkb.decode("ascii"))))
            geometrias += 1
            bytes_wkb += len(wkb) // 2
    return mascara, validos, detectados, geometrias, histograma, area, bytes_wkb


def varrer(raster, banda, classes, grade, *, emitir=None, pasta=None, fiscalizar_legenda=False):
    assinatura = hashlib.sha256(json.dumps(grade, sort_keys=True).encode())
    contagens = {"pixels_validos": 0, "pixels_detectados": 0, "poligonos": 0, "blocos": 0, "bytes_wkb": 0}
    histograma_total = Counter()
    peso_linhas = area_pixel_por_linha(raster)
    area_total = 0.0 if peso_linhas is not None else None
    reaproveitados = 0
    for indice, janela in enumerate(janelas(raster)):
        dados_bloco = None
        if pasta is not None:
            caminho = pasta / f"{indice:06d}.gz"
            if caminho.is_file():
                try:
                    dados_bloco = consumir_bloco(caminho)
                    reaproveitados += 1
                except (OSError, EOFError, ValueError, TypeError):
                    REGISTRO.warning("Bloco %s incompleto ou inválido. Reprocessando.", indice + 1)
        if dados_bloco is None:
            mascara, validos, detectados, dados, alvo, histograma = ler_janela(
                raster, banda, classes, janela, fiscalizar_legenda,
            )
            area = None
            if peso_linhas is not None:
                linha = int(janela.row_off)
                area = float(np.dot(np.count_nonzero(alvo, axis=1), peso_linhas[linha:linha + int(janela.height)])) if detectados else 0.0
            geometrias = vetorizar_janela(raster, janela, dados, alvo)
            if pasta is not None:
                gravar_bloco(caminho, mascara, validos, detectados, histograma, area, geometrias)
                dados_bloco = consumir_bloco(caminho)
            else:
                poligonos = bytes_wkb = 0
                for linha in geometrias:
                    emitir(linha)
                    poligonos += 1
                    bytes_wkb += len(linha[1])
                dados_bloco = mascara, validos, detectados, poligonos, histograma, area, bytes_wkb
        mascara, validos, detectados, poligonos, histograma, area, bytes_wkb = dados_bloco
        assinatura.update(mascara)
        contagens["blocos"] += 1
        contagens["pixels_validos"] += validos
        contagens["pixels_detectados"] += detectados
        contagens["poligonos"] += poligonos
        contagens["bytes_wkb"] += bytes_wkb
        histograma_total.update(histograma)
        if area_total is not None:
            area_total += area
        if (indice + 1) % 500 == 0:
            REGISTRO.info("%s blocos conferidos, %s retomados.", indice + 1, reaproveitados)
    if not contagens["pixels_validos"]:
        raise ValueError("A banda não tem pixels válidos.")
    REGISTRO.info("%s blocos completos, %s retomados, %s polígonos.",
                  contagens["blocos"], reaproveitados, contagens["poligonos"])
    contagens["area_raster_ha"] = area_total
    contagens["pixels_sem_dado"] = raster.width * raster.height - contagens["pixels_validos"]
    contagens["pixels_por_codigo"] = dict(histograma_total)
    return assinatura.hexdigest(), contagens
