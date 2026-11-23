-- Se a sessão atual do pgAdmin estiver em 25P02, execute ROLLBACK nessa aba primeiro.
BEGIN;

CREATE SCHEMA IF NOT EXISTS carga;

CREATE TABLE IF NOT EXISTS carga.arquivos_geojson (
    id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    camada text NOT NULL CHECK (
        camada IN ('terras_indigenas', 'ufs', 'garimpos', 'rios', 'estradas', 'pistas_pouso')
    ),
    nome_arquivo text NOT NULL CHECK (btrim(nome_arquivo) <> ''),
    conteudo jsonb NOT NULL CHECK ((
        jsonb_typeof(conteudo) = 'object'
        AND conteudo->>'type' = 'FeatureCollection'
        AND jsonb_typeof(conteudo->'features') = 'array'
    ) IS TRUE),
    carregado_em timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS arquivos_geojson_camada_idx ON carga.arquivos_geojson (camada);

COMMIT;
