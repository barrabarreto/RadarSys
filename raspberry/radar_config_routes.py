#!/usr/bin/env python3
"""
=============================================================
  RADAR_CONFIG_ROUTES.PY
  Adicione este conteúdo ao radar_service.py existente,
  logo antes do bloco  if __name__ == "__main__":
=============================================================
"""

# ─────────────────────────────────────────────
#  IMPORTS ADICIONAIS (adicione ao topo do arquivo)
# ─────────────────────────────────────────────
# import subprocess   ← já deve existir ou adicione
# from pathlib import Path  ← já deve existir

# ─────────────────────────────────────────────
#  PASTA DE TEMPLATES (ajuste o app Flask existente)
#
#  Substitua a linha:
#    app = Flask(__name__)
#  Por:
#    app = Flask(__name__, template_folder="templates")
#
#  E copie o arquivo config_page.html para:
#    /opt/radar_service/templates/config_page.html
# ─────────────────────────────────────────────


# ══════════════════════════════════════════════
#  PÁGINA WEB DE CONFIGURAÇÃO
# ══════════════════════════════════════════════

@app.route("/", methods=["GET"])
@app.route("/config", methods=["GET"])
def pagina_config():
    """Serve a página web de configuração."""
    from flask import render_template
    return render_template("config_page.html")


# ══════════════════════════════════════════════
#  ENDPOINTS PARA A PÁGINA WEB
# ══════════════════════════════════════════════

@app.route("/api/config-local", methods=["GET"])
@requer_token
def api_config_local():
    """Retorna as configurações atuais do .env para a página web."""
    env_path = Path("/etc/radar/.env")
    config = {}
    if env_path.exists():
        for linha in env_path.read_text().splitlines():
            linha = linha.strip()
            if "=" in linha and not linha.startswith("#"):
                chave, _, valor = linha.partition("=")
                # Nunca retorna a senha do PostgreSQL
                if chave.strip() not in ("PG_PASS",):
                    config[chave.strip()] = valor.strip()
    return jsonify(config)


@app.route("/api/network-info", methods=["GET"])
def api_network_info():
    """Retorna IPs das interfaces Wi-Fi e Ethernet."""
    import subprocess
    info = {"wifi": "—", "eth": "—"}
    try:
        # wlan0 = Wi-Fi
        r = subprocess.run(
            ["ip", "-4", "addr", "show", "wlan0"],
            capture_output=True, text=True
        )
        for line in r.stdout.splitlines():
            if "inet " in line:
                info["wifi"] = line.strip().split()[1].split("/")[0]

        # eth0 = Ethernet
        r = subprocess.run(
            ["ip", "-4", "addr", "show", "eth0"],
            capture_output=True, text=True
        )
        for line in r.stdout.splitlines():
            if "inet " in line:
                info["eth"] = line.strip().split()[1].split("/")[0]
    except Exception as e:
        log.debug(f"network-info erro: {e}")

    return jsonify(info)


@app.route("/api/wifi/scan", methods=["GET"])
@requer_token
def api_wifi_scan():
    """Escaneia redes Wi-Fi disponíveis."""
    import subprocess, re
    redes = []
    try:
        # Força um scan
        subprocess.run(["sudo", "iwlist", "wlan0", "scan"], capture_output=True)
        r = subprocess.run(
            ["sudo", "iwlist", "wlan0", "scan"],
            capture_output=True, text=True, timeout=10
        )
        ssids = re.findall(r'ESSID:"(.+?)"', r.stdout)
        qualidades = re.findall(r'Quality=(\d+)/(\d+)', r.stdout)

        for i, ssid in enumerate(ssids):
            if ssid and ssid not in [n["ssid"] for n in redes]:
                qual = qualidades[i] if i < len(qualidades) else (0, 70)
                sinal = round((int(qual[0]) / int(qual[1])) * 4)
                redes.append({"ssid": ssid, "signal": max(1, min(4, sinal))})
    except Exception as e:
        log.warning(f"Scan Wi-Fi falhou: {e}")

    return jsonify(redes)


@app.route("/api/wifi/configurar", methods=["POST"])
@requer_token
def api_wifi_configurar():
    """
    Configura uma nova rede Wi-Fi no wpa_supplicant.conf.
    Payload: { "ssid": "...", "password": "...", "autoconnect": true }
    """
    dados = request.get_json()
    ssid = dados.get("wifi_ssid", "")
    senha = dados.get("wifi_pass", "")

    if not ssid:
        return jsonify({"status": "ok", "mensagem": "SSID não fornecido, Wi-Fi não alterado"})

    wpa_path = Path("/etc/wpa_supplicant/wpa_supplicant.conf")

    # Lê o arquivo atual
    try:
        conteudo_atual = wpa_path.read_text() if wpa_path.exists() else ""
    except Exception:
        conteudo_atual = ""

    # Remove bloco da mesma rede se já existir
    import re
    conteudo_atual = re.sub(
        rf'network\s*\{{[^}}]*ssid\s*=\s*"{re.escape(ssid)}"[^}}]*\}}',
        '', conteudo_atual, flags=re.DOTALL
    ).strip()

    # Adiciona nova configuração
    if senha:
        novo_bloco = f"""
network={{
    ssid="{ssid}"
    psk="{senha}"
    key_mgmt=WPA-PSK
    priority=10
}}
"""
    else:
        novo_bloco = f"""
network={{
    ssid="{ssid}"
    key_mgmt=NONE
    priority=10
}}
"""

    # Garante cabeçalho
    if "country=BR" not in conteudo_atual:
        cabecalho = "country=BR\nctrl_interface=DIR=/var/run/wpa_supplicant GROUP=netpie\nupdate_config=1\n"
        conteudo_atual = cabecalho + conteudo_atual

    novo_conteudo = conteudo_atual + "\n" + novo_bloco

    try:
        wpa_path.write_text(novo_conteudo)
        # Aplica sem reiniciar
        import subprocess
        subprocess.run(["sudo", "wpa_cli", "-i", "wlan0", "reconfigure"], capture_output=True)
        log.info(f"Wi-Fi configurado para SSID: {ssid}")
        return jsonify({"status": "ok", "mensagem": f"Wi-Fi '{ssid}' configurado"})
    except Exception as e:
        log.error(f"Erro ao salvar wpa_supplicant: {e}")
        return jsonify({"status": "erro", "erro": str(e)}), 500


@app.route("/api/pg/test", methods=["POST"])
@requer_token
def api_pg_test():
    """Testa uma conexão PostgreSQL com os dados fornecidos."""
    dados = request.get_json()
    if not PG_AVAILABLE:
        return jsonify({"ok": False, "erro": "psycopg2 não instalado"})
    try:
        conn = psycopg2.connect(
            host=dados.get("pg_host"),
            port=dados.get("pg_port", 5432),
            dbname=dados.get("pg_db"),
            user=dados.get("pg_user"),
            password=dados.get("pg_pass", ""),
            connect_timeout=5
        )
        conn.close()
        return jsonify({"ok": True})
    except Exception as e:
        return jsonify({"ok": False, "erro": str(e)})


@app.route("/api/reset-factory", methods=["POST"])
@requer_token
def api_reset_factory():
    """Apaga as configurações e restaura os padrões."""
    import uuid as _uuid
    env_path = Path("/etc/radar/.env")
    novo_id = str(_uuid.uuid4())[:8]
    padrao = f"""RADAR_ID={novo_id}
RADAR_NAME={socket.gethostname()}
RADAR_LOCATION=Não configurado
API_TOKEN=mude-este-token
SENSOR_A_PIN=17
SENSOR_B_PIN=27
SENSOR_DIST_M=1.0
SPEED_LIMIT=20.0
PG_HOST=
PG_PORT=5432
PG_DB=
PG_USER=
PG_PASS=
SQLITE_PATH=/var/lib/radar/radar_local.db
SYNC_INTERVAL=30
API_PORT=5000
BLE_NAME=Radar-{novo_id}
BLE_ENABLED=0
"""
    env_path.parent.mkdir(parents=True, exist_ok=True)
    env_path.write_text(padrao)
    log.warning(f"Reset de fábrica realizado por {request.remote_addr}")

    def _reiniciar():
        import time as _time
        _time.sleep(1)
        import os as _os
        _os.system("sudo systemctl restart radar_service")

    import threading as _threading
    _threading.Thread(target=_reiniciar, daemon=True).start()

    return jsonify({"status": "ok", "mensagem": "Reset realizado. Reiniciando..."})


# ══════════════════════════════════════════════
#  SUBSTITUIÇÃO DO /api/configurar EXISTENTE
#  (substitui o endpoint anterior para também
#   salvar Wi-Fi e BLE, e aplicar wpa_supplicant)
# ══════════════════════════════════════════════

# Remova o @app.route("/api/configurar") existente e use este:
@app.route("/api/configurar-v2", methods=["POST"])
@requer_token
def api_configurar_v2():
    """
    Versão expandida do /api/configurar.
    Aceita os mesmos campos de antes + wifi_ssid, wifi_pass, ble_name, ble_enabled.
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
        f"BLE_NAME={dados.get('ble_name', socket.gethostname())}",
        f"BLE_ENABLED={dados.get('ble_enabled', '0')}",
    ]

    env_path.write_text("\n".join(linhas) + "\n")
    log.info(f"Configuração salva via página web por {request.remote_addr}")

    # Aplica Wi-Fi se fornecido
    if dados.get("wifi_ssid"):
        try:
            api_wifi_configurar.__wrapped__ = api_wifi_configurar
            # Chama a lógica de Wi-Fi diretamente
            from flask import g
            _configurar_wifi(dados.get("wifi_ssid"), dados.get("wifi_pass", ""))
        except Exception as e:
            log.warning(f"Wi-Fi config falhou: {e}")

    # Reinicia serviço para aplicar
    def _reiniciar():
        import time as _t; _t.sleep(1)
        import os as _o; _o.system("sudo systemctl restart radar_service")
    import threading as _th
    _th.Thread(target=_reiniciar, daemon=True).start()

    return jsonify({
        "status": "ok",
        "mensagem": "Configurações salvas. Reiniciando serviço...",
        "radar_id": RADAR_ID
    })


def _configurar_wifi(ssid: str, senha: str):
    """Função auxiliar para configurar Wi-Fi."""
    import re
    wpa_path = Path("/etc/wpa_supplicant/wpa_supplicant.conf")
    conteudo = wpa_path.read_text() if wpa_path.exists() else "country=BR\nctrl_interface=DIR=/var/run/wpa_supplicant GROUP=netpie\nupdate_config=1\n"

    conteudo = re.sub(
        rf'network\s*\{{[^}}]*ssid\s*=\s*"{re.escape(ssid)}"[^}}]*\}}',
        '', conteudo, flags=re.DOTALL
    ).strip()

    if senha:
        bloco = f'\nnetwork={{\n    ssid="{ssid}"\n    psk="{senha}"\n    key_mgmt=WPA-PSK\n    priority=10\n}}\n'
    else:
        bloco = f'\nnetwork={{\n    ssid="{ssid}"\n    key_mgmt=NONE\n    priority=10\n}}\n'

    wpa_path.write_text(conteudo + bloco)
    import subprocess
    subprocess.run(["sudo", "wpa_cli", "-i", "wlan0", "reconfigure"], capture_output=True)


# ══════════════════════════════════════════════
#  BLUETOOTH BLE — STUB PARA FUTURO APP MOBILE
# ══════════════════════════════════════════════
"""
COMO IMPLEMENTAR O APP BLUETOOTH NO FUTURO:

1. Instale no Pi:
   pip3 install bleak bluepy --break-system-packages

2. Use a biblioteca 'bluezero' para expor um servidor GATT BLE:
   pip3 install bluezero --break-system-packages

3. Crie um serviço GATT com características para cada configuração:
   - Characteristic UUID para RADAR_NAME   (read/write)
   - Characteristic UUID para PG_HOST      (read/write)
   - Characteristic UUID para SPEED_LIMIT  (read/write)
   - Characteristic UUID para STATUS       (read/notify)

4. No app mobile (Flutter/React Native):
   - Use flutter_blue_plus (Flutter) ou react-native-ble-plx
   - Scan por dispositivos com nome "Radar-XXXXXX"
   - Conecta e lê/escreve as características GATT

5. Código base para o servidor BLE no Pi (bluezero):

   from bluezero import peripheral, constants
   
   radar_service_uuid  = '12345678-1234-5678-1234-56789abcdef0'
   name_char_uuid      = '12345678-1234-5678-1234-56789abcdef1'
   pg_host_char_uuid   = '12345678-1234-5678-1234-56789abcdef2'
   
   periph = peripheral.Peripheral(adapter_address, local_name='Radar-XXXX')
   periph.add_service(srv_id=1, uuid=radar_service_uuid, primary=True)
   periph.add_characteristic(
       srv_id=1, chr_id=1, uuid=name_char_uuid,
       value=[], notifying=False,
       flags=['read', 'write'],
       read_callback=lambda: list(RADAR_NAME.encode()),
       write_callback=lambda val, opts: salvar_config_ble('RADAR_NAME', bytes(val).decode())
   )
   periph.publish()
"""
