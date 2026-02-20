#!/usr/bin/env python3
"""
=============================================================
  RADAR MANAGER — Servidor Web para Windows
  Sistema de Gerenciamento Central de Radares
=============================================================
  - Escaneamento de rede para descobrir radares
  - Dashboard em tempo real (velocidades, alertas)
  - Configuração em massa de todos os radares
  - Visualização do PostgreSQL central
  - Interface web acessível no navegador
=============================================================
  Instalar: pip install flask requests psycopg2-binary
  Executar:  python manager_server.py
  Acessar:   http://localhost:8080
=============================================================
"""

import os
import json
import socket
import threading
import time
import ipaddress
import subprocess
from datetime import datetime, timedelta
from pathlib import Path

import requests
from flask import Flask, render_template, jsonify, request, Response

try:
    import psycopg2
    import psycopg2.extras
    PG_AVAILABLE = True
except ImportError:
    PG_AVAILABLE = False

# ─────────────────────────────────────────────
#  CONFIGURAÇÃO DO MANAGER
# ─────────────────────────────────────────────
CONFIG_FILE = Path("manager_config.json")
MANAGER_PORT = 8080
RADAR_API_PORT = 5000
SCAN_TIMEOUT = 1.5  # segundos por host no scan

# Carrega/cria configuração persistente
def carregar_config():
    default = {
        "pg_host": "", "pg_port": 5432, "pg_db": "radares",
        "pg_user": "", "pg_pass": "",
        "api_token": "mude-este-token",
        "speed_limit": 20.0,
        "sensor_dist_m": 1.0,
        "sensor_a_pin": 17,
        "sensor_b_pin": 27,
        "sync_interval": 30,
        "radares_conhecidos": {}
    }
    if CONFIG_FILE.exists():
        try:
            saved = json.loads(CONFIG_FILE.read_text())
            default.update(saved)
        except Exception:
            pass
    return default

def salvar_config(cfg: dict):
    CONFIG_FILE.write_text(json.dumps(cfg, indent=2, ensure_ascii=False))

config = carregar_config()

# ─────────────────────────────────────────────
#  CACHE DE STATUS DOS RADARES
# ─────────────────────────────────────────────
status_radares = {}   # radar_id -> {status, ultima_atualizacao, ...}
eventos_recentes = [] # lista de detecções em tempo real
eventos_lock = threading.Lock()


def atualizar_status_radar(radar_id: str, dados: dict):
    status_radares[radar_id] = {
        **dados,
        "ultima_atualizacao": datetime.now().isoformat()
    }


def adicionar_evento(evento: dict):
    with eventos_lock:
        eventos_recentes.insert(0, evento)
        if len(eventos_recentes) > 200:
            eventos_recentes.pop()


# ─────────────────────────────────────────────
#  COMUNICAÇÃO COM RADARES
# ─────────────────────────────────────────────
def chamar_radar(ip: str, endpoint: str, method: str = "GET", payload: dict = None):
    """Faz requisição autenticada a um radar."""
    url = f"http://{ip}:{RADAR_API_PORT}/api/{endpoint}"
    headers = {"Authorization": f"Bearer {config['api_token']}"}
    try:
        if method == "POST":
            r = requests.post(url, json=payload, headers=headers, timeout=4)
        else:
            r = requests.get(url, headers=headers, timeout=4)
        return r.json(), r.status_code
    except Exception as e:
        return {"erro": str(e)}, 0


def descobrir_radar(ip: str) -> dict | None:
    """Tenta /api/ping em um IP para ver se é um radar."""
    try:
        r = requests.get(
            f"http://{ip}:{RADAR_API_PORT}/api/ping",
            timeout=SCAN_TIMEOUT
        )
        if r.status_code == 200:
            dados = r.json()
            if dados.get("servico") == "radar":
                return {"ip": ip, **dados}
    except Exception:
        pass
    return None


def escanear_rede(rede_cidr: str) -> list:
    """Escaneia uma rede CIDR procurando radares."""
    encontrados = []
    lock = threading.Lock()

    def checar_ip(ip):
        r = descobrir_radar(str(ip))
        if r:
            with lock:
                encontrados.append(r)
                # Registra radar conhecido
                config["radares_conhecidos"][r["radar_id"]] = {
                    "ip": str(ip),
                    "radar_nome": r.get("radar_nome", ""),
                    "descoberto_em": datetime.now().isoformat()
                }
                salvar_config(config)

    try:
        rede = ipaddress.ip_network(rede_cidr, strict=False)
        threads = []
        for ip in rede.hosts():
            t = threading.Thread(target=checar_ip, args=(ip,))
            t.daemon = True
            threads.append(t)
            t.start()
            # Limita concorrência
            if len([x for x in threads if x.is_alive()]) >= 50:
                time.sleep(0.1)

        for t in threads:
            t.join(timeout=SCAN_TIMEOUT + 1)

    except ValueError as e:
        return [{"erro": str(e)}]

    return encontrados


def detectar_rede_local() -> str:
    """Detecta a rede local automaticamente."""
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip_local = s.getsockname()[0]
        s.close()
        # Assume /24
        partes = ip_local.split(".")
        return f"{partes[0]}.{partes[1]}.{partes[2]}.0/24"
    except Exception:
        return "192.168.1.0/24"


# ─────────────────────────────────────────────
#  POLLING EM TEMPO REAL
# ─────────────────────────────────────────────
def loop_polling():
    """Faz polling periódico em todos os radares conhecidos."""
    while True:
        for radar_id, info in list(config["radares_conhecidos"].items()):
            ip = info.get("ip")
            if not ip:
                continue
            try:
                status, code = chamar_radar(ip, "status")
                if code == 200:
                    status["ip"] = ip
                    status["online"] = True
                    atualizar_status_radar(radar_id, status)

                    # Busca eventos novos
                    evts, code2 = chamar_radar(ip, "eventos")
                    if code2 == 200 and isinstance(evts, list):
                        for ev in evts[:5]:  # máximo 5 por polling
                            ev_key = ev.get("uuid", "")
                            if ev_key and not any(
                                e.get("uuid") == ev_key for e in eventos_recentes[:50]
                            ):
                                adicionar_evento(ev)
                else:
                    atualizar_status_radar(radar_id, {
                        "ip": ip, "online": False,
                        "radar_id": radar_id,
                        "radar_nome": info.get("radar_nome", radar_id)
                    })
            except Exception:
                pass
        time.sleep(5)


# ─────────────────────────────────────────────
#  POSTGRESQL CENTRAL
# ─────────────────────────────────────────────
def get_pg():
    if not PG_AVAILABLE or not config.get("pg_host"):
        return None
    try:
        return psycopg2.connect(
            host=config["pg_host"], port=config["pg_port"],
            dbname=config["pg_db"], user=config["pg_user"],
            password=config["pg_pass"], connect_timeout=5
        )
    except Exception:
        return None


def pg_estatisticas():
    """Busca estatísticas do banco central."""
    pg = get_pg()
    if not pg:
        return None
    try:
        c = pg.cursor(cursor_factory=psycopg2.extras.RealDictCursor)

        c.execute("""
            SELECT
                COUNT(*) as total,
                COUNT(*) FILTER (WHERE acima_limite) as infrações,
                AVG(velocidade) as media_velocidade,
                MAX(velocidade) as max_velocidade,
                COUNT(DISTINCT radar_id) as total_radares
            FROM radar_deteccoes
            WHERE timestamp > NOW() - INTERVAL '24 hours'
        """)
        hoje = dict(c.fetchone())

        c.execute("""
            SELECT radar_id, radar_nome,
                   COUNT(*) as deteccoes,
                   AVG(velocidade) as media,
                   MAX(velocidade) as maxima,
                   COUNT(*) FILTER (WHERE acima_limite) as infracoes
            FROM radar_deteccoes
            WHERE timestamp > NOW() - INTERVAL '24 hours'
            GROUP BY radar_id, radar_nome
            ORDER BY deteccoes DESC
        """)
        por_radar = [dict(r) for r in c.fetchall()]

        c.execute("""
            SELECT
                date_trunc('hour', timestamp) as hora,
                COUNT(*) as deteccoes,
                AVG(velocidade) as media
            FROM radar_deteccoes
            WHERE timestamp > NOW() - INTERVAL '24 hours'
            GROUP BY hora
            ORDER BY hora
        """)
        por_hora = [dict(r) for r in c.fetchall()]
        for r in por_hora:
            if r.get("hora"):
                r["hora"] = r["hora"].isoformat()

        pg.close()
        return {"hoje": hoje, "por_radar": por_radar, "por_hora": por_hora}
    except Exception as e:
        pg.close()
        return {"erro": str(e)}


# ─────────────────────────────────────────────
#  FLASK — ROTAS DA API
# ─────────────────────────────────────────────
app = Flask(__name__, template_folder="templates", static_folder="static")


@app.route("/")
def index():
    return render_template("dashboard.html")


@app.route("/api/config", methods=["GET"])
def api_get_config():
    cfg = dict(config)
    cfg.pop("pg_pass", None)  # nunca envia a senha para o frontend
    return jsonify(cfg)


@app.route("/api/config", methods=["POST"])
def api_set_config():
    dados = request.get_json()
    campos_permitidos = [
        "pg_host", "pg_port", "pg_db", "pg_user", "pg_pass",
        "api_token", "speed_limit", "sensor_dist_m",
        "sensor_a_pin", "sensor_b_pin", "sync_interval"
    ]
    for campo in campos_permitidos:
        if campo in dados:
            config[campo] = dados[campo]
    salvar_config(config)
    return jsonify({"status": "ok"})


@app.route("/api/rede/detectar", methods=["GET"])
def api_detectar_rede():
    return jsonify({"rede": detectar_rede_local()})


@app.route("/api/rede/escanear", methods=["POST"])
def api_escanear():
    dados = request.get_json()
    rede = dados.get("rede", detectar_rede_local())
    radares = escanear_rede(rede)
    return jsonify({"radares": radares, "total": len(radares)})


@app.route("/api/radares", methods=["GET"])
def api_radares():
    """Lista todos os radares conhecidos com status."""
    resultado = []
    for radar_id, info in config["radares_conhecidos"].items():
        status = status_radares.get(radar_id, {})
        resultado.append({
            "radar_id": radar_id,
            "ip": info.get("ip"),
            "radar_nome": info.get("radar_nome", radar_id),
            "descoberto_em": info.get("descoberto_em"),
            "online": status.get("online", False),
            **status
        })
    return jsonify(resultado)


@app.route("/api/radares/<radar_id>/status", methods=["GET"])
def api_radar_status(radar_id):
    info = config["radares_conhecidos"].get(radar_id)
    if not info:
        return jsonify({"erro": "Radar não encontrado"}), 404
    status, code = chamar_radar(info["ip"], "status")
    return jsonify(status), code


@app.route("/api/radares/<radar_id>/configurar", methods=["POST"])
def api_radar_configurar(radar_id):
    """Envia configuração para um radar específico."""
    info = config["radares_conhecidos"].get(radar_id)
    if not info:
        return jsonify({"erro": "Radar não encontrado"}), 404

    payload = request.get_json() or {}
    # Mescla com a config global
    cfg_enviar = {
        "pg_host": config["pg_host"],
        "pg_port": config["pg_port"],
        "pg_db": config["pg_db"],
        "pg_user": config["pg_user"],
        "pg_pass": config["pg_pass"],
        "api_token": config["api_token"],
        "speed_limit": config["speed_limit"],
        "sensor_dist_m": config["sensor_dist_m"],
        "sensor_a_pin": config["sensor_a_pin"],
        "sensor_b_pin": config["sensor_b_pin"],
        "sync_interval": config["sync_interval"],
        **payload
    }
    resp, code = chamar_radar(info["ip"], "configurar", method="POST", payload=cfg_enviar)
    return jsonify(resp), code


@app.route("/api/radares/configurar-todos", methods=["POST"])
def api_configurar_todos():
    """Envia a config atual para TODOS os radares conhecidos."""
    payload = request.get_json() or {}
    resultados = {}
    cfg_enviar = {
        "pg_host": config["pg_host"],
        "pg_port": config["pg_port"],
        "pg_db": config["pg_db"],
        "pg_user": config["pg_user"],
        "pg_pass": config["pg_pass"],
        "api_token": config["api_token"],
        "speed_limit": config["speed_limit"],
        "sensor_dist_m": config["sensor_dist_m"],
        "sensor_a_pin": config["sensor_a_pin"],
        "sensor_b_pin": config["sensor_b_pin"],
        "sync_interval": config["sync_interval"],
        **payload
    }

    def enviar(rid, ip):
        resp, code = chamar_radar(ip, "configurar", method="POST", payload=cfg_enviar)
        resultados[rid] = {"status": code, "resposta": resp}

    threads = []
    for rid, info in config["radares_conhecidos"].items():
        t = threading.Thread(target=enviar, args=(rid, info["ip"]))
        threads.append(t)
        t.start()
    for t in threads:
        t.join(timeout=6)

    return jsonify(resultados)


@app.route("/api/radares/<radar_id>/reiniciar", methods=["POST"])
def api_radar_reiniciar(radar_id):
    info = config["radares_conhecidos"].get(radar_id)
    if not info:
        return jsonify({"erro": "Radar não encontrado"}), 404
    resp, code = chamar_radar(info["ip"], "reiniciar", method="POST")
    return jsonify(resp), code


@app.route("/api/radares/<radar_id>/remover", methods=["DELETE"])
def api_radar_remover(radar_id):
    if radar_id in config["radares_conhecidos"]:
        del config["radares_conhecidos"][radar_id]
        salvar_config(config)
    if radar_id in status_radares:
        del status_radares[radar_id]
    return jsonify({"status": "ok"})


@app.route("/api/eventos", methods=["GET"])
def api_eventos():
    with eventos_lock:
        return jsonify(list(eventos_recentes[:50]))


@app.route("/api/stream")
def api_stream():
    """Server-Sent Events para atualizações em tempo real."""
    def gerar():
        ultimo_tamanho = 0
        while True:
            with eventos_lock:
                tamanho_atual = len(eventos_recentes)
                if tamanho_atual > ultimo_tamanho:
                    novos = eventos_recentes[:tamanho_atual - ultimo_tamanho]
                    for ev in reversed(novos):
                        yield f"data: {json.dumps(ev)}\n\n"
                    ultimo_tamanho = tamanho_atual

            # Envia heartbeat a cada 5s
            yield f"event: heartbeat\ndata: {datetime.now().isoformat()}\n\n"
            time.sleep(2)

    return Response(gerar(), mimetype="text/event-stream",
                    headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


@app.route("/api/pg/status", methods=["GET"])
def api_pg_status():
    pg = get_pg()
    if not pg:
        return jsonify({"conectado": False, "motivo": "Sem configuração ou falha de conexão"})
    pg.close()
    return jsonify({"conectado": True})


@app.route("/api/pg/estatisticas", methods=["GET"])
def api_pg_stats():
    stats = pg_estatisticas()
    if stats is None:
        return jsonify({"erro": "PostgreSQL não configurado"}), 503
    return jsonify(stats)


# ─────────────────────────────────────────────
#  MAIN
# ─────────────────────────────────────────────
if __name__ == "__main__":
    print("=" * 55)
    print("  RADAR MANAGER — Sistema de Gerenciamento Central")
    print("=" * 55)
    print(f"  Dashboard: http://localhost:{MANAGER_PORT}")
    print(f"  Config salva em: {CONFIG_FILE.absolute()}")
    print("=" * 55)

    # Inicia polling em background
    threading.Thread(target=loop_polling, daemon=True).start()
    print("  Polling de radares iniciado (a cada 5s)")

    # Abre browser automaticamente no Windows
    try:
        import webbrowser
        threading.Timer(1.5, lambda: webbrowser.open(f"http://localhost:{MANAGER_PORT}")).start()
    except Exception:
        pass

    app.run(host="0.0.0.0", port=MANAGER_PORT, debug=False, threaded=True)
