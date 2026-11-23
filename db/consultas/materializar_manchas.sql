WITH superficies AS (
    SELECT 'detectada'::text AS tipo, geom FROM recortes_atual WHERE escopo='brasil'
    UNION ALL
    SELECT 'nova', nova FROM recortes_atual WHERE escopo='brasil' AND NOT ST_IsEmpty(nova)
), partes AS (
    SELECT s.tipo, parte.geom
    FROM superficies s
    CROSS JOIN LATERAL ST_Subdivide(s.geom, 512) AS p(geom)
    CROSS JOIN LATERAL ST_Dump(ST_CollectionExtract(p.geom,3)) AS parte
), medidas AS (
    SELECT tipo,geom,ST_Area(ST_Transform(geom,6933))/10000 AS area_ha FROM partes
)
INSERT INTO manchas_indigenas (ano,tipo,area_ha,geom)
SELECT %s,tipo,area_ha,geom FROM medidas WHERE area_ha > 0;
