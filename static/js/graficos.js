import { consultar, elemento, formatar, preencherAnos, preencherTabela, preencherTerras, recorteExclusivo } from "./consultas.js";

function indicadores(lista, registro) {
  const pares = [
    ["Área detectada", `${formatar(registro.area_detectada_ha)} ha`],
    ["Área detectada", `${formatar(registro.area_detectada_km2)} km²`],
    ["Novas detecções", registro.area_nova_ha === null ? "—" : `${formatar(registro.area_nova_ha)} ha`],
    ["Variação líquida", registro.variacao_liquida_ha === null ? "—" : `${formatar(registro.variacao_liquida_ha)} ha`],
    ["Crescimento anual", registro.crescimento_pct === null ? "—" : `${formatar(registro.crescimento_pct)}%`],
  ];
  lista.replaceChildren(...pares.map(([rotulo, valor]) => {
    const grupo = document.createElement("div");
    const termo = document.createElement("dt");
    const dado = document.createElement("dd");
    termo.textContent = rotulo;
    dado.textContent = valor;
    grupo.append(termo, dado);
    return grupo;
  }));
  lista.hidden = false;
}

async function iniciarPaginaInicial() {
  const estado = elemento("estado-inicio");
  try {
    const { dados } = await consultar("/api/estatisticas/evolucao");
    if (!dados.length) {
      estado.textContent = "Ainda não há estatísticas nacionais calculadas.";
      return;
    }
    estado.textContent = `Último ano processado: ${dados.at(-1).ano}.`;
    indicadores(elemento("indicadores-inicio"), dados.at(-1));
  } catch (erro) {
    estado.textContent = erro.message;
  }
}

function graficoSerie(dados) {
  const porAno = new Map(dados.map(linha => [linha.ano, linha]));
  const primeiro = dados[0].ano;
  const anos = Array.from({ length: dados.at(-1).ano - primeiro + 1 }, (_, indice) => primeiro + indice);
  const metricas = [
    ["Área detectada", "area_detectada_ha", "#146349"],
    ["Área nova", "area_nova_ha", "#a24a14"],
    ["Variação líquida", "variacao_liquida_ha", "#3f70a1"],
  ];
  return new Chart(elemento("grafico-serie"), {
    type: "line",
    data: { labels: anos, datasets: metricas.map(([label, chave, cor]) => ({
      label, data: anos.map(ano => porAno.get(ano)?.[chave] ?? null), borderColor: cor, backgroundColor: cor,
      pointRadius: 3, borderWidth: 2, spanGaps: false,
    })) },
    options: { responsive: true, maintainAspectRatio: false, locale: "pt-BR",
      scales: { y: { title: { display: true, text: "Hectares" } } } },
  });
}

function graficoRanking(dados) {
  return new Chart(elemento("grafico-ranking"), {
    type: "bar",
    data: { labels: dados.map(linha => linha.nome), datasets: [{
      label: "Área detectada (ha)", data: dados.map(linha => linha.area_detectada_ha), backgroundColor: "#27775b",
    }] },
    options: { indexAxis: "y", responsive: true, maintainAspectRatio: false, locale: "pt-BR",
      scales: { x: { beginAtZero: true } }, plugins: { legend: { display: false } } },
  });
}

async function iniciarEstatisticas() {
  const estadoSerie = elemento("estado-serie");
  const estadoRanking = elemento("estado-ranking");
  const terra = elemento("terra-serie");
  const uf = elemento("uf-serie");
  const seletorAno = elemento("ano-ranking");
  const quadroSerie = elemento("quadro-serie");
  const quadroRanking = elemento("quadro-ranking");
  const tabelaSerie = elemento("detalhes-serie");
  const tabelaRanking = elemento("tabela-ranking");
  const resumo = elemento("indicadores-serie");
  let serie;
  let ranking;
  let pedidoSerie;
  let pedidoRanking;
  recorteExclusivo(uf, terra);

  async function carregarSerie() {
    pedidoSerie?.abort();
    const atual = new AbortController();
    pedidoSerie = atual;
    serie?.destroy();
    serie = null;
    quadroSerie.hidden = tabelaSerie.hidden = resumo.hidden = true;
    estadoSerie.textContent = "Consultando a série disponível…";
    const caminho = terra.value ? `/api/estatisticas/terra-indigena/${terra.value}` : "/api/estatisticas/evolucao";
    try {
      const { dados } = await consultar(caminho, { uf: uf.value }, atual.signal);
      if (atual.signal.aborted) return;
      if (!dados.length) {
        estadoSerie.textContent = "Não há estatísticas calculadas para o recorte selecionado.";
        return;
      }
      estadoSerie.textContent = `Último ano calculado: ${dados.at(-1).ano}. Traços na tabela indicam métricas sem base de comparação.`;
      indicadores(resumo, dados.at(-1));
      preencherTabela(elemento("linhas-serie"), dados.map(linha => [
        linha.ano, formatar(linha.area_detectada_ha), formatar(linha.area_detectada_km2),
        formatar(linha.area_nova_ha), formatar(linha.variacao_liquida_ha), formatar(linha.crescimento_pct),
      ]));
      tabelaSerie.hidden = false;
      if (globalThis.Chart) {
        quadroSerie.hidden = false;
        serie = graficoSerie(dados);
      }
    } catch (erro) {
      if (!atual.signal.aborted) estadoSerie.textContent = erro.message;
    }
  }

  async function carregarRanking() {
    if (!seletorAno.value) return;
    pedidoRanking?.abort();
    const atual = new AbortController();
    pedidoRanking = atual;
    ranking?.destroy();
    ranking = null;
    quadroRanking.hidden = tabelaRanking.hidden = true;
    estadoRanking.textContent = "Consultando ranking…";
    try {
      const { dados, ano } = await consultar("/api/estatisticas/ranking", {
        ano: seletorAno.value, escopo: elemento("escopo-ranking").value, limite: 15,
      }, atual.signal);
      if (atual.signal.aborted) return;
      if (!dados.length) {
        estadoRanking.textContent = "Nenhum território com área detectada positiva e processada nesse recorte.";
        return;
      }
      estadoRanking.textContent = `${dados.length} maiores áreas em ${ano}.`;
      preencherTabela(elemento("linhas-ranking"), dados.map(linha => [
        linha.nome, formatar(linha.area_detectada_ha), formatar(linha.area_detectada_km2),
      ]));
      tabelaRanking.hidden = false;
      if (globalThis.Chart) {
        quadroRanking.hidden = false;
        ranking = graficoRanking(dados);
      }
    } catch (erro) {
      if (!atual.signal.aborted) estadoRanking.textContent = erro.message;
    }
  }

  elemento("filtros-serie").addEventListener("submit", evento => { evento.preventDefault(); carregarSerie(); });
  elemento("filtros-ranking").addEventListener("submit", evento => { evento.preventDefault(); carregarRanking(); });
  carregarSerie();
  const resultados = await Promise.allSettled([consultar("/api/anos"), preencherTerras(terra)]);
  if (resultados[0].status === "fulfilled") {
    const { anos } = resultados[0].value;
    preencherAnos(seletorAno, anos);
    elemento("buscar-ranking").disabled = anos.length === 0;
    if (anos.length) carregarRanking();
    else estadoRanking.textContent = "Ainda não há anos de garimpo carregados.";
  } else {
    estadoRanking.textContent = resultados[0].reason.message;
  }
}

async function carregarFontes() {
  const estado = elemento("estado-fontes");
  try {
    const { dados } = await consultar("/api/fontes");
    if (!dados.length) {
      estado.textContent = "Nenhuma aquisição registrada para as bases ativas.";
      return;
    }
    const data = new Intl.DateTimeFormat("pt-BR", { timeZone: "UTC" });
    estado.textContent = `Última aquisição entre os arquivos ativos: ${data.format(new Date(dados[0].adquirido_em))}.`;
    const listados = new Set();
    for (const fonte of dados) {
      const chave = [fonte.tipo, fonte.colecao, fonte.versao].join("|");
      if (listados.has(chave)) continue;
      listados.add(chave);
      const item = document.createElement("li");
      item.textContent = `${fonte.nome} · ${fonte.colecao} · ${fonte.versao}`;
      elemento("lista-fontes").append(item);
    }
  } catch (erro) {
    estado.textContent = erro.message;
  }
}

if (elemento("estado-inicio")) iniciarPaginaInicial();
if (elemento("estado-serie")) iniciarEstatisticas();
if (elemento("estado-fontes")) carregarFontes();
