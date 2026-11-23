from shutil import disk_usage

MIB = 1024 ** 2
GIB = 1024 ** 3


def estimar(poligonos, bytes_wkb):
    temporarios = bytes_wkb + poligonos * 192
    permanentes = bytes_wkb + poligonos * 288
    return dict(temporarios=temporarios, permanentes=permanentes, wal=2 * permanentes)


def conferir_local(pasta, bytes_entrada):
    pasta = pasta.resolve()
    while not pasta.exists():
        pasta = pasta.parent
    necessario = max(GIB, 3 * bytes_entrada)
    livre = disk_usage(pasta).free
    if livre < necessario:
        raise ValueError(f"Workspace sem espaço para o cache anual: {livre // MIB} MiB livres, "
                         f"{necessario // MIB} MiB estimados.")


def conferir_banco(conexao, estimativa, *, exigir=True):
    local = conexao.execute("""
        SELECT current_setting('temp_tablespaces') AS temporario,
               pg_size_bytes(current_setting('max_wal_size')) AS wal_configurado,
               (SELECT spcname FROM pg_tablespace WHERE oid = d.dattablespace) AS tablespace,
               EXISTS (
                   SELECT 1 FROM pg_class c WHERE c.reltablespace <> 0 AND
                   (c.oid IN ('public.garimpos_anuais'::regclass, 'public.anos_garimpo'::regclass,
                              'public.fontes_dados'::regclass) OR c.oid IN
                    (SELECT indexrelid FROM pg_index WHERE indrelid IN ('public.garimpos_anuais'::regclass,
                        'public.anos_garimpo'::regclass, 'public.fontes_dados'::regclass)))
               ) AS moveu_relacoes
        FROM pg_database d WHERE d.datname = current_database()
    """).fetchone()
    if local["temporario"] or local["tablespace"] != "pg_default" or local["moveu_relacoes"]:
        raise ValueError("A carga requer conferência dos volumes dos tablespaces personalizados.")
    if not conexao.execute("SELECT rolsuper OR pg_has_role('pg_execute_server_program','USAGE') AS permitido "
                           "FROM pg_roles WHERE rolname=current_user").fetchone()["permitido"]:
        raise ValueError("A conexão não pode medir o volume da VM. Execute a carga por um operador autorizado.")
    conexao.execute("CREATE TEMP TABLE espaco_mapbiomas_sonda (linha text) ON COMMIT DROP")
    conexao.execute("COPY espaco_mapbiomas_sonda FROM PROGRAM '/bin/df -P -k . pg_wal'")
    linhas = [linha["linha"].split() for linha in conexao.execute("SELECT linha FROM espaco_mapbiomas_sonda")]
    if len(linhas) != 3 or any(len(linha) < 6 or not linha[3].isdigit() for linha in linhas[1:]):
        raise ValueError("Não foi possível interpretar o espaço real do PostgreSQL e do WAL.")
    dados, wal = linhas[1:]
    livre_dados, livre_wal = int(dados[3]) * 1024, int(wal[3]) * 1024
    reserva_dados = GIB + estimativa["temporarios"] + estimativa["permanentes"]
    reserva_wal = local["wal_configurado"] + estimativa["wal"]
    if dados[0] == wal[0]:
        suficiente = livre_dados >= reserva_dados + reserva_wal
    else:
        suficiente = livre_dados >= reserva_dados and livre_wal >= reserva_wal
    resumo = dict(livre_dados_mib=livre_dados // MIB, livre_wal_mib=livre_wal // MIB,
                  estimativa_dados_mib=(reserva_dados + MIB - 1) // MIB,
                  estimativa_wal_mib=(reserva_wal + MIB - 1) // MIB,
                  mesmo_volume=dados[0] == wal[0], suficiente=suficiente)
    if exigir and not suficiente:
        raise ValueError(f"Espaço insuficiente para o ano, com margem para dados, temporários e WAL. {resumo}")
    return resumo
