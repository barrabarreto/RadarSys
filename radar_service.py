#!/usr/bin/env python3
"""
=============================================================
  RADAR SERVICE - Raspberry Pi Zero W
  Sistema de Radar para Condomínios
  Autor: Gerado automaticamente
=============================================================
  Funcionalidades:
  - Leitura de sensores IR via GPIO
  - Cálculo de velocidade por 2 sensores com distância fixa
  - Buffer local SQLite (funciona offline)
  - API REST Flask para o Manager se conectar
  - Sincronização automática com PostgreSQL central
  - Configuração via arquivo .env (enviado pelo Manager)
=============================================================
"""

import os
import time
import json
import uuid
import logging
import sqlite3
import threading
import socket
from datetime import datetime
from pathlib import Path

# Flask e extras
from flask import Flask, jsonify, request, abort
from functools import wraps

# GPIO (só disponível no Pi)
try:
    import RPi.GPIO as GPIO
    GPIO_AVAILABLE = True
except ImportError:
    GPIO_AVAILABLE = False
    print("[AVISO] RPi.GPIO não disponível - rodando em modo simulação")

# PostgreSQL
try:
    import psycopg2
    import psycopg2.extras
    PG_AVAILABLE = True
except ImportError:
    PG_AVAILABLE = False
    print("[AVISO] psycopg2 não instalado - sync com PostgreSQL desabilitado")

# Dotenv
try:
    from dotenv import load_dotenv
    load_dotenv("/etc/radar/.env")
except ImportError:
    pass

# ─────────────────────────────────────────────
#  CONFIGURAÇÕES (lidas do .env)
# ─────────────────────────────────────────────
RADAR_ID       = os.getenv("RADAR_ID", str(uuid.uuid4())[:8])
RADAR_NAME     = os.getenv("RADAR_NAME", socket.gethostname())
RADAR_LOCATION = os.getenv("RADAR_LOCATION", "Não configurado")
API_TOKEN      = os.getenv("API_TOKEN", "mude-este-token")

# GPIO
SENSOR_A_PIN   = int(os.getenv("SENSOR_A_PIN", "17"))  # Sensor de entrada
SENSOR_B_PIN   = int(os.getenv("SENSOR_B_PIN", "27"))  # Sensor de saída
SENSOR_DIST_M  = float(os.getenv("SENSOR_DIST_M", "1.0"))  # Distância entre sensores em metros
SPEED_LIMIT    = float(os.getenv("SPEED_LIMIT", "20.0"))    # Limite de velocidade km/h

# PostgreSQL Central
PG_HOST        = os.getenv("PG_HOST", "")
PG_PORT        = int(os.getenv("PG_PORT", "5432"))
PG_DB          = os.getenv("PG_DB", "")
PG_USER        = os.getenv("PG_USER", "")
PG_PASS        = os.getenv("PG_PASS", "")

# SQLite local
SQLITE_PATH    = os.getenv("SQLITE_PATH", "/var/lib/radar/radar_local.db")
SYNC_INTERVAL  = int(os.getenv("SYNC_INTERVAL", "30"))  # segundos

# API
API_PORT       = int(os.getenv("API_PORT", "5000"))

# ─────────────────────────────────────────────
#  LOGGING
# ─────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler("/var/log/radar_service.log")
    ]
)
log = logging.getLogger("RadarService")

# ─────────────────────────────────────────────
#  BANCO DE DADOS LOCAL (SQLite)
# ─────────────────────────────────────────────
def init_sqlite():
    """Cria tabelas no SQLite local."""
    Path(SQLITE_PATH).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(SQLITE_PATH)
    c = conn.cursor()

    c.execute("""
        CREATE TABLE IF NOT EXISTS deteccoes (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            uuid        TEXT    NOT NULL UNIQUE,
            radar_id    TEXT    NOT NULL,
            timestamp   TEXT    NOT NULL,
            velocidade  REAL    NOT NULL,
            direcao     TEXT    DEFAULT 'A->B',
            acima_limite INTEGER DEFAULT 0,
            sincronizado INTEGER DEFAULT 0
        )
    """)

    c.execute("""
        CREATE TABLE IF NOT EXISTS config (
            chave TEXT PRIMARY KEY,
            valor TEXT
        )
    """)

    c.execute("""
        CREATE TABLE IF NOT EXISTS status_log (
            id        INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp TEXT,
            evento    TEXT,
            detalhe   TEXT
        )
    """)

    conn.commit()
    conn.close()
    log.info(f"SQLite iniciado em: {SQLITE_PATH}")


def salvar_deteccao(velocidade: float, direcao: str = "A->B"):
    """Salva uma detecção no SQLite local."""
    conn = sqlite3.connect(SQLITE_PATH)
    c = conn.cursor()
    uid = str(uuid.uuid4())
    ts = datetime.now().isoformat()
    acima = 1 if velocidade > SPEED_LIMIT else 0

    c.execute("""
        INSERT INTO deteccoes (uuid, radar_id, timestamp, velocidade, direcao, acima_limite)
        VALUES (?, ?, ?, ?, ?, ?)
    """, (uid, RADAR_ID, ts, velocidade, direcao, acima))

    conn.commit()
    conn.close()

    log.info(f"Detecção salva: {velocidade:.1f} km/h | Acima do limite: {'SIM' if acima else 'NÃO'}")
    return uid


def get_deteccoes_nao_sincronizadas():
    """Retorna detecções ainda não enviadas ao PostgreSQL."""
    conn = sqlite3.connect(SQLITE_PATH)
    conn.row_factory = sqlite3.Row
    c = conn.cursor()
    c.execute("SELECT * FROM deteccoes WHERE sincronizado = 0 ORDER BY id LIMIT 100")
    rows = [dict(r) for r in c.fetchall()]
    conn.close()
    return rows


def marcar_sincronizado(uuids: list):
    """Marca registros como sincronizados."""
    conn = sqlite3.connect(SQLITE_PATH)
    c = conn.cursor()
    c.executemany("UPDATE deteccoes SET sincronizado = 1 WHERE uuid = ?", [(u,) for u in uuids])
    conn.commit()
    conn.close()


# ─────────────────────────────────────────────
#  SINCRONIZAÇÃO COM POSTGRESQL CENTRAL
# ─────────────────────────────────────────────
def get_pg_connection():
    """Cria conexão com o PostgreSQL central."""
    if not PG_AVAILABLE or not PG_HOST:
        return None
    try:
        conn = psycopg2.connect(
            host=PG_HOST, port=PG_PORT,
            dbname=PG_DB, user=PG_USER, password=PG_PASS,
            connect_timeout=5
        )
        return conn
    except Exception as e:
        log.warning(f"Falha ao conectar PostgreSQL: {e}")
        return None


def garantir_tabela_pg(pg_conn):
    """Cria tabela no PostgreSQL se não existir."""
    c = pg_conn.cursor()
    c.execute("""
        CREATE TABLE IF NOT EXISTS radar_deteccoes (
            id          SERIAL PRIMARY KEY,
            uuid        TEXT    NOT NULL UNIQUE,
            radar_id    TEXT    NOT NULL,
            radar_nome  TEXT,
            timestamp   TIMESTAMPTZ NOT NULL,
            velocidade  NUMERIC(6,2) NOT NULL,
            direcao     TEXT,
            acima_limite BOOLEAN DEFAULT FALSE,
            criado_em   TIMESTAMPTZ DEFAULT NOW()
        )
    """)
    pg_conn.commit()


def sincronizar_com_central():
    """Envia detecções pendentes para o PostgreSQL central."""
    pendentes = get_deteccoes_nao_sincronizadas()
    if not pendentes:
        return

    pg = get_pg_connection()
    if not pg:
        log.debug("PostgreSQL indisponível, tentando depois...")
        return

    try:
        garantir_tabela_pg(pg)
        c = pg.cursor()
        enviados = []

        for det in pendentes:
            try:
                c.execute("""
                    INSERT INTO radar_deteccoes
                        (uuid, radar_id, radar_nome, timestamp, velocidade, direcao, acima_limite)
                    VALUES (%s, %s, %s, %s, %s, %s, %s)
                    ON CONFLICT (uuid) DO NOTHING
                """, (
                    det["uuid"], det["radar_id"], RADAR_NAME,
                    det["timestamp"], det["velocidade"],
                    det["direcao"], bool(det["acima_limite"])
                ))
                enviados.append(det["uuid"])
            except Exception as e:
                log.error(f"Erro ao inserir detecção {det['uuid']}: {e}")

        pg.commit()
        pg.close()

        if enviados:
            marcar_sincronizado(enviados)
            log.info(f"Sincronizados {len(enviados)} registros com PostgreSQL central")

    except Exception as e:
        log.error(f"Erro na sincronização: {e}")
        pg.close()


def loop_sincronizacao():
    """Thread que sincroniza periodicamente."""
    while True:
        try:
            sincronizar_com_central()
        except Exception as e:
            log.error(f"Erro no loop de sync: {e}")
        time.sleep(SYNC_INTERVAL)


# ─────────────────────────────────────────────
#  LEITURA DE SENSORES IR (GPIO)
# ─────────────────────────────────────────────
sensor_a_tempo = None  # Timestamp quando sensor A detectou
sensor_lock = threading.Lock()

# Buffer para envio em tempo real via SSE
eventos_realtime = []
eventos_lock = threading.Lock()


def adicionar_evento_realtime(dados: dict):
    """Adiciona evento ao buffer de tempo real."""
    with eventos_lock:
        eventos_realtime.append(dados)
        if len(eventos_realtime) > 50:  # máximo 50 eventos no buffer
            eventos_realtime.pop(0)


def callback_sensor_a(channel):
    """Callback quando sensor A detecta objeto."""
    global sensor_a_tempo
    with sensor_lock:
        sensor_a_tempo = time.time()
        log.debug(f"Sensor A ativado (GPIO {SENSOR_A_PIN})")


def callback_sensor_b(channel):
    """Callback quando sensor B detecta objeto — calcula velocidade."""
    global sensor_a_tempo
    with sensor_lock:
        if sensor_a_tempo is None:
            log.debug("Sensor B ativado mas A não registrou antes — ignorando")
            return

        delta_t = time.time() - sensor_a_tempo
        sensor_a_tempo = None

        if delta_t <= 0 or delta_t > 10:  # ignora medições inválidas (>10s = irrelevante)
            return

        # Velocidade = distância / tempo, convertendo para km/h
        velocidade_ms = SENSOR_DIST_M / delta_t
        velocidade_kmh = velocidade_ms * 3.6

        if velocidade_kmh > 200:  # ignora leituras absurdas
            return

        log.info(f"Velocidade calculada: {velocidade_kmh:.2f} km/h (Δt={delta_t:.3f}s)")
        uid = salvar_deteccao(velocidade_kmh, "A->B")

        evento = {
            "uuid": uid,
            "radar_id": RADAR_ID,
            "radar_nome": RADAR_NAME,
            "velocidade": round(velocidade_kmh, 2),
            "limite": SPEED_LIMIT,
            "acima_limite": velocidade_kmh > SPEED_LIMIT,
            "timestamp": datetime.now().isoformat(),
            "direcao": "A->B"
        }
        adicionar_evento_realtime(evento)


def iniciar_gpio():
    """Configura e inicia os pinos GPIO."""
    if not GPIO_AVAILABLE:
        log.warning("GPIO não disponível — iniciando modo simulação")
        threading.Thread(target=simular_deteccoes, daemon=True).start()
        return

    GPIO.setmode(GPIO.BCM)
    GPIO.setup(SENSOR_A_PIN, GPIO.IN, pull_up_down=GPIO.PUD_UP)
    GPIO.setup(SENSOR_B_PIN, GPIO.IN, pull_up_down=GPIO.PUD_UP)

    GPIO.add_event_detect(SENSOR_A_PIN, GPIO.FALLING, callback=callback_sensor_a, bouncetime=50)
    GPIO.add_event_detect(SENSOR_B_PIN, GPIO.FALLING, callback=callback_sensor_b, bouncetime=50)

    log.info(f"GPIO iniciado — Sensor A: GPIO{SENSOR_A_PIN} | Sensor B: GPIO{SENSOR_B_PIN}")


def simular_deteccoes():
    """Simula detecções para testes sem hardware."""
    import random
    log.info("Modo simulação ativo — gerando detecções aleatórias")
    time.sleep(5)
    while True:
        velocidade = random.uniform(5, 45)
        uid = salvar_deteccao(velocidade, "A->B")
        evento = {
            "uuid": uid,
            "radar_id": RADAR_ID,
            "radar_nome": RADAR_NAME,
            "velocidade": round(velocidade, 2),
            "limite": SPEED_LIMIT,
            "acima_limite": velocidade > SPEED_LIMIT,
            "timestamp": datetime.now().isoformat(),
            "direcao": "A->B"
        }
        adicionar_evento_realtime(evento)
        time.sleep(random.uniform(3, 15))


# ─────────────────────────────────────────────
#  API REST (Flask)
# ─────────────────────────────────────────────
app = Flask(__name__)


def requer_token(f):
    """Decorator para autenticação via Bearer token."""
    @wraps(f)
    def decorado(*args, **kwargs):
        auth = request.headers.get("Authorization", "")
        if not auth.startswith("Bearer ") or auth[7:] != API_TOKEN:
            abort(401)
        return f(*args, **kwargs)
    return decorado


@app.route("/api/status", methods=["GET"])
@requer_token
def api_status():
    """Retorna status atual do radar."""
    conn = sqlite3.connect(SQLITE_PATH)
    c = conn.cursor()
    c.execute("SELECT COUNT(*) FROM deteccoes")
    total = c.fetchone()[0]
    c.execute("SELECT COUNT(*) FROM deteccoes WHERE sincronizado = 0")
    pendentes = c.fetchone()[0]
    c.execute("SELECT velocidade, timestamp FROM deteccoes ORDER BY id DESC LIMIT 1")
    ultima = c.fetchone()
    conn.close()

    pg_ok = get_pg_connection() is not None

    return jsonify({
        "radar_id": RADAR_ID,
        "radar_nome": RADAR_NAME,
        "localizacao": RADAR_LOCATION,
        "limite_velocidade": SPEED_LIMIT,
        "total_deteccoes": total,
        "pendentes_sync": pendentes,
        "postgresql_conectado": pg_ok,
        "gpio_ativo": GPIO_AVAILABLE,
        "ultima_deteccao": {
            "velocidade": ultima[0] if ultima else None,
            "timestamp": ultima[1] if ultima else None
        } if ultima else None,
        "timestamp": datetime.now().isoformat()
    })


@app.route("/api/deteccoes", methods=["GET"])
@requer_token
def api_deteccoes():
    """Retorna histórico de detecções."""
    limite = int(request.args.get("limite", 50))
    offset = int(request.args.get("offset", 0))

    conn = sqlite3.connect(SQLITE_PATH)
    conn.row_factory = sqlite3.Row
    c = conn.cursor()
    c.execute("""
        SELECT * FROM deteccoes
        ORDER BY id DESC
        LIMIT ? OFFSET ?
    """, (limite, offset))
    rows = [dict(r) for r in c.fetchall()]
    conn.close()
    return jsonify(rows)


@app.route("/api/eventos", methods=["GET"])
@requer_token
def api_eventos():
    """Retorna eventos recentes para o dashboard em tempo real."""
    with eventos_lock:
        dados = list(reversed(eventos_realtime[-20:]))
    return jsonify(dados)


@app.route("/api/configurar", methods=["POST"])
@requer_token
def api_configurar():
    """
    Recebe configuração do Manager Windows e salva em /etc/radar/.env
    Payload JSON esperado:
    {
        "pg_host": "...", "pg_port": 5432, "pg_db": "...",
        "pg_user": "...", "pg_pass": "...",
        "radar_name": "...", "radar_location": "...",
        "speed_limit": 20.0, "sensor_dist_m": 1.0,
        "sensor_a_pin": 17, "sensor_b_pin": 27,
        "api_token": "...", "sync_interval": 30
    }
    """
    dados = request.get_json()
    if not dados:
        return jsonify({"erro": "Payload inválido"}), 400

    env_path = Path("/etc/radar/.env")
    env_path.parent.mkdir(parents=True, exist_ok=True)

    linhas = [
        f"RADAR_ID={RADAR_ID}",
        f"RADAR_NAME={dados.get('radar_name', RADAR_NAME)}",
        f"RADAR_LOCATION={dados.get('radar_location', RADAR_LOCATION)}",
        f"API_TOKEN={dados.get('api_token', API_TOKEN)}",
        f"SENSOR_A_PIN={dados.get('sensor_a_pin', SENSOR_A_PIN)}",
        f"SENSOR_B_PIN={dados.get('sensor_b_pin', SENSOR_B_PIN)}",
        f"SENSOR_DIST_M={dados.get('sensor_dist_m', SENSOR_DIST_M)}",
        f"SPEED_LIMIT={dados.get('speed_limit', SPEED_LIMIT)}",
        f"PG_HOST={dados.get('pg_host', PG_HOST)}",
        f"PG_PORT={dados.get('pg_port', PG_PORT)}",
        f"PG_DB={dados.get('pg_db', PG_DB)}",
        f"PG_USER={dados.get('pg_user', PG_USER)}",
        f"PG_PASS={dados.get('pg_pass', PG_PASS)}",
        f"SQLITE_PATH={SQLITE_PATH}",
        f"SYNC_INTERVAL={dados.get('sync_interval', SYNC_INTERVAL)}",
        f"API_PORT={API_PORT}",
    ]

    env_path.write_text("\n".join(linhas) + "\n")
    log.info(f"Configuração atualizada via Manager por {request.remote_addr}")

    return jsonify({
        "status": "ok",
        "mensagem": "Configuração salva. Reinicie o serviço para aplicar.",
        "radar_id": RADAR_ID
    })


@app.route("/api/reiniciar", methods=["POST"])
@requer_token
def api_reiniciar():
    """Reinicia o serviço do radar."""
    def _reiniciar():
        time.sleep(1)
        os.system("sudo systemctl restart radar_service")
    threading.Thread(target=_reiniciar, daemon=True).start()
    return jsonify({"status": "ok", "mensagem": "Reiniciando em 1 segundo..."})


@app.route("/api/ping", methods=["GET"])
def api_ping():
    """Endpoint público para descoberta na rede."""
    return jsonify({
        "servico": "radar",
        "radar_id": RADAR_ID,
        "radar_nome": RADAR_NAME,
        "versao": "1.0.0"
    })


# ─────────────────────────────────────────────
#  MAIN
# ─────────────────────────────────────────────
if __name__ == "__main__":
    log.info("=" * 50)
    log.info(f"  Iniciando Radar Service — ID: {RADAR_ID}")
    log.info(f"  Nome: {RADAR_NAME} | Local: {RADAR_LOCATION}")
    log.info("=" * 50)

    # Inicializa banco local
    init_sqlite()

    # Inicia GPIO / simulação
    iniciar_gpio()

    # Inicia thread de sincronização
    threading.Thread(target=loop_sincronizacao, daemon=True).start()
    log.info(f"Thread de sincronização iniciada (intervalo: {SYNC_INTERVAL}s)")

    # Inicia API Flask
    log.info(f"API REST disponível em http://0.0.0.0:{API_PORT}")
    app.run(host="0.0.0.0", port=API_PORT, debug=False, threaded=True)
