BEGIN;
CREATE EXTENSION IF NOT EXISTS postgis;

CREATE TABLE IF NOT EXISTS fontes_dados (
    id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    tipo text NOT NULL CHECK (tipo IN ('mapbiomas','funai','ana','osm','ibge')),
    nome text NOT NULL CHECK (length(btrim(nome)) BETWEEN 1 AND 240),
    url text NOT NULL CHECK (url ~ '^https?://[^[:space:]]+$'),
    colecao text NOT NULL CHECK (btrim(colecao) <> ''),
    versao text NOT NULL CHECK (btrim(versao) <> ''),
    licenca text NOT NULL CHECK (btrim(licenca) <> ''),
    adquirido_em date NOT NULL,
    metodologia text NOT NULL CHECK (btrim(metodologia) <> ''),
    sha256 text NOT NULL CHECK (sha256 ~ '^[0-9a-f]{64}$'),
    metadados jsonb NOT NULL DEFAULT '{}' CHECK (jsonb_typeof(metadados) = 'object'),
    registrado_em timestamptz NOT NULL DEFAULT now(),
    UNIQUE (tipo, url, colecao, versao, adquirido_em, sha256, metadados)
);

CREATE TABLE IF NOT EXISTS bases_ativas (
    camada text PRIMARY KEY CHECK (camada IN ('terras_indigenas','ufs','rios','estradas','pistas_pouso')),
    fonte_id bigint NOT NULL REFERENCES fontes_dados
);

CREATE TABLE IF NOT EXISTS terras_indigenas (
    id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    codigo text NOT NULL UNIQUE,
    nome text NOT NULL CHECK (length(btrim(nome)) BETWEEN 1 AND 240),
    situacao text,
    fonte_id bigint NOT NULL REFERENCES fontes_dados,
    geom geometry(MultiPolygon, 4674) NOT NULL CHECK (NOT ST_IsEmpty(geom) AND ST_IsValid(geom))
);

CREATE TABLE IF NOT EXISTS unidades_federativas (
    sigla text PRIMARY KEY CHECK (sigla IN ('AC','AL','AP','AM','BA','CE','DF','ES','GO','MA','MT','MS','MG',
        'PA','PB','PR','PE','PI','RJ','RN','RS','RO','RR','SC','SP','SE','TO')),
    nome text NOT NULL CHECK (length(btrim(nome)) BETWEEN 1 AND 240),
    fonte_id bigint NOT NULL REFERENCES fontes_dados,
    geom geometry(MultiPolygon, 4674) NOT NULL CHECK (NOT ST_IsEmpty(geom) AND ST_IsValid(geom))
);

CREATE TABLE IF NOT EXISTS anos_garimpo (
    ano smallint PRIMARY KEY CHECK (ano BETWEEN 1985 AND 9999),
    fonte_id bigint NOT NULL REFERENCES fontes_dados,
    classes integer[] NOT NULL CHECK (cardinality(classes) > 0),
    assinatura_grade text NOT NULL,
    UNIQUE (ano, fonte_id)
);

CREATE TABLE IF NOT EXISTS garimpos_anuais (
    id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    ano smallint NOT NULL,
    fonte_id bigint NOT NULL,
    classe integer NOT NULL,
    geom geometry(MultiPolygon, 4674) NOT NULL CHECK (NOT ST_IsEmpty(geom) AND ST_IsValid(geom)),
    FOREIGN KEY (ano, fonte_id) REFERENCES anos_garimpo (ano, fonte_id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS rios (
    id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    codigo text NOT NULL,
    nome text,
    fonte_id bigint NOT NULL REFERENCES fontes_dados,
    geom geometry(MultiLineString, 4674) NOT NULL CHECK (NOT ST_IsEmpty(geom) AND ST_IsValid(geom))
);

CREATE TABLE IF NOT EXISTS estradas (
    id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    codigo text NOT NULL,
    nome text,
    classe text NOT NULL,
    fonte_id bigint NOT NULL REFERENCES fontes_dados,
    geom geometry(MultiLineString, 4674) NOT NULL CHECK (NOT ST_IsEmpty(geom) AND ST_IsValid(geom))
);

CREATE TABLE IF NOT EXISTS pistas_pouso (
    id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    codigo text NOT NULL,
    nome text,
    classe text NOT NULL,
    fonte_id bigint NOT NULL REFERENCES fontes_dados,
    geom geometry(Geometry, 4674) NOT NULL CHECK (
        GeometryType(geom) IN ('POINT','MULTIPOINT','MULTILINESTRING','MULTIPOLYGON')
        AND NOT ST_IsEmpty(geom) AND ST_IsValid(geom)
    )
);

CREATE TABLE IF NOT EXISTS estatisticas_anuais (
    id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    ano smallint NOT NULL REFERENCES anos_garimpo ON DELETE CASCADE,
    escopo text NOT NULL CHECK (escopo IN ('brasil','terra','uf')),
    terra_id bigint REFERENCES terras_indigenas ON DELETE CASCADE,
    uf text REFERENCES unidades_federativas ON DELETE CASCADE,
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

CREATE TABLE IF NOT EXISTS manchas_indigenas (
    id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    ano smallint NOT NULL REFERENCES anos_garimpo ON DELETE CASCADE,
    tipo text NOT NULL CHECK (tipo IN ('detectada','nova')),
    area_ha double precision NOT NULL CHECK (area_ha > 0),
    geom geometry(Polygon, 4674) NOT NULL CHECK (NOT ST_IsEmpty(geom) AND ST_IsValid(geom))
);

CREATE TABLE IF NOT EXISTS analises_proximidade (
    mancha_id bigint REFERENCES manchas_indigenas ON DELETE CASCADE,
    alvo text NOT NULL CHECK (alvo IN ('rios','estradas','pistas')),
    feicao_id bigint NOT NULL,
    fonte_id bigint NOT NULL REFERENCES fontes_dados,
    distancia_m double precision NOT NULL CHECK (distancia_m >= 0 AND distancia_m < 'Infinity'::float8),
    calculado_em timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (mancha_id, alvo)
);

CREATE TABLE IF NOT EXISTS execucoes_analise (
    analise text PRIMARY KEY CHECK (analise IN ('estatisticas','proximidades')),
    calculado_em timestamptz NOT NULL DEFAULT now(),
    fontes jsonb NOT NULL
);
COMMIT;
