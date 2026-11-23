from flask import Blueprint, jsonify, request

from controle import controle_garimpo
from rotas.rotas_mapa import responder_geojson

api = Blueprint("api_garimpo", __name__, url_prefix="/api")


@api.get("/anos")
def anos():
    return jsonify(controle_garimpo.anos(request.args))


@api.get("/garimpos")
def garimpos():
    return responder_geojson(controle_garimpo.geometrias(request.args))
