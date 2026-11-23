import hashlib
import json
from contextlib import ExitStack
from pathlib import Path
from tempfile import NamedTemporaryFile, mkdtemp

import numpy as np
import pyarrow as pa
import pyarrow.parquet as pq
import shapely
from psycopg import sql

from db.conexao import conectar
from processamento.carga import ativar_base, bloquear_escritas, registrar_fonte
from processamento.validar_dados import normalizar, transformador

TABELAS = {"rios": "ana", "estradas": "osm", "pistas_pouso": "osm"}
CAMPOS = pa.schema([
    ("codigo", pa.string()), ("nome", pa.string()), ("classe", pa.string()),
    ("wkb", pa.binary()), ("assinatura", pa.binary(16)), ("preferencia", pa.int16()),
])
VERSAO_PROCESSAMENTO = 1


def gravar_json(caminho, valor):
    with NamedTemporaryFile(dir=caminho.parent, suffix=".parcial", delete=False) as arquivo:
        temporario = Path(arquivo.name)
        arquivo.write(json.dumps(valor, ensure_ascii=False, indent=2).encode("utf-8"))
    try:
        temporario.replace(caminho)
    finally:
        temporario.unlink(missing_ok=True)


def mascara_nacional():
    with conectar(escrita=True) as conexao:
        conexao.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY")
        linhas = conexao.execute("SELECT ST_AsBinary(geom) AS wkb FROM unidades_federativas ORDER BY sigla").fetchall()
    if len(linhas) != 27:
        raise ValueError("A malha nacional deve conter as 27 UFs para aplicar o recorte.")
    geometrias = [bytes(linha["wkb"]) for linha in linhas]
    assinatura = hashlib.sha256(b"".join(geometrias)).hexdigest()
    mascara = shapely.union_all(shapely.from_wkb(geometrias))
    if mascara.is_empty or not mascara.is_valid:
        raise ValueError("A união das UFs não produziu uma máscara válida.")
    shapely.prepare(mascara)
    return mascara, assinatura


def gravar_lote(pasta, numero, codigos, nomes, classes, geometrias, origem, mascara, tipo, contagens):
    geometrias = shapely.force_2d(geometrias)
    if origem.upper() != "EPSG:4674":
        geometrias = shapely.transform(geometrias, transformador(origem, "EPSG:4674").transform, interleaved=False)
    tipos = shapely.get_type_id(geometrias)
    contagens["lidas"] = len(codigos)
    mascara_valida = ~shapely.is_missing(geometrias) & ~shapely.is_empty(geometrias)
    contagens["vazias"] = int((~mascara_valida).sum())
    linhas = mascara_valida & (tipos == 1)
    if linhas.any():
        geometrias[linhas] = shapely.multilinestrings(geometrias[linhas].reshape(-1, 1))
    aceitas = (5,) if tipo == "LineString" else (0, 4, 5, 6)
    revisar = mascara_valida & (~shapely.is_valid(geometrias) | ~np.isin(shapely.get_type_id(geometrias), aceitas))
    for indice in np.flatnonzero(revisar):
        try:
            geometrias[indice] = normalizar(geometrias[indice], tipo, contagens)
        except ValueError:
            mascara_valida[indice] = False
            contagens["irrecuperaveis"] = contagens.get("irrecuperaveis", 0) + 1
    candidatas = np.flatnonzero(mascara_valida)
    incluidas = candidatas[shapely.intersects(mascara, geometrias[candidatas])]
    contagens["fora_do_recorte"] = len(candidatas) - len(incluidas)
    contagens["preparadas"] = len(incluidas)
    geometrias = geometrias[incluidas]
    formas = shapely.to_wkb(geometrias, byte_order=1, output_dimension=2)
    normalizadas = shapely.to_wkb(shapely.normalize(geometrias), byte_order=1, output_dimension=2)
    tabela = pa.Table.from_arrays([
        pa.array([codigos[i] for i in incluidas], pa.string()),
        pa.array([nomes[i] for i in incluidas], pa.string()),
        pa.array([classes[i] for i in incluidas], pa.string()),
        pa.array(formas, pa.binary()),
        pa.array([hashlib.blake2b(forma, digest_size=16).digest() for forma in normalizadas], pa.binary(16)),
        pa.array(shapely.get_dimensions(geometrias), pa.int16()),
    ], schema=CAMPOS).replace_schema_metadata({b"contagens": json.dumps(contagens).encode()})
    caminho = pasta / f"{numero:06d}.parquet"
    with NamedTemporaryFile(dir=pasta, suffix=".parcial", delete=False) as arquivo:
        temporario = Path(arquivo.name)
    try:
        pq.write_table(tabela, temporario, compression="zstd")
        temporario.replace(caminho)
    finally:
        temporario.unlink(missing_ok=True)


def verificar_publicacao(tabela, assinatura):
    with conectar() as conexao:
        linha = conexao.execute("""
            SELECT f.sha256, f.metadados FROM bases_ativas b
            JOIN fontes_dados f ON f.id = b.fonte_id WHERE b.camada = %s
        """, (tabela,)).fetchone()
    if linha and linha["sha256"] != assinatura:
        raise ValueError(f"A camada {tabela} já tem uma aquisição ativa diferente.")
    return linha


def lotes_parquet(caminhos, colunas=None):
    for caminho in caminhos:
        yield from pq.ParquetFile(caminho).iter_batches(batch_size=20_000, columns=colunas)


def selecionar_unicas(tabela, caminhos, quantidade, classes_selecionadas=None):
    """Ordena apenas as chaves em memória local. As coordenadas ficam nos Parquet."""
    if quantidade <= 0 or not caminhos:
        raise ValueError(f"Nenhuma feição preparada em {tabela}.")
    identificadores = np.empty(quantidade, dtype=np.int64)
    preferencias = np.empty(quantidade, dtype=np.int8)
    chaves = np.empty(quantidade, dtype=[("classe", np.uint8), ("assinatura", "V16")])
    classes = {}
    prefixos = {"n": 0, "w": 1, "r": 2}
    inicio = 0
    for lote in lotes_parquet(caminhos, ["codigo", "classe", "assinatura", "preferencia"]):
        fim = inicio + lote.num_rows
        codigos = lote["codigo"].to_pylist()
        if tabela == "rios":
            identificadores[inicio:fim] = [int(codigo) if not codigo.startswith("fid:") else -int(codigo[4:])
                                           for codigo in codigos]
        else:
            identificadores[inicio:fim] = [int(codigo[5:]) * 4 + prefixos[codigo[4]] for codigo in codigos]
        preferencias[inicio:fim] = lote["preferencia"].to_numpy()
        chaves["classe"][inicio:fim] = [classes.setdefault(classe, len(classes)) for classe in lote["classe"].to_pylist()]
        assinaturas = lote["assinatura"]
        chaves["assinatura"][inicio:fim] = np.frombuffer(assinaturas.buffers()[1], dtype="V16",
                                                       count=lote.num_rows, offset=assinaturas.offset * 16)
        inicio = fim
    if inicio != quantidade:
        raise ValueError(f"A quantidade de feições preparadas diverge em {tabela}.")
    if classes_selecionadas is None:
        elegiveis = np.arange(quantidade)
    else:
        elegiveis = np.flatnonzero(np.isin(chaves["classe"], [classes[c] for c in classes_selecionadas if c in classes]))
    if not len(elegiveis):
        raise ValueError(f"Nenhuma feição elegível em {tabela} para as classes selecionadas.")
    por_codigo = elegiveis[np.lexsort((-preferencias[elegiveis], identificadores[elegiveis]))]
    codigos_ordenados = identificadores[por_codigo]
    por_codigo = np.sort(por_codigo[np.r_[True, codigos_ordenados[1:] != codigos_ordenados[:-1]]])
    chaves = chaves[por_codigo]
    ordem = np.argsort(chaves, order=("classe", "assinatura"), kind="stable")
    ordenadas = chaves[ordem]
    mascara = np.zeros(quantidade, dtype=bool)
    mascara[por_codigo[ordem[np.r_[True, ordenadas[1:] != ordenadas[:-1]]]]] = True
    return mascara, len(elegiveis)


def ordenar_para_copia(pasta, detalhes, tabela):
    classes = detalhes.get("classes") if tabela == "estradas" else None
    chave = hashlib.sha256(json.dumps(sorted(classes) if classes else None).encode()).hexdigest()[:16]
    destino = pasta / f"ordem-espacial-1-{chave}"
    if destino.is_dir():
        resumo = json.loads((destino / "contagens.json").read_text(encoding="utf-8"))
        return sorted(destino.glob("*.parquet")), resumo
    caminhos = sorted(pasta.glob("*.parquet"))
    print(f"{tabela}: deduplicando no workspace e agrupando os trechos por vizinhança.", flush=True)
    unicas, elegiveis = selecionar_unicas(tabela, caminhos, detalhes["preparadas"], classes)
    resumo = dict(inseridas=int(unicas.sum()), duplicadas=elegiveis - int(unicas.sum()),
                  excluidas_pela_selecao=detalhes["preparadas"] - elegiveis)
    pasta_temporaria = Path(mkdtemp(prefix="ordem-parcial-", dir=pasta))
    inicio = 0
    escritores = {}
    with ExitStack() as abertos:
        for lote in lotes_parquet(caminhos, ["codigo", "nome", "classe", "wkb"]):
            fim = inicio + lote.num_rows
            lote = lote.filter(pa.array(unicas[inicio:fim])).replace_schema_metadata(None)
            inicio = fim
            if not lote.num_rows:
                continue
            caixas = shapely.bounds(shapely.from_wkb(lote["wkb"].to_pylist()))
            coluna = np.floor((caixas[:, 0] + caixas[:, 2] + 360) / 5).astype(int)
            linha = np.floor((caixas[:, 1] + caixas[:, 3] + 180) / 5).astype(int)
            celulas = linha * 144 + coluna
            ordem = np.argsort(celulas, kind="stable")
            agrupadas = celulas[ordem]
            cortes = np.r_[0, np.flatnonzero(agrupadas[1:] != agrupadas[:-1]) + 1, len(ordem)]
            for a, b in zip(cortes[:-1], cortes[1:]):
                celula = agrupadas[a]
                if celula not in escritores:
                    escritores[celula] = abertos.enter_context(pq.ParquetWriter(pasta_temporaria / f"{celula:05d}.parquet", lote.schema,
                                                                                compression="zstd"))
                escritores[celula].write_batch(lote.take(pa.array(ordem[a:b])))
    gravar_json(pasta_temporaria / "contagens.json", resumo)
    try:
        pasta_temporaria.rename(destino)
    except FileExistsError:
        resumo = json.loads((destino / "contagens.json").read_text(encoding="utf-8"))
    print(f"{tabela}: {resumo['inseridas']:,} feições únicas prontas para publicação.", flush=True)
    return sorted(destino.glob("*.parquet")), resumo


def publicar(tabela, manifesto, assinatura, detalhes, pasta):
    identificador = sql.Identifier(tabela)
    classe = sql.SQL("") if tabela == "rios" else sql.SQL(",classe")
    ativa = verificar_publicacao(tabela, assinatura)
    resumo = None
    if not ativa:
        caminhos, resumo = ordenar_para_copia(pasta, detalhes, tabela)
    with conectar(escrita=True) as conexao:
        bloquear_escritas(conexao)
        ativa = conexao.execute("""
            SELECT f.id,f.sha256,f.metadados FROM bases_ativas b
            JOIN fontes_dados f ON f.id = b.fonte_id WHERE b.camada = %s
        """, (tabela,)).fetchone()
        if ativa:
            if ativa["sha256"] != assinatura or ativa["metadados"].get("assinatura_recorte") != detalhes["assinatura_recorte"]:
                raise ValueError(f"A camada {tabela} contém outra aquisição ou outro recorte.")
            if tabela == "estradas" and set(ativa["metadados"].get("classes", [])) != set(detalhes["classes"]):
                raise ValueError("A camada de estradas está publicada com outra seleção de classes.")
            total = conexao.execute(sql.SQL("SELECT count(*) AS n FROM {} WHERE fonte_id = %s").format(identificador),
                                    (ativa["id"],)).fetchone()["n"]
            if total != ativa["metadados"].get("inseridas"):
                raise ValueError(f"A camada {tabela} diverge da quantidade originalmente publicada.")
            print(f"{tabela}: {total:,} feições já publicadas com a mesma aquisição.", flush=True)
            return total
        if resumo is None:
            raise ValueError(f"A aquisição ativa para {tabela} mudou durante a verificação. Repita a carga.")
        if conexao.execute(sql.SQL("SELECT EXISTS (SELECT 1 FROM {}) AS existe").format(identificador)).fetchone()["existe"]:
            raise ValueError(f"A camada {tabela} contém registros anteriores. A carga preservou esses dados.")
        compressao = conexao.execute("""
            SELECT 'lz4' = ANY(enumvals) AND has_parameter_privilege('wal_compression','SET') AS permitida
            FROM pg_settings WHERE name = 'wal_compression'
        """).fetchone()
        if compressao and compressao["permitida"]:
            conexao.execute("SET LOCAL wal_compression = 'lz4'")
        detalhes = dict(detalhes, **resumo)
        fonte = registrar_fonte(conexao, TABELAS[tabela], manifesto, assinatura, detalhes)
        comando = sql.SQL("COPY {} (codigo,nome,fonte_id,geom{}) FROM STDIN").format(identificador, classe)
        resultado = 0
        cursor = conexao.cursor()
        with cursor.copy(comando) as copia:
            for numero, lote in enumerate(lotes_parquet(caminhos, ["codigo", "nome", "classe", "wkb"])):
                formas = shapely.set_srid(shapely.from_wkb(lote["wkb"].to_pylist()), 4674)
                formas = shapely.to_wkb(formas, hex=True, include_srid=True, output_dimension=2)
                for codigo, nome, forma, atributo in zip(lote["codigo"].to_pylist(), lote["nome"].to_pylist(),
                                                         formas, lote["classe"].to_pylist()):
                    valores = (codigo, nome, fonte, forma)
                    copia.write_row(valores if tabela == "rios" else (*valores, atributo))
                resultado += lote.num_rows
                if numero % 10 == 0:
                    print(f"{tabela}: {resultado:,} feições enviadas pelo cliente COPY.", flush=True)
        if resultado != resumo["inseridas"] or resultado != cursor.rowcount:
            raise ValueError(f"A publicação de {tabela} diverge da contagem validada.")
        ativar_base(conexao, tabela, fonte)
        conexao.execute(sql.SQL("ANALYZE {}").format(identificador))
    print(f"{tabela}: {resultado:,} feições publicadas.", flush=True)
    return resultado
