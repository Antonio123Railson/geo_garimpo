CREATE TEMP TABLE grade_atual ON COMMIT DROP AS
WITH partes AS (
    SELECT celula.i, celula.j, ST_CollectionExtract(ST_Intersection(g.geom, celula.geom), 3) AS geom
    FROM garimpos_anuais g
    CROSS JOIN LATERAL ST_SquareGrid(0.25, g.geom) AS celula
    WHERE g.ano = %s AND ST_Intersects(g.geom, celula.geom)
)
SELECT i,j,ST_UnaryUnion(ST_Collect(geom)) AS geom
FROM partes WHERE NOT ST_IsEmpty(geom)
GROUP BY i,j;
