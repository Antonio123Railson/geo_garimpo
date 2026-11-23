-- Execute no pgAdmin após carregar um GeoJSON RFC 7946 em carga.arquivos_geojson.
-- Preencha os dois IDs abaixo. A fonte IBGE deve corresponder ao ZIP original.
BEGIN;
SELECT pg_advisory_xact_lock(78120926);
LOCK TABLE public.unidades_federativas, public.bases_ativas, public.estatisticas_anuais,
    public.manchas_indigenas, public.analises_proximidade, public.execucoes_analise
    IN SHARE ROW EXCLUSIVE MODE;

DO $carga$
DECLARE
    id_arquivo bigint := NULL;
    id_fonte bigint := NULL;
    colecao jsonb;
    atual bigint;
    esperadas text[] := ARRAY['AC','AL','AM','AP','BA','CE','DF','ES','GO','MA','MG','MS','MT','PA',
        'PB','PE','PI','PR','RJ','RN','RO','RR','RS','SC','SE','SP','TO'];
BEGIN
    IF id_arquivo IS NULL OR id_fonte IS NULL THEN
        RAISE EXCEPTION 'Preencha id_arquivo e id_fonte com os identificadores validados.';
    END IF;
    SELECT conteudo INTO STRICT colecao FROM carga.arquivos_geojson
    WHERE id = id_arquivo AND camada = 'ufs';
    IF colecao ? 'crs' OR NOT EXISTS (
        SELECT 1 FROM public.fontes_dados WHERE id = id_fonte AND tipo = 'ibge'
    ) THEN
        RAISE EXCEPTION 'É necessário GeoJSON RFC 7946 em EPSG:4326 e uma fonte registrada como IBGE.';
    END IF;

    CREATE TEMP TABLE ufs_conferidas ON COMMIT DROP AS
    SELECT upper(btrim(feicao->'properties'->>'SIGLA_UF')) AS sigla,
           btrim(feicao->'properties'->>'NM_UF') AS nome,
           ST_Multi(ST_CollectionExtract(ST_MakeValid(ST_Transform(
               ST_Force2D(ST_GeomFromGeoJSON(feicao->'geometry')), 4674
           )), 3))::geometry(MultiPolygon,4674) AS geom
    FROM jsonb_array_elements(colecao->'features') AS itens(feicao);

    IF (SELECT array_agg(sigla ORDER BY sigla COLLATE "C") FROM ufs_conferidas)
           IS DISTINCT FROM esperadas
       OR EXISTS (SELECT 1 FROM ufs_conferidas WHERE nome IS NULL OR nome = '' OR geom IS NULL
                  OR ST_IsEmpty(geom) OR NOT ST_IsValid(geom)) THEN
        RAISE EXCEPTION 'A camada precisa conter as 27 UFs distintas com nome e geometria válida.';
    END IF;

    SELECT fonte_id INTO atual FROM public.bases_ativas WHERE camada = 'ufs';
    IF atual = id_fonte THEN
        IF EXISTS (
            SELECT 1 FROM ufs_conferidas c
            LEFT JOIN public.unidades_federativas u USING (sigla)
            WHERE u.sigla IS NULL OR u.nome IS DISTINCT FROM c.nome OR u.fonte_id <> id_fonte
                OR NOT ST_Equals(u.geom, c.geom)
        ) THEN
            RAISE EXCEPTION 'Os dados de origem divergem da edição já publicada.';
        END IF;
        RAISE NOTICE 'As 27 UFs desta aquisição já estão publicadas.';
        RETURN;
    END IF;
    IF atual IS NOT NULL OR EXISTS (SELECT 1 FROM public.unidades_federativas)
       OR EXISTS (SELECT 1 FROM public.estatisticas_anuais)
       OR EXISTS (SELECT 1 FROM public.manchas_indigenas)
       OR EXISTS (SELECT 1 FROM public.analises_proximidade)
       OR EXISTS (SELECT 1 FROM public.execucoes_analise) THEN
        RAISE EXCEPTION 'Há UFs ou análises existentes. A substituição exige autorização.';
    END IF;

    INSERT INTO public.unidades_federativas (sigla, nome, fonte_id, geom)
    SELECT sigla, nome, id_fonte, geom FROM ufs_conferidas;
    INSERT INTO public.bases_ativas (camada, fonte_id) VALUES ('ufs', id_fonte);
END
$carga$;

COMMIT;
