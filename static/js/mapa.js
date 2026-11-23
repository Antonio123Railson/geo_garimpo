import { consultar, elemento, endereco, formatar, preencherTerras } from "./consultas.js";
import { prepararTempo } from "./temporal.js";

const CAIXA_BRASIL = [[-34, -74], [5.4, -28.7]];
const RECORTE_BRASIL = [-74.5, -34.5, -28, 6.5];
const ESPERA_MAPA_MS = 500;
const VALIDADE_CACHE_MS = 45_000;
const LIMITE_CACHE = 24;
const CAMADAS = {
  terras: { caminho: "/api/terras-indigenas", nome: "Terras indígenas", cor: "#237f56" },
  garimpos: { caminho: "/api/garimpos", nome: "Garimpos", cor: "#c3561a" },
  rios: { caminho: "/api/rios", nome: "Rios", cor: "#2379a7", local: true },
  estradas: { caminho: "/api/estradas", nome: "Estradas", cor: "#756448", local: true },
  pistas: { caminho: "/api/pistas-pouso", nome: "Pistas de pouso", cor: "#80559a", local: true },
};

export function enquadrarBrasil(mapa) {
  mapa.fitBounds(CAIXA_BRASIL, { padding: [10, 10], animate: false });
}

export function abrirMapa(id, estadoBase) {
  const mapa = L.map(id, { minZoom: 2, maxZoom: 19, worldCopyJump: true, preferCanvas: true, zoomControl: false });
  const base = L.tileLayer("https://tile.openstreetmap.org/{z}/{x}/{y}.png", {
    attribution: '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a>',
    maxZoom: 19,
  });
  base.on("tileerror", () => {
    if (estadoBase) estadoBase.textContent = "O mapa-base online está temporariamente indisponível.";
  });
  base.on("tileload", () => { if (estadoBase) estadoBase.textContent = ""; });
  base.addTo(mapa);
  L.control.zoom({ zoomInTitle: "Aproximar o mapa", zoomOutTitle: "Afastar o mapa" }).addTo(mapa);
  L.control.scale({ imperial: false, position: "bottomleft" }).addTo(mapa);
  enquadrarBrasil(mapa);
  return mapa;
}

export function camadaVetorial(mapa, cor, campos) {
  const estilo = { color: cor, weight: 2, fillColor: cor, fillOpacity: .28 };
  return L.geoJSON(null, {
    style: estilo,
    pointToLayer: (_feicao, ponto) => L.circleMarker(ponto, { ...estilo, radius: 5, fillOpacity: .8 }),
    onEachFeature: (feicao, forma) => {
      const conteudo = document.createElement("div");
      conteudo.className = "popup-mapa";
      for (const [chave, nome] of Object.entries(campos)) {
        const valor = chave === "id" ? feicao.id : feicao.properties[chave];
        if (valor === null || valor === undefined) continue;
        const linha = document.createElement("p");
        const rotulo = document.createElement("strong");
        rotulo.textContent = `${nome} `;
        linha.append(rotulo, typeof valor === "number" ? formatar(valor) : valor);
        conteudo.append(linha);
      }
      forma.bindPopup(conteudo);
    },
  }).addTo(mapa);
}

function recorteVisivel(mapa) {
  const limites = mapa.getBounds();
  const passo = 360 / (256 * 2 ** mapa.getZoom()) * 4;
  const valores = [limites.getWest(), limites.getSouth(), limites.getEast(), limites.getNorth()];
  return valores.map((valor, indice) => {
    const borda = RECORTE_BRASIL[indice];
    const ajustado = indice < 2
      ? Math.max(borda, Math.floor(valor / passo) * passo)
      : Math.min(borda, Math.ceil(valor / passo) * passo);
    return Number(ajustado.toFixed(7));
  });
}

async function iniciarMapa() {
  const estado = elemento("estado-mapa");
  if (!globalThis.L) {
    estado.textContent = "O componente de mapa não foi carregado.";
    return;
  }
  const mapa = abrirMapa("mapa", elemento("estado-base"));
  const campos = { nome: "Nome", codigo: "Código", situacao: "Situação", ano: "Ano", classe: "Classe", fonte_id: "Fonte" };
  const vetores = Object.fromEntries(Object.entries(CAMADAS).map(([nome, camada]) => [
    nome, camadaVetorial(mapa, camada.cor, campos),
  ]));
  const uf = elemento("uf-mapa");
  const terra = elemento("terra-mapa");
  const classe = elemento("classe-mapa");
  const mais = elemento("mais-feicoes");
  const controles = [...document.querySelectorAll("[data-camada]:enabled")];
  let anoAtual;
  let catalogo = [];
  let mensagemCatalogo = "";
  let requisicao;
  let espera;
  let pausarTempo = () => {};
  const exibidas = new Map();
  const cache = new Map();

  async function consultarPagina(caminho, parametros, chave, sinal) {
    const entrada = cache.get(chave);
    cache.delete(chave);
    if (entrada && entrada.expira > Date.now()) {
      cache.set(chave, entrada);
      return entrada.dados;
    }
    const dados = await consultar(caminho, parametros, sinal);
    if (!sinal.aborted && JSON.stringify(dados).length <= 200_000) {
      cache.set(chave, { dados, expira: Date.now() + VALIDADE_CACHE_MS });
      if (cache.size > LIMITE_CACHE) cache.delete(cache.keys().next().value);
    }
    return dados;
  }

  function definirCarregamento(carregando) {
    elemento("mapa").setAttribute("aria-busy", String(carregando));
    estado.classList.toggle("carregando", carregando);
    mais.disabled = carregando;
    if (carregando) estado.textContent = "Atualizando camadas do mapa…";
  }

  function cancelar() {
    clearTimeout(espera);
    espera = null;
    requisicao?.abort();
    requisicao = null;
  }

  async function carregar(paginar = false) {
    if (paginar && (requisicao || espera)) return;
    cancelar();
    const [oeste, sul, leste, norte] = recorteVisivel(mapa);
    if (oeste >= leste || sul >= norte) {
      definirCarregamento(false);
      mais.hidden = true;
      estado.textContent = "Enquadre o Brasil para consultar as camadas.";
      return;
    }
    const atual = new AbortController();
    requisicao = atual;
    const mensagens = [];
    const parametros = { bbox: [oeste, sul, leste, norte].join(","), zoom: mapa.getZoom(),
      uf: uf.value, terra_id: terra.value, limite: 150 };
    const consultas = [];
    let temProxima = false;
    const habilitadas = controles.filter(controle => controle.checked).map(controle => controle.dataset.camada);
    for (const nome of habilitadas) {
      const camada = CAMADAS[nome];
      if (nome === "garimpos" && !anoAtual) {
        mensagens.push("Nenhum ano de garimpo carregado.");
        continue;
      }
      if (camada.local && (leste - oeste > 6 || norte - sul > 6)) {
        mensagens.push(`${camada.nome} ficam disponíveis por aproximação.`);
        continue;
      }
      const adicionais = nome === "garimpos" ? { ano: anoAtual, classe: classe.value } : {};
      const filtros = { ...parametros, ...adicionais };
      const chave = endereco(camada.caminho, filtros).href;
      const exibicao = exibidas.get(nome);
      const mesmaConsulta = exibicao?.chave === chave;
      const anterior = paginar && mesmaConsulta ? exibicao : null;
      if ((paginar && !anterior?.cursor) || (!paginar && mesmaConsulta && exibicao.expira > Date.now())) {
        if (mesmaConsulta) {
          mensagens.push(exibicao.mensagem);
          temProxima ||= Boolean(exibicao.cursor);
        }
        continue;
      }
      consultas.push({ nome, camada, filtros, chave, anterior });
    }
    definirCarregamento(consultas.length > 0);
    await Promise.all(consultas.map(async ({ nome, camada, filtros, chave, anterior }) => {
      try {
        const pagina = { ...filtros, apos: anterior?.cursor };
        const chavePagina = endereco(camada.caminho, pagina).href;
        const dados = await consultarPagina(camada.caminho, pagina, chavePagina, atual.signal);
        if (requisicao !== atual || atual.signal.aborted) return;
        if (!anterior) vetores[nome].clearLayers();
        vetores[nome].addData(dados);
        const paginas = (anterior?.paginas || 0) + 1;
        const cursor = paginas < 4 ? dados.metadados.proximo : null;
        const quantidade = vetores[nome].getLayers().length;
        const aviso = dados.metadados.omitidas || (dados.metadados.proximo && paginas >= 4)
          ? " Aproxime para consultar mais detalhes." : "";
        const mensagem = dados.metadados.estado === "alvo_ausente"
          ? `${camada.nome} estão temporariamente indisponíveis.`
          : `${camada.nome}: ${quantidade || "sem feições no recorte"}.${aviso}`;
        exibidas.set(nome, { chave, mensagem, paginas, cursor, expira: Date.now() + VALIDADE_CACHE_MS });
        mensagens.push(mensagem);
        temProxima ||= Boolean(cursor);
      } catch (erro) {
        if (requisicao !== atual || atual.signal.aborted) return;
        mensagens.push(`${camada.nome}: ${erro.message}${
          exibidas.has(nome) ? " A camada anterior permanece visível." : ""}`);
        temProxima ||= Boolean(anterior?.cursor);
      }
    }));
    if (requisicao !== atual || atual.signal.aborted) return;
    requisicao = null;
    definirCarregamento(false);
    estado.textContent = [...new Set([...mensagens, mensagemCatalogo].filter(Boolean))].join(" ")
      || "Ative uma camada para consultar o mapa.";
    mais.hidden = !temProxima;
  }

  function aguardarMovimento() {
    pausarTempo();
    cancelar();
    definirCarregamento(true);
  }

  function agendarCarregamento() {
    aguardarMovimento();
    espera = setTimeout(() => carregar(), ESPERA_MAPA_MS);
  }

  function selecionarAno(ano) {
    anoAtual = ano;
    const anterior = classe.value;
    const classes = catalogo.find(item => item.ano === ano)?.classes || [];
    classe.replaceChildren(new Option("Todas as classes", ""), ...classes.map(codigo => new Option(codigo, codigo)));
    if (classes.includes(Number(anterior))) classe.value = anterior;
    classe.disabled = !classes.length;
    return carregar();
  }

  mapa.on("movestart", aguardarMovimento);
  mapa.on("moveend", agendarCarregamento);
  elemento("enquadrar").addEventListener("click", () => enquadrarBrasil(mapa));
  uf.addEventListener("change", agendarCarregamento);
  classe.addEventListener("change", agendarCarregamento);
  terra.addEventListener("change", () => {
    const caixa = terra.selectedOptions[0]?.dataset.bbox?.split(",").map(Number);
    if (caixa) mapa.fitBounds([[caixa[1], caixa[0]], [caixa[3], caixa[2]]], { padding: [35, 35], maxZoom: 13 });
    agendarCarregamento();
  });
  for (const controle of controles) controle.addEventListener("change", () => {
    const camada = vetores[controle.dataset.camada];
    if (controle.checked) camada.addTo(mapa);
    else mapa.removeLayer(camada);
    agendarCarregamento();
  });
  for (const [nome, camada] of Object.entries(vetores)) {
    if (!controles.some(controle => controle.dataset.camada === nome && controle.checked)) mapa.removeLayer(camada);
  }
  mais.addEventListener("click", () => carregar(true));
  const resultados = await Promise.allSettled([consultar("/api/anos"), preencherTerras(terra)]);
  if (resultados[1].status === "rejected") mensagemCatalogo = "Catálogo de terras indígenas temporariamente indisponível.";
  else if (resultados[1].value) mensagemCatalogo = "Catálogo parcial de terras indígenas.";
  if (resultados[0].status === "fulfilled") {
    catalogo = resultados[0].value.fontes;
    pausarTempo = prepararTempo(resultados[0].value.anos, selecionarAno);
  } else {
    mensagemCatalogo = [mensagemCatalogo, resultados[0].reason.message].filter(Boolean).join(" ");
    pausarTempo = prepararTempo([], selecionarAno);
  }
}

if (elemento("mapa")) iniciarMapa();
