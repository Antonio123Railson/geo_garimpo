from contextlib import contextmanager
from os import environ
from pathlib import Path
from threading import Lock

import psycopg
from dotenv import dotenv_values
from flask import current_app, has_app_context
from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool

RAIZ = Path(__file__).resolve().parents[1]
TRAVA_POOL = Lock()
VARIAVEIS_BANCO = ("DB_HOST", "DB_PORTA", "DB_NOME", "DB_USUARIO", "DB_SENHA")


def argumentos_conexao(configuracao):
    valores = configuracao
    if valores is None:
        ambiente = {chave: environ[chave] for chave in VARIAVEIS_BANCO if chave in environ}
        arquivo = (
            dotenv_values(RAIZ / ".env", encoding="utf-8-sig", interpolate=False)
            if len(ambiente) < len(VARIAVEIS_BANCO) else {}
        )
        valores = {**arquivo, **ambiente}
    host = valores.get("DB_HOST")
    argumentos = {
        "host": host,
        "port": valores.get("DB_PORTA"),
        "dbname": valores.get("DB_NOME"),
        "user": valores.get("DB_USUARIO"),
        "password": valores.get("DB_SENHA"),
        "connect_timeout": 3,
        "row_factory": dict_row,
        "application_name": "geogarimpo-brasil",
    }
    hospedado = configuracao is None and environ.get("RENDER") == "true"
    if hospedado or (host and host.lower().rstrip(".").endswith((".supabase.co", ".pooler.supabase.com"))):
        argumentos["sslmode"] = "require"
    if str(argumentos["port"]) == "6543":
        argumentos["prepare_threshold"] = None
    return argumentos


class ConexaoWeb(psycopg.Connection):
    @classmethod
    def connect(cls, *args, **kwargs):
        try:
            return super().connect(*args, **kwargs)
        except psycopg.Error as erro:
            # O pool registra falhas ao conectar. Omita a mensagem do libpq.
            raise psycopg.OperationalError(
                f"Falha de conexão ao PostgreSQL ({type(erro).__name__}, SQLSTATE={erro.sqlstate or 'indisponível'})."
            ) from None


def obter_pool():
    extensoes = current_app.extensions
    with TRAVA_POOL:
        if "pool_postgresql" not in extensoes:
            extensoes["pool_postgresql"] = ConnectionPool(
                kwargs=dict(argumentos_conexao(None), prepare_threshold=None),
                connection_class=ConexaoWeb, name="geogarimpo-web",
                min_size=0, max_size=2, num_workers=2, timeout=4, max_waiting=6,
                max_idle=60, reconnect_timeout=10, open=True,
            )
        return extensoes["pool_postgresql"]


@contextmanager
def conectar(*, escrita=False, configuracao=None):
    usar_pool = has_app_context() and not escrita and configuracao is None
    abrir = obter_pool().connection() if usar_pool else psycopg.connect(**argumentos_conexao(configuracao))
    with abrir as conexao:
        if not escrita:
            conexao.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY; "
                            "SET LOCAL statement_timeout = '5s'")
        yield conexao


def tabelas_disponiveis(conexao, *tabelas):
    return {linha["tabela"] for linha in conexao.execute("""
        SELECT tabela FROM unnest(%s::text[]) AS tabela
        WHERE to_regclass(tabela) IS NOT NULL
    """, (list(tabelas),))}
