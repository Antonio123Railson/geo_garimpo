from flask import Blueprint, render_template

from controle.controle_mapa import redes_habilitadas
from controle.validacao import UFS

paginas = Blueprint("paginas", __name__)


@paginas.get("/")
def inicio():
    return render_template("index.html")


@paginas.get("/mapa")
def mapa():
    return render_template("mapa.html", ufs=UFS, redes_habilitadas=redes_habilitadas())


@paginas.get("/estatisticas")
def estatisticas():
    return render_template("estatisticas.html", ufs=UFS)


@paginas.get("/analise")
def analise():
    return render_template("analise.html", ufs=UFS, redes_habilitadas=redes_habilitadas())


@paginas.get("/sobre")
def sobre():
    return render_template("sobre.html")
