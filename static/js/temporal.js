import { elemento } from "./consultas.js";

export function prepararTempo(anos, atualizar) {
  const controle = elemento("ano-mapa");
  const saida = elemento("ano-visivel");
  const voltar = elemento("voltar-ano");
  const avancar = elemento("avancar-ano");
  const reproduzir = elemento("reproduzir");
  const pausar = elemento("pausar");
  const velocidade = elemento("velocidade");
  let temporizador = null;
  let atualizacao;
  let reproduzindo = false;
  let sequencia = 0;

  function atualizarBotoes() {
    reproduzir.disabled = anos.length < 2 || reproduzindo;
    pausar.disabled = !reproduzindo;
  }

  function sincronizar() {
    const indice = Number(controle.value);
    const ano = anos[indice];
    saida.value = ano || "Sem série carregada";
    controle.setAttribute("aria-valuetext", saida.value);
    voltar.disabled = !ano || indice === 0;
    avancar.disabled = !ano || indice === anos.length - 1;
    atualizarBotoes();
    atualizacao = atualizar(ano);
  }

  function mudar(indice) {
    controle.value = Math.max(0, Math.min(anos.length - 1, indice));
    sincronizar();
  }

  function pausarTempo() {
    clearTimeout(temporizador);
    reproduzindo = false;
    sequencia++;
    atualizarBotoes();
  }

  async function agendar(recomecar = false) {
    clearTimeout(temporizador);
    const atual = ++sequencia;
    await atualizacao;
    if (!reproduzindo || atual !== sequencia) return;
    const indice = Number(controle.value);
    if (indice >= anos.length - 1) {
      if (!recomecar) {
        pausarTempo();
        return;
      }
      mudar(0);
      agendar();
      return;
    }
    temporizador = setTimeout(() => {
      mudar(indice + 1);
      agendar();
    }, Number(velocidade.value));
  }

  controle.max = Math.max(0, anos.length - 1);
  controle.value = Math.max(0, anos.length - 1);
  controle.disabled = anos.length === 0;
  controle.addEventListener("input", () => { pausarTempo(); sincronizar(); });
  voltar.addEventListener("click", () => { pausarTempo(); mudar(Number(controle.value) - 1); });
  avancar.addEventListener("click", () => { pausarTempo(); mudar(Number(controle.value) + 1); });
  reproduzir.addEventListener("click", () => {
    reproduzindo = true;
    atualizarBotoes();
    agendar(true);
  });
  pausar.addEventListener("click", pausarTempo);
  velocidade.addEventListener("change", () => { if (reproduzindo) agendar(); });
  document.addEventListener("visibilitychange", () => { if (document.hidden) pausarTempo(); });
  sincronizar();
  return pausarTempo;
}
