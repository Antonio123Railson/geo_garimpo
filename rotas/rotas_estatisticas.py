from flask import Blueprint, jsonify, request

from controle import controle_estatisticas

api = Blueprint("api_estatisticas", __name__, url_prefix="/api/estatisticas")


@api.get("/evolucao")
def evolucao():
    return jsonify(controle_estatisticas.evolucao(request.args))


@api.get("/terra-indigena/<terra_id>")
def evolucao_terra(terra_id):
    return jsonify(controle_estatisticas.evolucao(request.args, terra_id))


@api.get("/ranking")
def ranking():
    return jsonify(controle_estatisticas.ranking(request.args))
