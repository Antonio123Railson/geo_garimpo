# GeoGarimpo Brasil

**MVP v0.1.0**

GeoGarimpo Brasil é um WebGIS acadêmico de abrangência nacional. O projeto permite explorar as áreas de garimpo detectadas pelo MapBiomas, consultar limites de terras indígenas e acompanhar as mudanças nos anos publicados. O mapa mostra as ocorrências no recorte de visualização. A série estatística mede somente as áreas de interseção com terras indígenas.

**A série histórica ainda está incompleta.** A base deste MVP tem dados publicados de 1985 a 1989 e de 2025. A linha do tempo usa o catálogo do banco e acompanha novas cargas validadas. Detecção de garimpo por sensoriamento remoto **não determina sua legalidade**.

## Funcionalidades

- Mapa Leaflet com camadas independentes de garimpo e terras indígenas, filtros por ano, UF, terra indígena e classe, e carregamento por recorte e página.
- Linha do tempo com reprodução, pausa e navegação pelos anos disponíveis.
- Painel Chart.js de evolução e ranking quando a série está materializada, com indicação de resultados pendentes.
- Consulta de expansão e exportação paginada em GeoJSON e CSV quando existem resultados e metadados do cálculo.
- Catálogo de fontes, notas metodológicas e APIs de leitura para os dados publicados.

## Arquitetura e tecnologias

Flask e Gunicorn servem HTML5, CSS3 e JavaScript, com Leaflet no mapa e Chart.js nos gráficos. O backend usa PostgreSQL/PostGIS e SQL parametrizado com psycopg 3, sem ORM. Dados geográficos e cálculos ficam no banco remoto. O mapa-base OpenStreetMap é carregado pelo navegador. Leaflet e Chart.js são servidos com a aplicação.

| Camada | Responsabilidade |
| --- | --- |
| `main.py` e `rotas/` | Aplicação WSGI, páginas e endpoints HTTP |
| `controle/` | Validação dos filtros e regras de negócio |
| `modelo/` e `db/` | Consultas, conexão, esquema e índices |
| `templates/` e `static/` | Páginas, mapa, gráficos e controles |
| `processamento/` | Aquisição e análise offline com GeoPandas, Shapely, Rasterio e PyProj |

## Fontes

- **MapBiomas**, produto Substância Minerada da Coleção 11, para a série de garimpo detectado.
- **FUNAI** e **IBGE**, para os limites de terras indígenas e unidades federativas.
- **ANA** e **OpenStreetMap**, para hidrografia e infraestrutura nas etapas futuras. O mapa-base do MVP usa OpenStreetMap.

Os produtos, seus termos de uso e os procedimentos de aquisição estão no [catálogo de fontes](documentacao/fontes.md). As regras de interseção e cálculo estão na [metodologia](documentacao/metodologia.md).

## Instalação local

Use Python 3.12. No PowerShell, abra a raiz do repositório.

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
if (!(Test-Path .env)) { Copy-Item .env.example .env }
```

No Linux, use `python3.12 -m venv .venv` e `source .venv/bin/activate`. Preencha o `.env` conforme a seção do banco e inicie com `python main.py`. O servidor local abre em [127.0.0.1:5000](http://127.0.0.1:5000). A variável `PORT` pode selecionar outra porta.

Para importação, processamento e testes, instale o perfil local.

```powershell
python -m pip install -r requirements-local.txt
python -m pytest -q
```

Os testes de integração que criam bancos descartáveis exigem um PostgreSQL/PostGIS de testes. Use `GEOGARIMPO_CONFIG_TESTES` com o caminho de um arquivo de configuração separado. Confira os [comandos de aquisição e análise](documentacao/importacao_local.md).

## Banco de dados

Para reutilizar o Supabase, use os dados do PostgreSQL do painel **Connect**, preferencialmente o **Session pooler** quando precisar de IPv4. As variáveis são `DB_HOST`, `DB_PORTA`, `DB_NOME`, `DB_USUARIO` e `DB_SENHA`. O Flask usa SSL com Supabase e no Render, e as variáveis do processo têm precedência sobre o arquivo `.env`.

Para iniciar com um banco PostgreSQL local vazio, instale o PostGIS e execute, com um usuário que possa criar o banco e habilitar a extensão:

```powershell
createdb -U postgres geo_garimpo
psql -U postgres -d geo_garimpo -v ON_ERROR_STOP=1 -f db/schema.sql -f db/indices.sql
```

No banco já restaurado do Supabase, consulte o [guia da migração](documentacao/supabase.md) para materializar a evolução e o ranking sem recarregar as tabelas de origem. A API pública do site passa pelo Flask. O [SQL de proteção das tabelas](db/supabase/04_proteger_tabelas_publicas.sql) habilita RLS e remove o acesso direto dos papéis públicos nas tabelas do projeto. Rode `/health` para verificar o processo HTTP e `/api/saude` para verificar a conexão.

## Publicação no GitHub e no Render

Crie um repositório vazio no GitHub. Na raiz do projeto, revise os arquivos antes do primeiro commit.

```powershell
git init -b main
git add .
git diff --cached --name-only
git commit -m "Publica GeoGarimpo Brasil MVP v0.1.0"
git tag -a v0.1.0 -m "GeoGarimpo Brasil MVP v0.1.0"
git remote add origin https://github.com/SEU_USUARIO/geogarimpo-brasil.git
git push -u origin main
git push origin v0.1.0
```

No Render, crie um **Blueprint** a partir do repositório e da branch `main`. O [render.yaml](render.yaml) configura um Web Service gratuito com Python, Gunicorn, um worker, duas threads e a verificação `/health`. Informe as cinco variáveis `DB_*` no painel do Render. O Render injeta `PORT` e lê a versão de Python em [.python-version](.python-version). Depois de publicar, abra `/health`, `/api/saude`, `/mapa` e `/estatisticas` no endereço do serviço. [Blueprints do Render](https://render.com/docs/blueprint-spec) · [Porta dos serviços](https://render.com/docs/web-services#port-binding)

## Limitações e próximas etapas

- A importação histórica tem lacunas. A série e o ranking ficam pendentes para recortes ainda sem estatísticas materializadas.
- Proximidade, rios, estradas e pistas de pouso estão desabilitados no Supabase desta versão. A expansão depende da publicação das manchas calculadas e da sua proveniência.
- As feições do mapa são paginadas e simplificadas para visualização. Os cálculos científicos usam as geometrias de origem. A edição dos limites de terras indígenas e a resolução do MapBiomas influenciam a interpretação.
- O plano gratuito do Render pode iniciar a aplicação sob demanda após um período ocioso. [Limites da hospedagem gratuita](https://render.com/docs/free)

As próximas etapas são completar a série histórica, materializar as métricas e manchas de expansão, migrar as redes e validar as análises de proximidade. Consulte as [limitações metodológicas](documentacao/limitacoes.md) e o [relatório técnico](documentacao/relatorio_tecnico.md).
