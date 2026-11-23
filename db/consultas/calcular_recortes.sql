CREATE TEMP TABLE recortes_atual ON COMMIT DROP AS
WITH por_terra AS MATERIALIZED (
    SELECT g.i,g.j,t.id AS terra_id,ST_UnaryUnion(ST_Collect(
               ST_CollectionExtract(ST_Intersection(g.geom,t.geom),3)
           )) AS geom
    FROM grade_atual g JOIN partes_terra t ON ST_Intersects(g.geom,t.geom)
    GROUP BY g.i,g.j,t.id
), nacional AS MATERIALIZED (
    SELECT i,j,ST_UnaryUnion(ST_Collect(geom)) AS geom
    FROM por_terra WHERE NOT ST_IsEmpty(geom) GROUP BY i,j
), por_uf AS (
    SELECT n.i,n.j,u.sigla AS uf,ST_UnaryUnion(ST_Collect(
               ST_CollectionExtract(ST_Intersection(n.geom,u.geom),3)
           )) AS geom
    FROM nacional n JOIN partes_uf u ON ST_Intersects(n.geom,u.geom)
    GROUP BY n.i,n.j,u.sigla
), recortes AS (
    SELECT i,j,'terra'::text AS escopo,terra_id,NULL::text AS uf,geom FROM por_terra
    UNION ALL
    SELECT i,j,'brasil',NULL,NULL,geom FROM nacional
    UNION ALL
    SELECT i,j,'uf',NULL,uf,geom FROM por_uf
)
SELECT r.escopo,r.terra_id,r.uf,r.geom,
    CASE WHEN NOT %s THEN NULL
         WHEN a.geom IS NULL THEN r.geom
         ELSE ST_CollectionExtract(ST_Difference(r.geom,a.geom),3) END AS nova
FROM recortes r LEFT JOIN grade_anterior a USING (i,j)
WHERE NOT ST_IsEmpty(r.geom);
