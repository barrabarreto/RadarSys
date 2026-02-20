# 🚦 Sistema de Radares para Condomínios

Sistema completo de controle de velocidade para condomínios usando **Raspberry Pi Zero W** com sensores IR, banco local **SQLite**, banco central **PostgreSQL** e painel web de gestão rodando no **Windows**.

---

## 📁 Estrutura do Projeto

```
radar-system/
├── raspberry/
│   ├── radar_service.py       ← Serviço que roda no Pi (sensor + API + SQLite)
│   ├── setup.sh               ← Instalação automática no Pi
│   └── requirements_pi.txt
│
└── server/
    ├── manager_server.py      ← Servidor web Windows (dashboard + gestão)
    ├── requirements_manager.txt
    └── templates/
        └── dashboard.html     ← Interface web
```

---

## 🥧 PARTE 1 — Raspberry Pi Zero W

### Hardware necessário
- Raspberry Pi Zero W (ou 2W)
- 2x Sensores IR (ex: E18-D80NK ou FC-51)
  - Sensor A → GPIO 17 (entrada da rua)
  - Sensor B → GPIO 27 (saída da rua)
- Fonte 5V micro-USB

### Ligações GPIO

```
Pi Zero W          Sensor IR
─────────────────────────────
GPIO 17  ←────── Sensor A (SINAL)
GPIO 27  ←────── Sensor B (SINAL)
3.3V     ──────► VCC (ambos)
GND      ──────► GND (ambos)
```

> **Cálculo de velocidade:** O Pi mede o tempo entre Sensor A e Sensor B detectarem.
> `Velocidade = Distância / Tempo × 3.6` (resultado em km/h)
> Configure a distância real entre os sensores em `SENSOR_DIST_M`.

### Instalação

1. **Flash do SD:** Use Raspberry Pi OS Lite (32-bit)
2. **Configure Wi-Fi** no `wpa_supplicant.conf` antes de bootar
3. **Acesse via SSH** e execute:

```bash
git clone https://github.com/barrabarreto/RadarSys.git radar
cd radar/raspberry
sudo bash setup.sh
```

O setup irá:
- Instalar Python, Flask, psycopg2, RPi.GPIO
- Criar o serviço systemd (inicia automaticamente no boot)
- Gerar arquivo `/etc/radar/.env` com configuração padrão

### Configuração manual (opcional)

```bash
sudo nano /etc/radar/.env
sudo systemctl restart radar_service
```

Variáveis importantes do `.env`:
```env
RADAR_NAME=Entrada Principal
RADAR_LOCATION=Portão 1 - Rua das Flores
SENSOR_A_PIN=17
SENSOR_B_PIN=27
SENSOR_DIST_M=1.0        # distância real entre sensores em metros
SPEED_LIMIT=20.0         # km/h
PG_HOST=192.168.1.100
PG_DB=radares
PG_USER=postgres
PG_PASS=suasenha
API_TOKEN=token-secreto  # mesmo token no Manager
```

### Testar manualmente

```bash
# Ver logs em tempo real
journalctl -u radar_service -f

# Testar API
curl http://IP_DO_PI:5000/api/ping
curl -H "Authorization: Bearer seu-token" http://IP_DO_PI:5000/api/status
```

---

## 💻 PARTE 2 — Manager Windows

### Pré-requisitos
- Python 3.9+ no Windows
- PostgreSQL acessível na rede

### Instalação

```cmd
cd server
pip install -r requirements_manager.txt
python manager_server.py
```

O navegador abrirá automaticamente em `http://localhost:8080`

### Funcionalidades do Dashboard

| Aba | O que faz |
|-----|-----------|
| **Dashboard** | Métricas do dia, feed de velocidades em tempo real, status dos radares |
| **Radares** | Escanear rede, ver todos os radares, configurar individualmente ou em massa |
| **Eventos** | Tabela completa de todas as detecções |
| **Configurações** | Banco PostgreSQL, parâmetros globais dos radares |

### Fluxo de uso por condomínio

1. Instale os Raspberry Pi nos locais desejados
2. Abra o Manager → **Configurações** → configure o PostgreSQL do condomínio
3. Aba **Radares** → **Escanear Rede** → aguarde descobrir os Pi's
4. Clique **⚡ Config em Massa** → envia PG + token + parâmetros para todos
5. Clique ⚙ em cada radar para dar nome e localização individuais
6. Pronto! O dashboard mostra velocidades em tempo real

---

## 🗄️ PostgreSQL Central

A tabela criada automaticamente:

```sql
CREATE TABLE radar_deteccoes (
    id           SERIAL PRIMARY KEY,
    uuid         TEXT NOT NULL UNIQUE,
    radar_id     TEXT NOT NULL,
    radar_nome   TEXT,
    timestamp    TIMESTAMPTZ NOT NULL,
    velocidade   NUMERIC(6,2) NOT NULL,
    direcao      TEXT,
    acima_limite BOOLEAN DEFAULT FALSE,
    criado_em    TIMESTAMPTZ DEFAULT NOW()
);
```

Os Radares sincronizam automaticamente (padrão: a cada 30s).
Funciona **offline** — armazena no SQLite local e envia quando reconectar.

---

## 🔒 Segurança

- Toda comunicação entre Manager ↔ Radar usa **Bearer Token**
- O token é gerado aleatoriamente no `setup.sh`
- Configure o mesmo token no Manager antes de escanear
- Para produção, considere HTTPS com Nginx + certificado autoassinado

---

## 📡 API do Radar (referência)

| Método | Endpoint | Descrição |
|--------|----------|-----------|
| GET | `/api/ping` | Público — descoberta na rede |
| GET | `/api/status` | Status completo do radar |
| GET | `/api/deteccoes` | Histórico local |
| GET | `/api/eventos` | Últimas 20 detecções (polling) |
| POST | `/api/configurar` | Recebe nova config do Manager |
| POST | `/api/reiniciar` | Reinicia o serviço |

Todos os endpoints (exceto `/ping`) requerem header:
```
Authorization: Bearer <api_token>
```
