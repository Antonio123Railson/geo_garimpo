-- Pode ser repetido. Os nomes correspondem aos índices restaurados do projeto.
BEGIN;
SET LOCAL lock_timeout = '3s';

CREATE INDEX IF NOT EXISTS garimpos_anuais_geom_gix ON public.garimpos_anuais USING gist (geom);
CREATE INDEX IF NOT EXISTS garimpos_anuais_ano_id_idx ON public.garimpos_anuais (ano, id);
CREATE INDEX IF NOT EXISTS terras_indigenas_geom_gix ON public.terras_indigenas USING gist (geom);
CREATE INDEX IF NOT EXISTS unidades_federativas_geom_gix ON public.unidades_federativas USING gist (geom);
CREATE INDEX IF NOT EXISTS manchas_indigenas_geom_gix ON public.manchas_indigenas USING gist (geom);
CREATE INDEX IF NOT EXISTS manchas_indigenas_ano_tipo_idx ON public.manchas_indigenas (ano, tipo, id);

ANALYZE public.anos_garimpo;
ANALYZE public.fontes_dados;
ANALYZE public.terras_indigenas;
ANALYZE public.unidades_federativas;
ANALYZE public.garimpos_anuais;
ANALYZE public.manchas_indigenas;
COMMIT;

SELECT tablename, indexname, indexdef
FROM pg_indexes
WHERE schemaname = 'public'
  AND tablename IN ('garimpos_anuais', 'terras_indigenas', 'unidades_federativas', 'manchas_indigenas')
ORDER BY tablename, indexname;
