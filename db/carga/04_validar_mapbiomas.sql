-- Requer apenas leitura das tabelas definitivas. A grade e os recortes são temporários.
BEGIN ISOLATION LEVEL REPEATABLE READ;

SELECT count(*) AS anos_importados, min(ano) AS primeiro_ano, max(ano) AS ultimo_ano
FROM public.anos_garimpo;

SELECT a.ano, a.classes, a.assinatura_grade, a.fonte_id, f.colecao, f.versao, f.sha256,
       (f.metadados->>'area_raster_ha')::double precision AS area_raster_ha,
       count(g.id) AS poligonos,
       count(g.id) - count(DISTINCT md5(ST_AsEWKB(g.geom))) AS geometrias_binarias_repetidas,
       array_agg(DISTINCT ST_SRID(g.geom)) FILTER (WHERE g.id IS NOT NULL) AS srids,
       count(g.id) FILTER (WHERE NOT ST_IsValid(g.geom) OR ST_IsEmpty(g.geom)) AS invalidas_ou_vazias,
       count(g.id) FILTER (WHERE ST_SRID(g.geom) <> 4674 OR GeometryType(g.geom) <> 'MULTIPOLYGON')
           AS tipo_ou_srid_incorreto,
       count(g.id) FILTER (WHERE g.fonte_id IS DISTINCT FROM f.id OR f.tipo IS DISTINCT FROM 'mapbiomas')
           AS referencias_incorretas,
       count(g.id) FILTER (WHERE NOT (g.classe = ANY(a.classes))) AS classes_fora_da_selecao
FROM public.anos_garimpo a
LEFT JOIN public.fontes_dados f ON f.id = a.fonte_id
LEFT JOIN public.garimpos_anuais g ON g.ano = a.ano
GROUP BY a.ano, f.id
ORDER BY a.ano;

SELECT classe, count(*) AS poligonos_na_classe
FROM public.garimpos_anuais
WHERE ano = 2025
GROUP BY classe ORDER BY classe;

SELECT (SELECT count(*) FROM public.unidades_federativas) AS unidades_federativas,
       (SELECT count(*) FROM public.terras_indigenas) AS terras_indigenas,
       (SELECT count(*) FROM public.manchas_indigenas) AS manchas_historicas_materializadas,
       (SELECT count(*) FROM public.estatisticas_anuais) AS estatisticas_materializadas;

SELECT f.metadados->>'papel' AS papel, count(*) AS partes_inventario,
       sum(jsonb_array_length(f.metadados->'arquivos')) AS hashes_arquivos
FROM public.fontes_dados f JOIN public.anos_garimpo a
  ON f.metadados->>'fonte_anual_id' = a.fonte_id::text
WHERE a.ano = 2025 AND f.metadados->>'papel' = 'inventario_raster'
GROUP BY f.metadados->>'papel';

-- Área nacional deduplicada do piloto. A união é feita em células disjuntas
-- para limitar a memória. A área projetada é calculada em metros quadrados.
CREATE TEMP TABLE grade_validacao_mapbiomas ON COMMIT DROP AS
WITH partes AS (
    SELECT celula.i, celula.j,
           CASE WHEN ST_CoveredBy(g.geom, celula.geom) THEN g.geom
                ELSE ST_CollectionExtract(ST_Intersection(g.geom, celula.geom), 3) END AS geom
    FROM public.garimpos_anuais g
    CROSS JOIN LATERAL ST_SquareGrid(0.25, g.geom) AS celula
    WHERE g.ano = 2025 AND ST_Intersects(g.geom, celula.geom)
)
SELECT i, j, ST_UnaryUnion(ST_Collect(geom)) AS geom
FROM partes WHERE NOT ST_IsEmpty(geom)
GROUP BY i, j;

CREATE INDEX ON grade_validacao_mapbiomas USING gist (geom);
ANALYZE grade_validacao_mapbiomas;

WITH medidas AS (
    SELECT sum(ST_Area(ST_Transform(geom, 6933))) / 10000 AS hectares
    FROM grade_validacao_mapbiomas
), soma_poligonos AS (
    SELECT sum(ST_Area(ST_Transform(geom, 6933))) / 10000 AS hectares
    FROM public.garimpos_anuais WHERE ano = 2025
)
SELECT a.ano, coalesce(m.hectares, 0) AS hectares_garimpo_mapeado,
       coalesce(s.hectares, 0) AS hectares_soma_poligonos,
       coalesce(s.hectares, 0) - coalesce(m.hectares, 0) AS diferenca_soma_e_uniao_ha,
       coalesce(m.hectares, 0) - (f.metadados->>'area_raster_ha')::double precision AS diferenca_vetor_e_raster_ha
FROM public.anos_garimpo a JOIN public.fontes_dados f ON f.id = a.fonte_id
CROSS JOIN medidas m CROSS JOIN soma_poligonos s WHERE a.ano = 2025;

-- As TIs podem se sobrepor. O total indígena une as interseções em cada célula.
CREATE TEMP TABLE recortes_ti_validacao ON COMMIT DROP AS
WITH partes_ti AS MATERIALIZED (
    SELECT t.id, ST_Subdivide(t.geom, 512) AS geom FROM public.terras_indigenas t
), intersecoes AS (
    SELECT g.i, g.j, t.id,
           ST_CollectionExtract(ST_Intersection(g.geom, t.geom), 3) AS geom
    FROM partes_ti t JOIN grade_validacao_mapbiomas g ON ST_Intersects(g.geom, t.geom)
)
SELECT i, j, id, ST_UnaryUnion(ST_Collect(geom)) AS geom
FROM intersecoes WHERE NOT ST_IsEmpty(geom)
GROUP BY i, j, id;

CREATE TEMP TABLE grade_ti_validacao ON COMMIT DROP AS
SELECT i, j, ST_UnaryUnion(ST_Collect(geom)) AS geom
FROM recortes_ti_validacao GROUP BY i, j;

CREATE INDEX ON grade_ti_validacao USING gist (geom);
ANALYZE grade_ti_validacao;

SELECT a.ano,
       (SELECT count(DISTINCT id) FROM recortes_ti_validacao) AS terras_com_intersecao,
       coalesce((SELECT sum(ST_Area(ST_Transform(geom, 6933))) / 10000 FROM grade_ti_validacao), 0)
           AS hectares_dentro_de_tis
FROM public.anos_garimpo a WHERE a.ano = 2025;

WITH partes_uf AS MATERIALIZED (
    SELECT u.sigla, ST_Subdivide(u.geom, 512) AS geom FROM public.unidades_federativas u
), intersecoes AS (
    SELECT u.sigla, g.i, g.j, 'total' AS escopo,
           ST_CollectionExtract(ST_Intersection(g.geom, u.geom), 3) AS geom
    FROM partes_uf u JOIN grade_validacao_mapbiomas g ON ST_Intersects(g.geom, u.geom)
    UNION ALL
    SELECT u.sigla, g.i, g.j, 'ti',
           ST_CollectionExtract(ST_Intersection(g.geom, u.geom), 3)
    FROM partes_uf u JOIN grade_ti_validacao g ON ST_Intersects(g.geom, u.geom)
), celulas AS (
    SELECT sigla, escopo, ST_UnaryUnion(ST_Collect(geom)) AS geom
    FROM intersecoes WHERE NOT ST_IsEmpty(geom)
    GROUP BY sigla, i, j, escopo
), areas AS (
    SELECT sigla,
           sum(ST_Area(ST_Transform(geom, 6933))) FILTER (WHERE escopo = 'total') / 10000 AS total,
           sum(ST_Area(ST_Transform(geom, 6933))) FILTER (WHERE escopo = 'ti') / 10000 AS em_ti
    FROM celulas GROUP BY sigla
)
SELECT u.sigla, u.nome, coalesce(r.total, 0) AS hectares_na_uf,
       coalesce(r.em_ti, 0) AS hectares_dentro_de_tis_na_uf
FROM public.unidades_federativas u LEFT JOIN areas r USING (sigla)
WHERE EXISTS (SELECT 1 FROM public.anos_garimpo WHERE ano = 2025)
ORDER BY hectares_na_uf DESC, u.sigla;

COMMIT;
