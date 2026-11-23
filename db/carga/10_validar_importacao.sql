BEGIN READ ONLY;

SELECT current_database() AS banco, current_setting('server_version') AS postgresql,
       postgis_full_version() AS postgis, to_regclass('carga.arquivos_geojson') AS tabela_de_carga;

SELECT count(*) AS unidades, count(DISTINCT u.sigla) AS siglas_distintas,
       array_agg(u.sigla ORDER BY u.sigla) AS siglas, ST_Extent(u.geom) AS cobertura,
       count(*) FILTER (WHERE ST_SRID(u.geom) <> 4674 OR GeometryType(u.geom) <> 'MULTIPOLYGON'
                        OR ST_NDims(u.geom) <> 2) AS tipos_ou_srids_divergentes,
       count(*) FILTER (WHERE NOT ST_IsValid(u.geom) OR ST_IsEmpty(u.geom)) AS invalidas_ou_vazias,
       count(*) FILTER (WHERE f.tipo IS DISTINCT FROM 'ibge' OR f.id IS DISTINCT FROM b.fonte_id) AS fontes_divergentes,
       sum(ST_Area(u.geom::geography))/1000000 AS area_geodesica_km2
FROM public.unidades_federativas u
LEFT JOIN public.fontes_dados f ON f.id = u.fonte_id
LEFT JOIN public.bases_ativas b ON b.camada = 'ufs';

SELECT b.camada, f.id, f.nome, f.versao, f.adquirido_em, f.sha256, f.url, f.licenca
FROM public.bases_ativas b JOIN public.fontes_dados f ON f.id = b.fonte_id
WHERE b.camada = 'ufs';

SELECT ano, fonte_id FROM public.anos_garimpo ORDER BY ano;

COMMIT;
