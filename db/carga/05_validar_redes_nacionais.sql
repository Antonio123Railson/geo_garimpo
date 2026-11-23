-- Execute no banco geo_garimpo após a carga. A auditoria lê apenas dados definitivos.
BEGIN ISOLATION LEVEL REPEATABLE READ, READ ONLY;
SET LOCAL statement_timeout = 0;
SET LOCAL work_mem = '32MB';

WITH fontes AS (
    SELECT camada, fonte_id FROM public.bases_ativas JOIN public.fontes_dados ON id = fonte_id
), resumo AS (
    SELECT 'rios' AS camada, count(*) AS registros,
           count(*) FILTER (WHERE NOT ST_IsValid(d.geom)) AS invalidas,
           count(*) FILTER (WHERE ST_IsEmpty(d.geom)) AS vazias,
           count(*) FILTER (WHERE ST_SRID(d.geom) <> 4674) AS srid_incorreto,
           count(*) FILTER (WHERE GeometryType(d.geom) <> 'MULTILINESTRING') AS tipos_incorretos,
           count(*) FILTER (WHERE d.fonte_id IS DISTINCT FROM (SELECT fonte_id FROM fontes WHERE camada = 'rios'))
               AS fontes_incorretas,
           array_remove(ARRAY[min(ST_SRID(d.geom)), nullif(max(ST_SRID(d.geom)),min(ST_SRID(d.geom)))],NULL) AS srids,
           array_remove(ARRAY[min(GeometryType(d.geom)), nullif(max(GeometryType(d.geom)),min(GeometryType(d.geom)))],NULL) AS tipos,
           ST_Extent(d.geom)::text AS extensao
    FROM public.rios d
    UNION ALL
    SELECT 'estradas', count(*),
           count(*) FILTER (WHERE NOT ST_IsValid(d.geom)), count(*) FILTER (WHERE ST_IsEmpty(d.geom)),
           count(*) FILTER (WHERE ST_SRID(d.geom) <> 4674), count(*) FILTER (WHERE GeometryType(d.geom) <> 'MULTILINESTRING'),
           count(*) FILTER (WHERE d.fonte_id IS DISTINCT FROM (SELECT fonte_id FROM fontes WHERE camada = 'estradas')),
           array_remove(ARRAY[min(ST_SRID(d.geom)), nullif(max(ST_SRID(d.geom)),min(ST_SRID(d.geom)))],NULL),
           array_remove(ARRAY[min(GeometryType(d.geom)), nullif(max(GeometryType(d.geom)),min(GeometryType(d.geom)))],NULL),
           ST_Extent(d.geom)::text
    FROM public.estradas d
    UNION ALL
    SELECT 'pistas_pouso', count(*),
           count(*) FILTER (WHERE NOT ST_IsValid(d.geom)), count(*) FILTER (WHERE ST_IsEmpty(d.geom)),
           count(*) FILTER (WHERE ST_SRID(d.geom) <> 4674),
           count(*) FILTER (WHERE GeometryType(d.geom) NOT IN ('POINT','MULTIPOINT','MULTILINESTRING','MULTIPOLYGON')),
           count(*) FILTER (WHERE d.fonte_id IS DISTINCT FROM (SELECT fonte_id FROM fontes WHERE camada = 'pistas_pouso')),
           array_agg(DISTINCT ST_SRID(d.geom)), array_agg(DISTINCT GeometryType(d.geom)), ST_Extent(d.geom)::text
    FROM public.pistas_pouso d
)
SELECT * FROM resumo ORDER BY camada;

SELECT b.camada, f.id AS fonte_id, f.nome, f.colecao, f.versao, f.licenca, f.adquirido_em,
       f.sha256, f.url, f.metadados
FROM public.bases_ativas b JOIN public.fontes_dados f ON f.id = b.fonte_id
WHERE b.camada IN ('rios','estradas','pistas_pouso') ORDER BY b.camada;

SELECT 'estradas' AS camada, classe, count(*) AS registros FROM public.estradas GROUP BY classe
UNION ALL
SELECT 'pistas_pouso', classe, count(*) FROM public.pistas_pouso GROUP BY classe
ORDER BY camada, classe;

SELECT sigla,
       EXISTS (SELECT 1 FROM public.rios r WHERE r.geom && u.geom AND ST_Intersects(r.geom,u.geom)) AS rios,
       EXISTS (SELECT 1 FROM public.estradas e WHERE e.geom && u.geom AND ST_Intersects(e.geom,u.geom)) AS estradas,
       EXISTS (SELECT 1 FROM public.pistas_pouso p WHERE p.classe = 'runway'
               AND p.geom && u.geom AND ST_Intersects(p.geom,u.geom)) AS pistas,
       EXISTS (SELECT 1 FROM public.pistas_pouso p WHERE p.classe = 'aerodrome'
               AND p.geom && u.geom AND ST_Intersects(p.geom,u.geom)) AS aerodromos,
       EXISTS (SELECT 1 FROM public.pistas_pouso p WHERE p.classe = 'airstrip'
               AND p.geom && u.geom AND ST_Intersects(p.geom,u.geom)) AS faixas_pouso
FROM public.unidades_federativas u ORDER BY sigla;

SELECT 'unidades_federativas' AS camada, count(*) AS registros,
       md5(string_agg(md5(to_jsonb(t)::text),'' ORDER BY sigla)) AS assinatura FROM public.unidades_federativas t
UNION ALL SELECT 'terras_indigenas', count(*), md5(string_agg(md5(to_jsonb(t)::text),'' ORDER BY id))
FROM public.terras_indigenas t
UNION ALL SELECT 'garimpos_anuais', count(*), md5(string_agg(md5(to_jsonb(t)::text),'' ORDER BY id))
FROM public.garimpos_anuais t
ORDER BY camada;

COMMIT;
