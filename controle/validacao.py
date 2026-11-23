from datetime import date
from math import isfinite
from re import fullmatch

UFS = "AC AL AP AM BA CE DF ES GO MA MT MS MG PA PB PR PE PI RJ RN RS RO RR SC SP SE TO".split()
BRASIL = (-74.5, -34.5, -28.0, 6.5)
MAXIMO_ID = 9_007_199_254_740_991


class ErroParametro(ValueError):
    codigo = 400


class RecursoAusente(LookupError):
    codigo = 404


def aceitar(parametros, *nomes):
    for nome in parametros:
        if nome not in nomes or len(parametros.getlist(nome)) != 1:
            raise ErroParametro(f"Parâmetro desconhecido ou repetido: {nome}.")


def inteiro(parametros, nome, minimo, maximo, padrao=None, obrigatorio=False):
    valor = parametros.get(nome)
    if valor is None:
        if obrigatorio:
            raise ErroParametro(f"Informe o parâmetro {nome}.")
        return padrao
    if not fullmatch(r"[0-9]{1,16}", str(valor)) or not minimo <= int(valor) <= maximo:
        raise ErroParametro(f"{nome} deve ser um inteiro entre {minimo} e {maximo}.")
    return int(valor)


def ano(parametros, nome="ano", *, obrigatorio=False):
    return inteiro(parametros, nome, 1985, date.today().year, obrigatorio=obrigatorio)


def escolha(parametros, nome, opcoes, padrao=None):
    valor = parametros.get(nome, padrao)
    if valor is not None and valor not in opcoes:
        raise ErroParametro(f"Valor inválido para {nome}. Opções: {', '.join(opcoes)}.")
    return valor


def caixa(parametros, padrao=BRASIL):
    bruto = parametros.get("bbox")
    if bruto is None:
        return padrao
    try:
        oeste, sul, leste, norte = map(float, bruto.split(",")) if len(bruto) <= 110 else ()
    except ValueError:
        raise ErroParametro("Use bbox=oeste,sul,leste,norte em graus decimais.") from None
    if not (
        all(map(isfinite, (oeste, sul, leste, norte)))
        and -180 <= oeste < leste <= 180
        and -90 <= sul < norte <= 90
    ):
        raise ErroParametro("A caixa espacial está fora dos limites ou com coordenadas invertidas.")
    return oeste, sul, leste, norte


def janela(parametros):
    return {
        "caixa": caixa(parametros),
        "zoom": inteiro(parametros, "zoom", 0, 22, 5),
        "limite": inteiro(parametros, "limite", 1, 400, 200),
        "apos": inteiro(parametros, "apos", 0, MAXIMO_ID, 0),
        "uf": escolha(parametros, "uf", UFS),
        "terra": inteiro(parametros, "terra_id", 1, MAXIMO_ID),
    }
