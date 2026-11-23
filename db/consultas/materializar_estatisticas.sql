WITH medidas AS (
    SELECT escopo,terra_id,uf,sum(ST_Area(ST_Transform(geom,6933)))/10000 AS detectada,
           sum(ST_Area(ST_Transform(nova,6933)))/10000 AS nova
    FROM recortes_atual GROUP BY escopo,terra_id,uf
), territorios AS (
    SELECT 'brasil'::text AS escopo, NULL::bigint AS terra_id, NULL::text AS uf
    UNION ALL SELECT 'terra',id,NULL FROM terras_indigenas
    UNION ALL SELECT 'uf',NULL,sigla FROM unidades_federativas
), totais AS (
    SELECT t.escopo,t.terra_id,t.uf,coalesce(m.detectada,0) AS detectada,coalesce(m.nova,0) AS nova,
           anterior.area_detectada_ha AS anterior
    FROM territorios t
    LEFT JOIN medidas m ON t.escopo = m.escopo AND t.terra_id IS NOT DISTINCT FROM m.terra_id
        AND t.uf IS NOT DISTINCT FROM m.uf
    LEFT JOIN estatisticas_anuais anterior ON anterior.ano = %(ano)s - 1 AND anterior.escopo = t.escopo
        AND anterior.terra_id IS NOT DISTINCT FROM t.terra_id AND anterior.uf IS NOT DISTINCT FROM t.uf
)
INSERT INTO estatisticas_anuais
    (ano,escopo,terra_id,uf,area_detectada_ha,area_nova_ha,variacao_liquida_ha,crescimento_pct,fontes)
SELECT %(ano)s,escopo,terra_id,uf,detectada,CASE WHEN %(comparavel)s AND anterior IS NOT NULL THEN nova END,
       CASE WHEN %(comparavel)s THEN detectada - anterior END,
       CASE WHEN %(comparavel)s THEN 100 * (detectada - anterior) / nullif(anterior,0) END,%(fontes)s
FROM totais;
