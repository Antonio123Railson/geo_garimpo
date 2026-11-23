import { consultar, elemento, endereco, formatar, preencherAnos, preencherTabela, preencherTerras, recorteExclusivo } from "./consultas.js";
import { abrirMapa, camadaVetorial, enquadrarBrasil } from "./mapa.js";

const MENSAGENS = {
  sem_serie: "Nenhum ano de garimpo carregado.",
  pendente: "A análise ainda precisa ser calculada offline.",
  alvo_ausente: "A camada de referência selecionada ainda não foi importada e processada.",
  sem_ano_anterior: "O ano imediatamente anterior está ausente. Não há comparação anual.",
  mascaras_incompativeis: "Os dois anos têm áreas válidas diferentes. A comparação de novas detecções está indisponível.",
  calculado: "Nenhuma mancha corresponde aos filtros aplicados.",
};

async function iniciarAnalise() {
  const forma = elemento("filtros-analise");
  const tipo = elemento("tipo-analise");
  const ano = elemento("ano-analise");
  const uf = elemento("uf-analise");
  const terra = elemento("terra-analise");
  const estado = elemento("estado-analise");
  const opcoes = elemento("opcoes-proximidade");
  const csv = elemento("exportar-csv");
  const geojson = elemento("exportar-geojson");
  const anterior = elemento("anterior-analise");
  const proxima = elemento("proxima-analise");
  const tabela = elemento("tabela-analise");
  const tituloDistancia = elemento("titulo-distancia");
  const mapa = globalThis.L ? abrirMapa("mapa-analise") : null;
  const vetores = mapa ? camadaVetorial(mapa, "#b3511b", {
    id: "Mancha", ano: "Ano", area_ha: "Área (ha)", distancia_m: "Distância (m)", fonte_id: "Fonte",
  }) : null;
  let filtros;
  let dadosPagina;
  let cursores = [];
  let pagina = 0;
  let requisicao;

  recorteExclusivo(uf, terra);
  tipo.addEventListener("change", () => { opcoes.hidden = opcoes.disabled = tipo.value !== "proximidade"; });

  async function carregar(indice) {
    requisicao?.abort();
    const atual = new AbortController();
    requisicao = atual;
    tabela.hidden = true;
    csv.disabled = geojson.disabled = anterior.disabled = proxima.disabled = true;
    estado.textContent = "Consultando resultados calculados…";
    vetores?.clearLayers();
    try {
      const dados = await consultar(filtros.caminho, { ...filtros.parametros, apos: cursores[indice] }, atual.signal);
      if (atual.signal.aborted) return;
      dadosPagina = dados;
      pagina = indice;
      const proximo = dados.metadados.proximo;
      cursores[indice + 1] = proximo;
      anterior.disabled = indice === 0;
      proxima.disabled = !proximo;
      if (!dados.features.length) {
        estado.textContent = MENSAGENS[dados.metadados.estado];
        if (mapa) enquadrarBrasil(mapa);
        return;
      }
      estado.textContent = `Página ${indice + 1} · ${dados.features.length} manchas. Os arquivos correspondem a esta página.`;
      csv.disabled = geojson.disabled = false;
      const temDistancia = filtros.caminho.endsWith("/proximidade");
      tituloDistancia.hidden = !temDistancia;
      preencherTabela(elemento("linhas-analise"), dados.features.map(feicao => {
        const { ano: referencia, area_ha, distancia_m } = feicao.properties;
        return [feicao.id, referencia, formatar(area_ha), ...(temDistancia ? [formatar(distancia_m)] : [])];
      }));
      tabela.hidden = false;
      if (vetores) {
        vetores.addData(dados);
        const limites = vetores.getBounds();
        if (limites.isValid()) mapa.fitBounds(limites, { padding: [25, 25], maxZoom: 14 });
      }
    } catch (erro) {
      if (!atual.signal.aborted) {
        estado.textContent = erro.message;
        anterior.disabled = indice === 0;
      }
    }
  }

  function baixar(url, nome) {
    const link = document.createElement("a");
    link.href = url;
    link.download = nome;
    link.click();
  }

  forma.addEventListener("submit", evento => {
    evento.preventDefault();
    const adicionais = tipo.value === "proximidade" ? {
      alvo: elemento("alvo-analise").value, tipo: elemento("manchas-analise").value, raio: elemento("raio-analise").value,
    } : {};
    filtros = {
      caminho: `/api/analise/${tipo.value}`, nome: tipo.value,
      parametros: { ano: ano.value, uf: uf.value, terra_id: terra.value, limite: 100, ...adicionais },
    };
    cursores = [null];
    pagina = 0;
    carregar(pagina);
  });
  anterior.addEventListener("click", () => carregar(pagina - 1));
  proxima.addEventListener("click", () => carregar(pagina + 1));
  geojson.addEventListener("click", () => {
    const arquivo = new Blob([JSON.stringify(dadosPagina)], { type: "application/geo+json" });
    const url = URL.createObjectURL(arquivo);
    baixar(url, `${filtros.nome}-${filtros.parametros.ano}-${pagina + 1}.geojson`);
    setTimeout(() => URL.revokeObjectURL(url), 1000);
  });
  csv.addEventListener("click", () => baixar(endereco(filtros.caminho, {
    ...filtros.parametros, apos: cursores[pagina], formato: "csv",
  }), `${filtros.nome}-${filtros.parametros.ano}-${pagina + 1}.csv`));

  const resultados = await Promise.allSettled([consultar("/api/anos"), preencherTerras(terra)]);
  if (resultados[0].status === "rejected") {
    estado.textContent = resultados[0].reason.message;
    return;
  }
  const { anos } = resultados[0].value;
  preencherAnos(ano, anos);
  elemento("buscar-analise").disabled = !anos.length;
  estado.textContent = anos.length ? "Selecione os parâmetros e consulte uma análise." : "Nenhum ano de garimpo carregado.";
}

iniciarAnalise();
