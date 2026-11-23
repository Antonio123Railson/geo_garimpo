# Dicionário de dados

As geometrias são armazenadas em SIRGAS 2000, EPSG:4674, com longitude e latitude. Áreas analíticas usam EPSG:6933 e distâncias usam `geography` com esferoide. A API cartográfica transforma a saída para EPSG:4326.

| Tabela | Chaves e atributos |
|---|---|
| `fontes_dados` | `id` identifica uma aquisição. `tipo`, `nome`, `url`, `colecao`, `versao`, `licenca`, `adquirido_em`, `metodologia`, `sha256`, `registrado_em` e `metadados` registram a proveniência. |
| `bases_ativas` | `camada` identifica uma das cinco bases vetoriais ativas e `fonte_id` referencia a aquisição corrente. |
| `terras_indigenas` | `id` interno, `codigo` estável da FUNAI, `nome`, `situacao` opcional, `fonte_id` e `geom` MultiPolygon. Partes do mesmo código são dissolvidas. |
| `unidades_federativas` | `sigla` da UF, `nome`, `fonte_id` e `geom` MultiPolygon. |
| `anos_garimpo` | `ano` identifica uma banda carregada, inclusive quando ela tem zero detecções. `fonte_id`, `classes` e `assinatura_grade` registram aquisição, seleção de legenda e comparabilidade. |
| `garimpos_anuais` | `id` de um fragmento vetorizado, `ano`, `fonte_id`, `classe` original e `geom` MultiPolygon. A FK composta impede divergência da fonte registrada no ano. |
| `rios` | `id`, `codigo` da fonte, `nome` opcional, `fonte_id` e `geom` MultiLineString. |
| `estradas` | `id`, `codigo` OSM, `nome` opcional, `classe`, `fonte_id` e `geom` MultiLineString. |
| `pistas_pouso` | `id`, `codigo` OSM, `nome` opcional, `classe`, `fonte_id` e `geom` ponto, multiponto, multilinha ou multipolígono. |
| `estatisticas_anuais` | Uma linha por `ano` e recorte `escopo`, com `terra_id` ou `uf` conforme o caso. Guarda `area_detectada_ha`, `area_nova_ha`, `variacao_liquida_ha`, `crescimento_pct`, `fontes` e `calculado_em`. |
| `manchas_indigenas` | `id` de um fragmento da máscara nacional indígena, `ano`, `tipo` detectada ou nova, `area_ha` e `geom` Polygon. |
| `analises_proximidade` | PK `mancha_id,alvo`. `feicao_id` remete à tabela identificada por `alvo`, `fonte_id` à edição usada, `distancia_m` à menor distância observada e `calculado_em` ao processamento. |
| `execucoes_analise` | `analise` identifica a última execução publicada, `calculado_em` e `fontes` descrevem o instantâneo processado. |

As chaves primárias e índices B-tree atendem às buscas por identificador, ano e escopo. Índices GiST atendem às interseções em geometria e à distância em geografia. As rotinas de carga substituem camadas em transação e serializam alterações com trava consultiva. As consultas HTTP leem um instantâneo consistente.
