-- Seleção das novas manchas materializadas. Os filtros territoriais selecionam por interseção.
WITH janela AS NOT MATERIALIZED (
    SELECT ST_Transform(ST_MakeEnvelope(%(oeste)s, %(sul)s, %(leste)s, %(norte)s, 4326), 4674) AS geom
), selecionadas AS MATERIALIZED (
    SELECT m.id, m.ano, m.area_ha, m.geom
    FROM manchas_indigenas m CROSS JOIN janela j
    WHERE m.tipo = 'nova' AND m.ano = %(ano)s AND m.id > %(apos)s
      AND m.geom && j.geom AND ST_Intersects(m.geom, j.geom)
      AND (%(terra)s::bigint IS NULL OR EXISTS (
        SELECT 1 FROM terras_indigenas t WHERE t.id = %(terra)s AND ST_Intersects(t.geom, m.geom)))
      AND (%(uf)s::text IS NULL OR EXISTS (
        SELECT 1 FROM unidades_federativas u WHERE u.sigla = %(uf)s AND ST_Intersects(u.geom, m.geom)))
    ORDER BY m.id LIMIT %(limite)s
)
SELECT id, ano, area_ha, ST_AsGeoJSON(ST_ForcePolygonCCW(ST_Transform(geom, 4326)), 9)::json AS geometria
FROM selecionadas ORDER BY id;
