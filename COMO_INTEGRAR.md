# Como integrar a Página Web de Configuração

## Arquivos gerados

```
radar_config_routes.py   ← Novas rotas Flask para adicionar ao radar_service.py
templates/
  config_page.html       ← Página web de configuração (copiar para o Pi)
```

## Passos para integrar no Pi

### 1. Copie os arquivos para o Pi

```bash
scp config_page.html pi@IP_DO_PI:/opt/radar_service/templates/
scp radar_config_routes.py pi@IP_DO_PI:/opt/radar_service/
```

### 2. Ajuste o radar_service.py

Faça 2 alterações no arquivo existente:

**a) Troque a criação do Flask para habilitar templates:**
```python
# ANTES:
app = Flask(__name__)

# DEPOIS:
app = Flask(__name__, template_folder="templates")
```

**b) No final do arquivo, antes do `if __name__ == "__main__":`, cole o conteúdo do `radar_config_routes.py`**
(ou use um import — veja abaixo)

### 3. Alternativa: usar import direto

Adicione esta linha ao `radar_service.py` logo após criar o `app`:

```python
app = Flask(__name__, template_folder="templates")

# Importa as rotas da página de configuração
from radar_config_routes import *   # ← adicione esta linha
```

### 4. Reinicie o serviço

```bash
sudo systemctl restart radar_service
```

### 5. Acesse pelo navegador

Conecte no mesmo Wi-Fi do Pi e abra:
```
http://IP_DO_PI:5000/
  ou
http://IP_DO_PI:5000/config
```

---

## O que a página faz

| Seção          | Funcionalidade |
|----------------|----------------|
| Dispositivo    | Mostra ID, hostname, IPs Wi-Fi e Ethernet, status PostgreSQL, pendentes de sync |
| Identificação  | Nome e localização do radar |
| Sensores       | GPIOs, distância entre sensores, limite de velocidade |
| Banco Central  | Host/porta/banco/usuário/senha do PostgreSQL + botão testar |
| Wi-Fi          | Escaneia redes, seleciona e configura sem editar arquivos |
| Segurança      | Token API + gerador de token aleatório |
| Bluetooth BLE  | Stub preparado para o app mobile futuro |

---

## App Bluetooth — Roadmap

Quando quiser desenvolver o app mobile, o arquivo `radar_config_routes.py` 
tem no final um comentário detalhado com:

1. Dependências Python para BLE no Pi (`bluezero`)
2. Código base do servidor GATT BLE
3. Bibliotecas recomendadas para Flutter e React Native
4. Mapeamento de características UUID para cada config

A lógica de configuração já está pronta (salva no .env e reinicia o serviço) — 
o BLE só precisa de uma camada de transporte diferente sobre ela.
