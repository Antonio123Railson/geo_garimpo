import math


def grade_compativel(grade, referencia):
    if not isinstance(grade, dict) or not isinstance(referencia, dict):
        return False
    if any(grade.get(campo) != referencia.get(campo) or campo not in grade
           for campo in ("crs", "largura", "altura", "nodata")):
        return False
    for campo, tamanho in (("transformacao", 9), ("resolucao", 2), ("bounds", 4)):
        atual, piloto = grade.get(campo), referencia.get(campo)
        if not isinstance(atual, list) or not isinstance(piloto, list) or len(atual) != tamanho or len(piloto) != tamanho:
            return False
        if any(not isinstance(x, (int, float)) or not isinstance(y, (int, float))
               or not math.isclose(x, y, rel_tol=0, abs_tol=1e-10) for x, y in zip(atual, piloto)):
            return False
    return True


def estado_ano(conexao, ano):
    registro = conexao.execute("""
        SELECT a.fonte_id, a.classes, f.tipo, f.colecao, f.versao, f.sha256, f.metadados,
               (SELECT count(*) FROM garimpos_anuais WHERE ano = a.ano) AS registros
        FROM anos_garimpo a JOIN fontes_dados f ON f.id = a.fonte_id WHERE a.ano = %s
    """, (ano,)).fetchone()
    if registro is None:
        return "ausente", None
    esperado = registro["metadados"].get("poligonos")
    validacao = registro["metadados"].get("validacao_publicacao") or {}
    total = registro["registros"]
    if type(esperado) is int and 0 <= total < esperado:
        return "incompleto", registro
    if total == esperado and validacao.get("poligonos") == total and validacao.get("aprovada") is True:
        return "validado", registro
    return "sem_validacao", registro


def conferir_origem(registro, manifesto, resumo, classes, legenda):
    meta = registro["metadados"]
    if (registro["tipo"] != "mapbiomas" or registro["colecao"] != manifesto["colecao"]
            or registro["versao"] != manifesto["versao"] or registro["classes"] != classes
            or meta.get("legenda") != legenda or meta.get("ativo") != manifesto["metadados"]["ativo"]
            or not grade_compativel(meta.get("grade"), resumo["grade"])):
        raise ValueError("O ano existente pertence a uma origem ou grade incompatível. Preserve a carga existente.")


def validar_publicacao(conexao, ano, fonte, classes, resumo):
    linha = conexao.execute("""
        SELECT count(*) AS poligonos,
               count(*) FILTER (WHERE NOT ST_IsValid(geom) OR ST_IsEmpty(geom)) AS invalidas_ou_vazias,
               count(*) FILTER (WHERE ST_SRID(geom) <> 4674 OR GeometryType(geom) <> 'MULTIPOLYGON') AS tipos_incorretos,
               count(*) FILTER (WHERE fonte_id <> %s OR NOT (classe = ANY(%s))) AS referencias_ou_classes_incorretas,
               count(*) - count(DISTINCT md5(ST_AsEWKB(ST_Normalize(geom)))) AS geometrias_repetidas,
               coalesce(sum(ST_Area(ST_Transform(geom,6933))) / 10000, 0) AS area_vetor_ha
        FROM garimpos_anuais WHERE ano = %s
    """, (fonte, classes, ano)).fetchone()
    falhas = ("invalidas_ou_vazias", "tipos_incorretos", "referencias_ou_classes_incorretas", "geometrias_repetidas")
    if linha["poligonos"] != resumo["poligonos"] or any(linha[campo] for campo in falhas):
        raise ValueError(f"A validação em PostGIS encontrou inconsistências no ano {ano}.")
    area_raster = resumo["area_raster_ha"]
    if area_raster is None or not math.isclose(area_raster, linha["area_vetor_ha"], rel_tol=1e-7, abs_tol=0.01):
        raise ValueError(f"A área vetorial do ano {ano} diverge da área dos pixels selecionados.")
    return dict(linha, aprovada=True)
