#!/bin/bash
# =============================================================
#  SETUP.SH — Instalação automática do Radar Service
#  Execute com: sudo bash setup.sh
# =============================================================

set -e

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
CYAN='\033[0;36m'
NC='\033[0m'

echo -e "${CYAN}"
echo "╔══════════════════════════════════════════╗"
echo "║     RADAR SERVICE — Instalação v1.0      ║"
echo "╚══════════════════════════════════════════╝"
echo -e "${NC}"

# Verifica root
if [ "$EUID" -ne 0 ]; then
    echo -e "${RED}Execute como root: sudo bash setup.sh${NC}"
    exit 1
fi

echo -e "${YELLOW}[1/7] Atualizando pacotes...${NC}"
apt-get update -qq

echo -e "${YELLOW}[2/7] Instalando dependências do sistema...${NC}"
apt-get install -y python3 python3-pip python3-venv git nmap -qq

echo -e "${YELLOW}[3/7] Criando estrutura de diretórios...${NC}"
mkdir -p /opt/radar_service
mkdir -p /var/lib/radar
mkdir -p /var/log
mkdir -p /etc/radar

echo -e "${YELLOW}[4/7] Copiando arquivos...${NC}"
cp radar_service.py /opt/radar_service/
chown -R pi:pi /opt/radar_service /var/lib/radar /etc/radar

echo -e "${YELLOW}[5/7] Instalando dependências Python...${NC}"
pip3 install flask psycopg2-binary RPi.GPIO python-dotenv --break-system-packages --quiet

# Cria .env padrão se não existir
if [ ! -f /etc/radar/.env ]; then
    echo -e "${YELLOW}Criando configuração padrão...${NC}"
    RADAR_UUID=$(python3 -c "import uuid; print(str(uuid.uuid4())[:8])")
    HOSTNAME=$(hostname)
    cat > /etc/radar/.env << EOF
RADAR_ID=${RADAR_UUID}
RADAR_NAME=${HOSTNAME}
RADAR_LOCATION=Não configurado
API_TOKEN=mude-este-token-$(openssl rand -hex 8)
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
EOF
    echo -e "${GREEN}Arquivo .env criado em /etc/radar/.env${NC}"
fi

echo -e "${YELLOW}[6/7] Criando serviço systemd...${NC}"
cat > /etc/systemd/system/radar_service.service << EOF
[Unit]
Description=Radar Service - Sistema de Controle de Velocidade
After=network.target
Wants=network-online.target

[Service]
Type=simple
User=root
WorkingDirectory=/opt/radar_service
ExecStart=/usr/bin/python3 /opt/radar_service/radar_service.py
Restart=always
RestartSec=5
StandardOutput=journal
StandardError=journal
EnvironmentFile=/etc/radar/.env

[Install]
WantedBy=multi-user.target
EOF

echo -e "${YELLOW}[7/7] Ativando e iniciando serviço...${NC}"
systemctl daemon-reload
systemctl enable radar_service
systemctl start radar_service

# Aguarda inicialização
sleep 3

echo ""
echo -e "${GREEN}╔══════════════════════════════════════════╗${NC}"
echo -e "${GREEN}║        INSTALAÇÃO CONCLUÍDA!              ║${NC}"
echo -e "${GREEN}╚══════════════════════════════════════════╝${NC}"
echo ""
echo -e "Status do serviço:"
systemctl status radar_service --no-pager -l || true
echo ""
echo -e "${CYAN}API disponível em: http://$(hostname -I | awk '{print $1}'):5000${NC}"
echo -e "${CYAN}Token de acesso em: cat /etc/radar/.env${NC}"
echo ""
echo -e "${YELLOW}Configure este radar via Manager Windows ou edite:${NC}"
echo -e "  sudo nano /etc/radar/.env"
echo -e "  sudo systemctl restart radar_service"
