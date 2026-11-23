SELECT ano, area_detectada_ha, area_detectada_ha / 100 AS area_detectada_km2,
       area_nova_ha, variacao_liquida_ha, crescimento_pct, calculado_em, fontes
FROM estatisticas_anuais e
WHERE escopo = %(escopo)s
  AND terra_id IS NOT DISTINCT FROM %(terra)s::bigint
  AND uf IS NOT DISTINCT FROM %(uf)s::text
  AND ano BETWEEN %(inicio)s AND %(fim)s
  AND EXISTS (SELECT 1 FROM anos_garimpo a WHERE a.ano = e.ano)
ORDER BY ano;
