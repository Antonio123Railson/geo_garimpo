-- Acesso às tabelas pelo Flask. O proprietário continua apto a ler e importar.
BEGIN;
SET LOCAL lock_timeout = '3s';
SET LOCAL statement_timeout = '15s';

DO $proteger$
DECLARE
    tabela text;
    relacao regclass;
    colunas text;
    sequencia text;
    papel text;
BEGIN
    FOREACH tabela IN ARRAY ARRAY[
        'anos_garimpo', 'fontes_dados', 'garimpos_anuais', 'manchas_indigenas',
        'terras_indigenas', 'unidades_federativas', 'estatisticas_anuais',
        'bases_ativas', 'execucoes_analise', 'analises_proximidade',
        'rios', 'estradas', 'pistas_pouso'
    ] LOOP
        relacao := to_regclass(format('public.%I', tabela));
        IF relacao IS NULL THEN
            CONTINUE;
        END IF;

        EXECUTE format('REVOKE ALL PRIVILEGES ON TABLE public.%I FROM PUBLIC, anon, authenticated', tabela);
        SELECT string_agg(quote_ident(attname), ', ' ORDER BY attnum) INTO colunas
        FROM pg_attribute WHERE attrelid = relacao AND attnum > 0 AND NOT attisdropped;
        IF colunas IS NOT NULL THEN
            EXECUTE format('REVOKE ALL PRIVILEGES (%s) ON TABLE public.%I FROM PUBLIC, anon, authenticated',
                           colunas, tabela);
        END IF;
        FOR sequencia IN
            SELECT pg_get_serial_sequence(format('public.%I', tabela), attname)
            FROM pg_attribute WHERE attrelid = relacao AND attnum > 0 AND NOT attisdropped
        LOOP
            IF sequencia IS NOT NULL THEN
                EXECUTE format('REVOKE ALL PRIVILEGES ON SEQUENCE %s FROM PUBLIC, anon, authenticated', sequencia);
            END IF;
        END LOOP;
        EXECUTE format('ALTER TABLE public.%I ENABLE ROW LEVEL SECURITY', tabela);

        FOREACH papel IN ARRAY ARRAY['anon', 'authenticated'] LOOP
            IF has_table_privilege(papel, relacao, 'SELECT, INSERT, UPDATE, DELETE, TRUNCATE, REFERENCES, TRIGGER, MAINTAIN')
               OR has_any_column_privilege(papel, relacao, 'SELECT, INSERT, UPDATE, REFERENCES') THEN
                RAISE EXCEPTION 'Privilégio efetivo remanescente em % para %.', tabela, papel;
            END IF;
        END LOOP;
    END LOOP;
END;
$proteger$;
COMMIT;
