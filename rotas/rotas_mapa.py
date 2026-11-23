from flask import Blueprint, jsonify, request

from controle import controle_mapa, controle_terras_indigenas
from controle.validacao import aceitar

api = Blueprint("api_mapa", __name__, url_prefix="/api")


def responder_geojson(resultado):
    resposta = jsonify(resultado)
    resposta.mimetype = "application/geo+json"
    return resposta


@api.get("/saude")
def saude():
    aceitar(request.args)
    return jsonify(controle_mapa.saude())


@api.get("/fontes")
def fontes():
    return jsonify(controle_mapa.fontes(request.args))


@api.get("/terras-indigenas")
def terras():
    resultado = controle_terras_indigenas.listar(request.args)
    return responder_geojson(resultado) if "features" in resultado else jsonify(resultado)


@api.get("/rios")
def rios():
    return responder_geojson(controle_mapa.camada(request.args, "rios"))


@api.get("/estradas")
def estradas():
    return responder_geojson(controle_mapa.camada(request.args, "estradas"))


@api.get("/pistas-pouso")
def pistas():
    return responder_geojson(controle_mapa.camada(request.args, "pistas"))
