import csv
from io import StringIO

from flask import Blueprint, Response, request

from controle import controle_analise
from rotas.rotas_mapa import responder_geojson

api = Blueprint("api_analise", __name__, url_prefix="/api/analise")


def responder(analise):
    resultado, formato = controle_analise.consultar(request.args, analise)
    metadados = resultado["metadados"]
    if formato == "geojson":
        resposta = responder_geojson(resultado)
        resposta.headers["Content-Disposition"] = f'inline; filename="{analise}.geojson"'
    else:
        saida = StringIO(newline="")
        escritor = csv.writer(saida)
        campos = ["ano", "area_ha"]
        if analise == "proximidades":
            campos += ["distancia_m", "feicao_id", "fonte_id"]
        escritor.writerow(["id", *campos])
        for feicao in resultado["features"]:
            escritor.writerow([feicao["id"], *(feicao["properties"][campo] for campo in campos)])
        resposta = Response("\ufeff" + saida.getvalue(), content_type="text/csv; charset=utf-8")
        resposta.headers["Content-Disposition"] = f'attachment; filename="{analise}.csv"'
    resposta.headers["X-Estado-Analise"] = metadados["estado"]
    if metadados["proximo"] is not None:
        resposta.headers["X-Proximo-Cursor"] = str(metadados["proximo"])
    return resposta


@api.get("/proximidade")
def proximidade():
    return responder("proximidades")


@api.get("/expansao")
def expansao():
    return responder("estatisticas")
