-- Compatível com o esquema do importador e com as consultas Flask.
BEGIN;
SET LOCAL lock_timeout = '3s';
DO $ddl$
BEGIN
    IF to_regclass('public.estatisticas_anuais') IS NULL THEN
        CREATE TABLE public.estatisticas_anuais (
            id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
            ano smallint NOT NULL REFERENCES public.anos_garimpo ON DELETE CASCADE,
            escopo text NOT NULL CHECK (escopo IN ('brasil', 'terra', 'uf')),
            terra_id bigint REFERENCES public.terras_indigenas ON DELETE CASCADE,
            uf text REFERENCES public.unidades_federativas ON DELETE CASCADE,
            area_detectada_ha double precision NOT NULL CHECK (area_detectada_ha >= 0),
            area_nova_ha double precision CHECK (area_nova_ha >= 0),
            variacao_liquida_ha double precision,
            crescimento_pct double precision,
            fontes jsonb NOT NULL,
            calculado_em timestamptz NOT NULL DEFAULT now(),
            CHECK ((escopo = 'terra') = (terra_id IS NOT NULL) AND (escopo = 'uf') = (uf IS NOT NULL)),
            CHECK ((area_nova_ha IS NULL) = (variacao_liquida_ha IS NULL)),
            UNIQUE NULLS NOT DISTINCT (ano, escopo, terra_id, uf)
        );
        ALTER TABLE public.estatisticas_anuais ENABLE ROW LEVEL SECURITY;
    END IF;
END;
$ddl$;
CREATE INDEX IF NOT EXISTS estatisticas_escopo_ano_idx
    ON public.estatisticas_anuais (escopo, ano, area_detectada_ha DESC);
CREATE INDEX IF NOT EXISTS estatisticas_terra_ano_idx ON public.estatisticas_anuais (terra_id, ano);
COMMIT;
