import os
from uuid import uuid4

import psycopg
import pytest
from dotenv import dotenv_values
from psycopg import sql

from db.conexao import RAIZ, conectar
from main import criar_aplicacao


@pytest.fixture
def cliente():
    aplicacao = criar_aplicacao()
    aplicacao.config["TESTING"] = True
    yield aplicacao.test_client()
    pool = aplicacao.extensions.get("pool_postgresql")
    if pool is not None:
        pool.close()


@pytest.fixture(scope="session")
def criar_banco_teste():
    caminho = os.getenv("GEOGARIMPO_CONFIG_TESTES")
    if not caminho:
        pytest.skip("Defina GEOGARIMPO_CONFIG_TESTES para executar a integração PostgreSQL/PostGIS.")
    valores = dotenv_values(caminho, encoding="utf-8-sig", interpolate=False)
    argumentos = {
        "host": valores.get("DB_HOST", "127.0.0.1"), "port": valores.get("DB_PORTA", "5432"),
        "user": valores.get("DB_USUARIO"), "password": valores.get("DB_SENHA"), "connect_timeout": 3,
        "dbname": "postgres", "autocommit": True,
    }
    criados = []
    with psycopg.connect(**argumentos) as administracao:
        def criar():
            nome = f"geo_garimpo_teste_{uuid4().hex}"
            administracao.execute(sql.SQL("CREATE DATABASE {} TEMPLATE template0 ENCODING 'UTF8'").format(
                sql.Identifier(nome)
            ))
            criados.append(nome)
            configuracao = dict(valores, DB_NOME=nome)
            with conectar(escrita=True, configuracao=configuracao) as conexao:
                conexao.autocommit = True
                for arquivo in ("schema.sql", "indices.sql"):
                    conexao.execute((RAIZ / "db" / arquivo).read_text(encoding="utf-8"))
            return configuracao

        try:
            yield criar
        finally:
            for nome in criados:
                administracao.execute(sql.SQL("DROP DATABASE {}").format(sql.Identifier(nome)))


@pytest.fixture(scope="session")
def banco_vazio(criar_banco_teste):
    return criar_banco_teste()


@pytest.fixture
def cliente_vazio(cliente, banco_vazio, monkeypatch):
    cliente.application.config["REDES_HABILITADAS"] = True
    monkeypatch.setattr("db.conexao.dotenv_values", lambda *args, **kwargs: banco_vazio)
    return cliente
