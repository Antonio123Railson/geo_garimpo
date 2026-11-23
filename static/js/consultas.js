const numero = new Intl.NumberFormat("pt-BR", { maximumFractionDigits: 2 });

export function formatar(valor) {
  return valor === null || valor === undefined ? "—" : numero.format(valor);
}

export function elemento(id) {
  return document.getElementById(id);
}

export function endereco(caminho, parametros = {}) {
  const url = new URL(caminho, window.location.origin);
  for (const [chave, valor] of Object.entries(parametros)) {
    if (valor !== "" && valor !== null && valor !== undefined) url.searchParams.set(chave, valor);
  }
  return url;
}

export async function consultar(caminho, parametros, sinal) {
  const resposta = await fetch(endereco(caminho, parametros), { signal: sinal });
  if (!resposta.ok) {
    const corpo = await resposta.json().catch(() => ({}));
    throw new Error(corpo.erro || "A consulta não pôde ser concluída.");
  }
  return resposta.json();
}

export async function preencherTerras(seletor) {
  const terras = [];
  let apos = null;
  do {
    const pagina = await consultar("/api/terras-indigenas", { formato: "lista", limite: 400, apos });
    terras.push(...pagina.dados);
    apos = pagina.proximo;
  } while (apos && terras.length < 2400);
  terras.sort((a, b) => a.nome.localeCompare(b.nome, "pt-BR"));
  for (const terra of terras) {
    const opcao = new Option(`${terra.nome} · ${terra.codigo}`, terra.id);
    opcao.dataset.bbox = terra.bbox.join(",");
    seletor.add(opcao);
  }
  return Boolean(apos);
}

export function preencherAnos(seletor, anos) {
  if (!anos.length) return;
  seletor.replaceChildren(...anos.map(ano => new Option(ano, ano)));
  seletor.value = anos.at(-1);
  seletor.disabled = false;
}

export function recorteExclusivo(uf, terra) {
  uf.addEventListener("change", () => { if (uf.value) terra.value = ""; });
  terra.addEventListener("change", () => { if (terra.value) uf.value = ""; });
}

export function preencherTabela(corpo, linhas) {
  corpo.replaceChildren(...linhas.map(valores => {
    const linha = document.createElement("tr");
    for (const valor of valores) {
      const celula = linha.insertCell();
      celula.textContent = valor;
    }
    return linha;
  }));
}
