import hashlib

import pytest

from processamento import mapbiomas_google


def test_inventario_nao_trata_pasta_ausente_como_lote_completo():
    previstas = {(0, 0), (0, 16384)}
    assert mapbiomas_google.inventario_drive(None, None, 1985, previstas) == ({}, previstas)


def test_inventario_rejeita_conteudos_diferentes_na_mesma_posicao(monkeypatch):
    nome = "geogarimpo_mapbiomas_c11_v1_1985-0000000000-0000000000.tif"
    arquivos = [dict(id=identificador, name=nome, size="100", md5Checksum=digest * 32)
                for identificador, digest in (("primeiro", "a"), ("segundo", "b"))]
    monkeypatch.setattr(mapbiomas_google, "arquivos_drive", lambda *args: iter(arquivos))
    with pytest.raises(ValueError, match="mesma posição"):
        mapbiomas_google.inventario_drive(None, "pasta", 1985, {(0, 0)})


def test_retomada_por_range_verifica_md5_antes_de_publicar_arquivo(tmp_path):
    nome = "geogarimpo_mapbiomas_c11_v1_1985-0000000000-0000000000.tif"
    conteudo = b"bloco usado para testar a transferencia"
    digest = hashlib.md5(conteudo, usedforsecurity=False).hexdigest()
    item = dict(id="arquivo_drive", name=nome, size=str(len(conteudo)), md5Checksum=digest)
    parcial = tmp_path / f"{nome}.{digest}.parcial"
    parcial.write_bytes(conteudo[:8])

    class Resposta:
        status_code = 206
        headers = {"Content-Range": f"bytes 8-{len(conteudo)-1}/{len(conteudo)}"}

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def iter_content(self, chunk_size):
            yield conteudo[8:]

    class Sessao:
        def get(self, url, **opcoes):
            assert opcoes["headers"]["Range"] == "bytes=8-"
            assert opcoes["params"] == {"alt": "media"}
            return Resposta()

    destino = mapbiomas_google.obter_tiff(Sessao(), tmp_path, item)
    assert destino.read_bytes() == conteudo
    assert not parcial.exists()
    assert mapbiomas_google.obter_tiff(None, tmp_path, item) == destino
    destino.write_bytes(conteudo[::-1])
    with pytest.raises(ValueError, match="foi preservado"):
        mapbiomas_google.obter_tiff(None, tmp_path, item)
    assert destino.read_bytes() == conteudo[::-1]
