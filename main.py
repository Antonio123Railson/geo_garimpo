from os import environ

import psycopg
from flask import Flask, jsonify, render_template, request
from psycopg_pool import PoolTimeout, TooManyRequests
from werkzeug.exceptions import HTTPException

from controle.validacao import ErroParametro, RecursoAusente
from rotas.rotas_analise import api as api_analise
from rotas.rotas_estatisticas import api as api_estatisticas
from rotas.rotas_garimpo import api as api_garimpo
from rotas.rotas_mapa import api as api_mapa
from rotas.rotas_paginas import paginas


def criar_aplicacao():
    aplicacao = Flask(__name__)
    aplicacao.config["REDES_HABILITADAS"] = False
    aplicacao.config.from_prefixed_env(prefix="GEOGARIMPO")
    for blueprint in (paginas, api_mapa, api_garimpo, api_estatisticas, api_analise):
        aplicacao.register_blueprint(blueprint)

    @aplicacao.get("/health")
    def saude_aplicacao():
        return jsonify(estado="ok")

    @aplicacao.errorhandler(ErroParametro)
    @aplicacao.errorhandler(RecursoAusente)
    def erro_validado(erro):
        return jsonify(erro=str(erro)), erro.codigo

    @aplicacao.errorhandler(Exception)
    def erro_padrao(erro):
        if isinstance(erro, HTTPException):
            codigo = erro.code
            mensagem = {404: "Página ou recurso não encontrado.", 405: "Método não permitido."}.get(
                codigo, "A solicitação não pôde ser atendida."
            )
        elif isinstance(erro, psycopg.Error):
            diagnostico = erro.diag
            aplicacao.logger.error(
                "Falha SQL em %s: %s SQLSTATE=%s esquema=%r tabela=%r coluna=%r restrição=%r função=%r posição=%r",
                request.endpoint, type(erro).__name__, erro.sqlstate or "indisponível",
                diagnostico.schema_name, diagnostico.table_name, diagnostico.column_name,
                diagnostico.constraint_name, diagnostico.source_function, diagnostico.statement_position,
            )
            if isinstance(erro, psycopg.errors.QueryCanceled):
                codigo, mensagem = 504, "A consulta excedeu o tempo disponível. Reduza o recorte e tente novamente."
            elif isinstance(erro, (PoolTimeout, TooManyRequests, psycopg.OperationalError)):
                codigo, mensagem = 503, "Banco indisponível ou ocupado. Tente novamente em instantes."
            else:
                codigo, mensagem = 500, "O banco não pôde concluir a consulta. Consulte o registro da aplicação."
        else:
            codigo, mensagem = 500, "Ocorreu uma falha interna. Consulte o registro da aplicação."
            aplicacao.logger.error("Falha em %s: %s", request.endpoint, type(erro).__name__)
        if request.path.startswith("/api/"):
            resposta = jsonify(erro=mensagem)
            if codigo == 503:
                resposta.headers["Retry-After"] = "2"
            return resposta, codigo
        return render_template("erro.html", codigo=codigo, mensagem=mensagem), codigo

    @aplicacao.after_request
    def proteger_resposta(resposta):
        resposta.headers["X-Content-Type-Options"] = "nosniff"
        if request.path == "/health" or request.path.startswith("/api/"):
            resposta.headers["Cache-Control"] = "no-store"
        return resposta

    return aplicacao


aplicacao = criar_aplicacao()

if __name__ == "__main__":
    aplicacao.run(host="127.0.0.1", port=int(environ.get("PORT", "5000")), debug=False)
