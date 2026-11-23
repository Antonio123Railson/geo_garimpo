-- Executar offline. No pgAdmin, use SET geogarimpo.ano = '2025' para escolher um ano.
WITH parametros AS (SELECT coalesce(nullif(current_setting('geogarimpo.ano',true),''),'2025')::integer AS ano),
partes AS (
    SELECT c.i, c.j, ST_CollectionExtract(ST_Intersection(g.geom,c.geom),3) AS geom
    FROM garimpos_anuais g JOIN parametros USING (ano)
    CROSS JOIN LATERAL ST_SquareGrid(0.25,g.geom) AS c
    WHERE g.geom && c.geom AND ST_Intersects(g.geom,c.geom)
), celulas AS MATERIALIZED (
    SELECT i,j,ST_UnaryUnion(ST_Collect(geom)) AS geom FROM partes
    WHERE NOT ST_IsEmpty(geom) GROUP BY i,j
), recortes AS (
    SELECT g.i,g.j,u.sigla,ST_CollectionExtract(ST_Intersection(g.geom,u.geom),3) AS geom
    FROM celulas g JOIN unidades_federativas u ON g.geom && u.geom AND ST_Intersects(g.geom,u.geom)
), area_uf AS (
    SELECT sigla,sum(ST_Area(ST_Transform(geom,6933)))/10000 AS area_ha
    FROM recortes WHERE NOT ST_IsEmpty(geom) GROUP BY sigla
)
SELECT p.ano,'Brasil'::text AS territorio,coalesce(sum(ST_Area(ST_Transform(g.geom,6933))),0)/10000 AS area_ha
FROM parametros p JOIN anos_garimpo USING (ano) LEFT JOIN celulas g ON true GROUP BY p.ano
UNION ALL
SELECT p.ano,u.sigla,coalesce(a.area_ha,0) FROM parametros p JOIN anos_garimpo USING (ano)
CROSS JOIN unidades_federativas u LEFT JOIN area_uf a USING (sigla)
ORDER BY territorio;
