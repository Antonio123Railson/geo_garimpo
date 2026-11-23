-- Execute no banco geo_garimpo. Lê o catálogo anual, as contagens e as áreas registradas na carga.
BEGIN ISOLATION LEVEL REPEATABLE READ, READ ONLY;

SELECT a.ano, f.colecao, f.versao, f.id AS fonte_id, contagem.poligonos,
       (f.metadados->>'area_raster_ha')::double precision AS area_raster_ha,
       (f.metadados#>>'{validacao_publicacao,area_vetor_ha}')::double precision AS area_vetor_validada_ha,
       CASE
           WHEN contagem.poligonos <> (f.metadados->>'poligonos')::bigint THEN 'contagem divergente'
           WHEN f.metadados#>>'{validacao_publicacao,aprovada}' = 'true' THEN 'validado na publicação histórica'
           WHEN a.ano = 2025 THEN 'piloto; consulte a auditoria de 2025'
           ELSE 'sem marca de validação histórica'
       END AS situacao
FROM public.anos_garimpo a JOIN public.fontes_dados f ON f.id = a.fonte_id
CROSS JOIN LATERAL (SELECT count(*) AS poligonos FROM public.garimpos_anuais g WHERE g.ano = a.ano) contagem
WHERE a.ano BETWEEN 1985 AND 2025 ORDER BY a.ano;

SELECT count(*) AS anos_importados, min(ano) AS primeiro, max(ano) AS ultimo
FROM public.anos_garimpo WHERE ano BETWEEN 1985 AND 2025;

SELECT esperado.ano AS ano_pendente
FROM generate_series(1985, 2025) AS esperado(ano)
LEFT JOIN public.anos_garimpo existente USING (ano)
WHERE existente.ano IS NULL ORDER BY esperado.ano;

COMMIT;
