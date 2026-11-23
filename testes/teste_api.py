import psycopg
import pytest
from psycopg_pool import PoolTimeout

from db.conexao import argumentos_conexao, conectar


@pytest.mark.parametrize("pagina", ["/", "/mapa", "/estatisticas", "/analise", "/sobre"])
def test_pagina_responde_sem_acessar_banco(cliente, monkeypatch, pagina):
    def acesso_invalido():
        pytest.fail("Uma página HTML não deve depender da disponibilidade do banco.")

    monkeypatch.setattr("db.conexao.obter_pool", acesso_invalido)
    resposta = cliente.get(pagina)
    assert resposta.status_code == 200
    assert "GeoGarimpo Brasil" in resposta.text
    assert 'lang="pt-BR"' in resposta.text


@pytest.mark.parametrize("url", [
    "/api/garimpos", "/api/garimpos?ano=1984", "/api/garimpos?ano=1%20OR%201=1",
    "/api/garimpos?ano=2020&ano=2021", "/api/garimpos?ano=2020&bbox=nan,0,1,1",
    "/api/garimpos?ano=2020&bbox=12,5,4,6", "/api/terras-indigenas?limite=9999",
    "/api/estatisticas/evolucao?uf=XX", "/api/estatisticas/evolucao?inicio=2022&fim=2020",
    "/api/estatisticas/terra-indigena/∞", "/api/analise/proximidade?ano=2020&raio=-10",
    "/api/analise/proximidade?ano=2020&alvo=rodovias", "/api/analise/expansao?ano=2020&formato=xls",
    "/api/rios?bbox=-74,-34,-29,5", "/api/anos?comando=sql", "/api/saude?extra=1",
])
def test_parametros_invalidos(cliente, url):
    resposta = cliente.get(url)
    assert resposta.status_code == 400
    assert resposta.json["erro"]


def test_falha_banco_nao_vaza_detalhes(cliente, monkeypatch):
    def falha():
        raise PoolTimeout("senha, SQL e detalhes privados")

    monkeypatch.setattr("db.conexao.obter_pool", falha)
    resposta = cliente.get("/api/saude")
    assert resposta.status_code == 503
    assert resposta.headers["Retry-After"]
    assert "Banco indisponível" in resposta.json["erro"]
    assert "detalhes privados" not in resposta.text
    assert cliente.get("/").status_code == 200


def test_timeout_mapeado_para_504(cliente, monkeypatch, caplog):
    def falha(_):
        raise psycopg.errors.QueryCanceled("conteúdo privado da consulta")

    monkeypatch.setattr("controle.controle_garimpo.geometrias", falha)
    resposta = cliente.get("/api/garimpos?ano=2025")
    assert resposta.status_code == 504
    assert "57014" in caplog.text
    assert "conteúdo privado" not in caplog.text
    assert "conteúdo privado" not in resposta.text


def test_banco_parcial_sem_tabelas_opcionais(cliente, monkeypatch):
    from contextlib import nullcontext
    from unittest.mock import Mock

    conexao = Mock()
    conexao.execute.return_value = []
    monkeypatch.setattr("controle.controle_estatisticas.conectar", lambda: nullcontext(conexao))
    resposta = cliente.get("/api/estatisticas/evolucao")
    assert resposta.status_code == 200
    assert resposta.json["dados"] == []
    assert resposta.json["estado"] == "pendente"
    assert conexao.execute.call_count == 1

    def acesso_indevido():
        pytest.fail("As redes desabilitadas não devem acessar o banco.")

    monkeypatch.setattr("controle.controle_mapa.conectar", acesso_indevido)
    monkeypatch.setattr("controle.controle_analise.conectar", acesso_indevido)
    for url in ("/api/rios?bbox=-53,-14,-50,-12", "/api/analise/proximidade?ano=2025"):
        resposta = cliente.get(url)
        assert resposta.status_code == 200
        assert resposta.json["metadados"]["estado"] == "alvo_ausente"


def test_recurso_e_metodo_http(cliente):
    assert cliente.get("/nao-existe").status_code == 404
    assert cliente.get("/api/nao-existe").json["erro"]
    assert cliente.post("/api/anos").status_code == 405


def test_health_independe_do_banco(cliente, monkeypatch):
    def nao_conectar():
        pytest.fail("A verificação HTTP não deve abrir conexões.")

    monkeypatch.setattr("db.conexao.obter_pool", nao_conectar)
    resposta = cliente.get("/health")
    assert resposta.status_code == 200
    assert resposta.json == {"estado": "ok"}
    assert resposta.headers["Cache-Control"] == "no-store"


def test_configuracao_usa_variaveis_de_producao(monkeypatch):
    valores = {
        "DB_HOST": "pooler-teste.pooler.supabase.com", "DB_PORTA": "6543",
        "DB_NOME": "banco_teste", "DB_USUARIO": "usuario_teste", "DB_SENHA": "segredo_teste",
    }
    for variavel, valor in valores.items():
        monkeypatch.setenv(variavel, valor)

    def arquivo_desnecessario(*args, **kwargs):
        pytest.fail("A configuração por variáveis de ambiente dispensa o arquivo local.")

    monkeypatch.setattr("db.conexao.dotenv_values", arquivo_desnecessario)
    argumentos = argumentos_conexao(None)
    assert (argumentos["host"], argumentos["port"], argumentos["dbname"]) == (
        valores["DB_HOST"], valores["DB_PORTA"], valores["DB_NOME"]
    )
    assert (argumentos["user"], argumentos["password"]) == (valores["DB_USUARIO"], valores["DB_SENHA"])
    assert argumentos["sslmode"] == "require"
    assert argumentos["prepare_threshold"] is None


def test_conexao_postgis(banco_vazio):
    with conectar(configuracao=banco_vazio) as conexao:
        linha = conexao.execute("SELECT postgis_version() AS versao, current_database() AS banco").fetchone()
    assert linha["versao"]
    assert linha["banco"].startswith("geo_garimpo_teste_")


def test_banco_vazio(cliente_vazio):
    assert cliente_vazio.get("/api/saude").json["tem_anos"] is False
    assert cliente_vazio.get("/api/anos").json == {"anos": [], "fontes": [], "primeiro": None, "ultimo": None}
    assert cliente_vazio.get("/api/estatisticas/evolucao").json["dados"] == []
    assert cliente_vazio.get("/api/fontes").json["dados"] == []
    assert cliente_vazio.get("/api/estatisticas/terra-indigena/400").status_code == 404
    assert cliente_vazio.get("/api/terras-indigenas?formato=lista").json["proximo"] is None
    for url in ("/api/terras-indigenas", "/api/garimpos?ano=2020", "/api/analise/expansao?ano=2020",
                "/api/analise/proximidade?ano=2020", "/api/rios?bbox=-53,-14,-50,-12"):
        resposta = cliente_vazio.get(url)
        assert resposta.status_code == 200
        assert resposta.mimetype == "application/geo+json"
        assert resposta.json["type"] == "FeatureCollection"
        assert resposta.json["features"] == []
    resposta = cliente_vazio.get("/api/analise/proximidade?ano=2020&formato=csv")
    assert resposta.status_code == 200
    assert resposta.headers["X-Estado-Analise"] == "sem_serie"
    assert "id,ano,area_ha,distancia_m,feicao_id,fonte_id" in resposta.text
