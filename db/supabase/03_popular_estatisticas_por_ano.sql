-- Execute para um ano publicado por vez, do primeiro para o último.
-- Para operações longas, use pgAdmin ou psql via conexão direta ou pooler de sessão.
BEGIN ISOLATION LEVEL REPEATABLE READ;
SET LOCAL search_path = pg_temp, public, extensions;
SET LOCAL lock_timeout = '3s';
SET LOCAL statement_timeout = '5min';
SELECT set_config('geogarimpo.ano_estatistico', '1985', true);

DO $calculo$
DECLARE
    referencia smallint := current_setting('geogarimpo.ano_estatistico')::smallint;
    fonte_atual bigint;
    fonte_anterior bigint;
    assinatura_atual text;
    assinatura_anterior text;
    comparavel boolean;
    total_territorios bigint;
    metadados jsonb;
BEGIN
    -- Mesma trava usada pelo importador para evitar cálculo durante uma carga do projeto.
    PERFORM pg_advisory_xact_lock(78120926);
    SELECT a.fonte_id, a.assinatura_grade INTO fonte_atual, assinatura_atual
    FROM public.anos_garimpo a WHERE a.ano = referencia;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'Ano % ausente em anos_garimpo.', referencia;
    END IF;
    SELECT a.fonte_id, a.assinatura_grade INTO fonte_anterior, assinatura_anterior
    FROM public.anos_garimpo a WHERE a.ano = referencia - 1;
    comparavel := assinatura_anterior IS NOT NULL AND assinatura_atual = assinatura_anterior;

    CREATE TEMP TABLE territorios_estatistica ON COMMIT DROP AS
    SELECT 'brasil'::text AS escopo, NULL::bigint AS terra_id, NULL::text AS uf
    UNION ALL SELECT 'terra', id, NULL FROM public.terras_indigenas
    UNION ALL SELECT 'uf', NULL, sigla FROM public.unidades_federativas;
    IF NOT EXISTS (SELECT 1 FROM territorios_estatistica WHERE escopo = 'terra')
       OR NOT EXISTS (SELECT 1 FROM territorios_estatistica WHERE escopo = 'uf') THEN
        RAISE EXCEPTION 'São necessárias as bases da FUNAI e do IBGE.';
    END IF;
    SELECT count(*) INTO total_territorios FROM territorios_estatistica;
    IF comparavel AND (
        SELECT count(*) FROM territorios_estatistica t
        JOIN public.estatisticas_anuais e ON e.ano = referencia - 1 AND e.escopo = t.escopo
           AND e.terra_id IS NOT DISTINCT FROM t.terra_id AND e.uf IS NOT DISTINCT FROM t.uf
        WHERE e.fontes->>'mapbiomas_ano' = fonte_anterior::text
    ) <> total_territorios THEN
        RAISE EXCEPTION 'Calcule primeiro as estatísticas completas de %.', referencia - 1;
    END IF;

    SELECT jsonb_build_object(
        'mapbiomas_ano', fonte_atual, 'mapbiomas_anterior', fonte_anterior,
        'mascaras_comparaveis', comparavel, 'projecao_area', 6933, 'grade_graus', 0.25,
        'bases', jsonb_build_object(
            'terras_indigenas', (SELECT jsonb_agg(DISTINCT fonte_id ORDER BY fonte_id) FROM public.terras_indigenas),
            'ufs', (SELECT jsonb_agg(DISTINCT fonte_id ORDER BY fonte_id) FROM public.unidades_federativas)
        )
    ) INTO metadados;

    CREATE TEMP TABLE partes_terra_estatistica ON COMMIT DROP AS
    SELECT t.id, parte.geom
    FROM public.terras_indigenas t CROSS JOIN LATERAL ST_Subdivide(t.geom, 512) AS parte(geom);
    CREATE INDEX ON partes_terra_estatistica USING gist (geom);
    ANALYZE partes_terra_estatistica;

    CREATE TEMP TABLE partes_uf_estatistica ON COMMIT DROP AS
    SELECT u.sigla, parte.geom
    FROM public.unidades_federativas u CROSS JOIN LATERAL ST_Subdivide(u.geom, 512) AS parte(geom);
    CREATE INDEX ON partes_uf_estatistica USING gist (geom);
    ANALYZE partes_uf_estatistica;

    -- A mesma grade de cálculo do processamento offline. Una os polígonos antes
    -- de medir a área para evitar duplicação entre classes ou terras sobrepostas.
    CREATE TEMP TABLE grade_estatistica ON COMMIT DROP AS
    WITH partes AS (
        SELECT g.ano, c.i, c.j, ST_CollectionExtract(ST_Intersection(g.geom, c.geom), 3) AS geom
        FROM public.garimpos_anuais g
        CROSS JOIN LATERAL ST_SquareGrid(0.25, g.geom) AS c
        WHERE g.ano IN (referencia, CASE WHEN comparavel THEN referencia - 1 END)
          AND ST_Intersects(g.geom, c.geom)
    )
    SELECT ano, i, j, ST_UnaryUnion(ST_Collect(geom)) AS geom
    FROM partes WHERE NOT ST_IsEmpty(geom)
    GROUP BY ano, i, j;
    CREATE UNIQUE INDEX ON grade_estatistica (ano, i, j);
    ANALYZE grade_estatistica;

    WITH por_terra AS MATERIALIZED (
        SELECT g.i, g.j, t.id AS terra_id, ST_UnaryUnion(ST_Collect(
            ST_CollectionExtract(ST_Intersection(g.geom, t.geom), 3)
        )) AS geom
        FROM grade_estatistica g JOIN partes_terra_estatistica t ON ST_Intersects(g.geom, t.geom)
        WHERE g.ano = referencia
        GROUP BY g.i, g.j, t.id
    ), nacional AS MATERIALIZED (
        SELECT i, j, ST_UnaryUnion(ST_Collect(geom)) AS geom
        FROM por_terra WHERE NOT ST_IsEmpty(geom)
        GROUP BY i, j
    ), por_uf AS (
        SELECT n.i, n.j, u.sigla, ST_UnaryUnion(ST_Collect(
            ST_CollectionExtract(ST_Intersection(n.geom, u.geom), 3)
        )) AS geom
        FROM nacional n JOIN partes_uf_estatistica u ON ST_Intersects(n.geom, u.geom)
        GROUP BY n.i, n.j, u.sigla
    ), recortes AS (
        SELECT i, j, 'terra'::text AS escopo, terra_id, NULL::text AS uf, geom FROM por_terra
        UNION ALL SELECT i, j, 'brasil', NULL, NULL, geom FROM nacional
        UNION ALL SELECT i, j, 'uf', NULL, sigla, geom FROM por_uf
    ), medidas AS (
        SELECT r.escopo, r.terra_id, r.uf,
            sum(ST_Area(ST_Transform(r.geom, 6933))) / 10000 AS detectada,
            sum(ST_Area(ST_Transform(CASE
                WHEN NOT comparavel THEN NULL
                WHEN a.geom IS NULL THEN r.geom
                ELSE ST_CollectionExtract(ST_Difference(r.geom, a.geom), 3)
            END, 6933))) / 10000 AS nova
        FROM recortes r LEFT JOIN grade_estatistica a ON a.ano = referencia - 1 AND (a.i, a.j) = (r.i, r.j)
        WHERE NOT ST_IsEmpty(r.geom)
        GROUP BY r.escopo, r.terra_id, r.uf
    ), totais AS (
        SELECT t.escopo, t.terra_id, t.uf,
            coalesce(m.detectada, 0) AS detectada, coalesce(m.nova, 0) AS nova, e.area_detectada_ha AS anterior
        FROM territorios_estatistica t
        LEFT JOIN medidas m ON m.escopo = t.escopo AND m.terra_id IS NOT DISTINCT FROM t.terra_id
                           AND m.uf IS NOT DISTINCT FROM t.uf
        LEFT JOIN public.estatisticas_anuais e ON comparavel AND e.ano = referencia - 1 AND e.escopo = t.escopo
            AND e.terra_id IS NOT DISTINCT FROM t.terra_id AND e.uf IS NOT DISTINCT FROM t.uf
    )
    INSERT INTO public.estatisticas_anuais
        (ano, escopo, terra_id, uf, area_detectada_ha, area_nova_ha, variacao_liquida_ha, crescimento_pct, fontes)
    SELECT referencia, escopo, terra_id, uf, detectada, CASE WHEN comparavel THEN nova END,
           detectada - anterior, 100 * (detectada - anterior) / nullif(anterior, 0), metadados
    FROM totais
    ON CONFLICT (ano, escopo, terra_id, uf) DO UPDATE SET
        area_detectada_ha = excluded.area_detectada_ha, area_nova_ha = excluded.area_nova_ha,
        variacao_liquida_ha = excluded.variacao_liquida_ha, crescimento_pct = excluded.crescimento_pct,
        fontes = excluded.fontes, calculado_em = now();
END;
$calculo$;
ANALYZE public.estatisticas_anuais;
COMMIT;

SELECT a.ano, count(e.id) AS territorios_calculados,
       max(e.area_detectada_ha) FILTER (WHERE e.escopo = 'brasil') AS area_detectada_em_tis_ha
FROM public.anos_garimpo a LEFT JOIN public.estatisticas_anuais e USING (ano)
GROUP BY a.ano ORDER BY a.ano;
