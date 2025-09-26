# csv_importer_netbox — Importador CSV para NetBox (Docker-friendly)

Ferramenta objetiva para importar objetos no NetBox a partir de arquivos CSV, utilizando a biblioteca `pynetbox`. O projeto foi pensado para rodar facilmente em Docker (ambiente limpo e reprodutível) ou em Python local.

---

## Visão geral

O script `netbox/csv_add.py` lê uma pasta contendo CSVs, normaliza cabeçalhos e cria/atualiza objetos no NetBox.
Ele resolve automaticamente referências por **nome** ou **ID** (por exemplo: `site`, `tenant`, `vrf`) e oferece política de *upsert* para itens específicos.

**Recursos principais**

* Normalização de cabeçalhos: converte para `minusculo_com_underscore`.
* Resolução de referências: aceita IDs (`123`) ou nomes (`{"name": "PoP-CE"}`).
* Conversão de tipos: campos inteiros são convertidos quando necessário (ex.: `mtu`, `speed`).
* *Upsert*:

  * **Devices**: *match* por `name + site` → cria ou atualiza parcialmente.
  * **IP Addresses**: com `--ip-upsert`, *match* por `address` (e `vrf`, se houver) → cria ou atualiza.
  * Demais modelos: criação simples (se já existir, o NetBox retorna erro de unicidade e o script registra como “skipped”).
* Auto-instalação de dependências em runtime (pynetbox; `python-dotenv` de forma opcional, se houver `.env`).

---

## Pré-requisitos

* NetBox acessível (ex.: `http://192.168.0.88:8000`).
* Um **API Token** com permissão para criar/atualizar os objetos desejados.
* Docker instalado **ou** Python 3.12+ com `pip`.

---

## Estrutura do projeto

```
csv_importer_netbox/
├─ requirements.txt
└─ netbox/
   ├─ csv_add.py
   └─ arquivos_csv/
      ├─ 1_manufacturers.csv
      ├─ 2_platforms.csv
      ├─ 3_device_roles.csv
      ├─ 3_device_types.csv
      ├─ 3_netbox_tenants.csv
      ├─ 3_sites.csv
      ├─ 4_devices.csv
      ├─ 5_interfaces.csv
      ├─ 5_VRFs.csv
      ├─ 6_IP_addresses.csv
      ├─ 6_providers.csv
      ├─ 6_circuit_types.csv
      └─ 7_circuits.csv
```

> Os prefixos `1_..7_` são **opcionais**, porém úteis para ordenar a criação e evitar erros de dependência (por exemplo, criar `manufacturers` antes de `device_types`, `sites` antes de `devices`).

---

## Convenções de nomes dos arquivos

O script mapeia automaticamente o arquivo ao endpoint do NetBox com base no nome do arquivo (após remover um prefixo numérico e a extensão). Alguns exemplos:

| Arquivo CSV            | Endpoint do NetBox       |
| ---------------------- | ------------------------ |
| `1_manufacturers.csv`  | `dcim.manufacturers`     |
| `2_platforms.csv`      | `dcim.platforms`         |
| `3_device_roles.csv`   | `dcim.device_roles`      |
| `3_device_types.csv`   | `dcim.device_types`      |
| `3_netbox_tenants.csv` | `tenancy.tenants`        |
| `3_sites.csv`          | `dcim.sites`             |
| `4_devices.csv`        | `dcim.devices`           |
| `5_interfaces.csv`     | `dcim.interfaces`        |
| `5_VRFs.csv`           | `ipam.vrfs`              |
| `6_IP_addresses.csv`   | `ipam.ip_addresses`      |
| `6_providers.csv`      | `circuits.providers`     |
| `6_circuit_types.csv`  | `circuits.circuit_types` |
| `7_circuits.csv`       | `circuits.circuits`      |

> Arquivos contendo `cables` são ignorados por este importador.

---

## Variáveis de ambiente

O script lê as variáveis abaixo (ou, na ausência delas, usa valores padrão):

* `NETBOX_URL` — ex.: `http://192.168.0.88:8000`
* `NETBOX_TOKEN` — seu token de API

Você pode também usar um arquivo `.env` na raiz do projeto (ou em `netbox/.env`) com, por exemplo:

```
NETBOX_URL=http://192.168.0.88:8000
NETBOX_TOKEN=coloque_seu_token_aqui
```

---

## Como executar

### A) Docker (recomendado)

```bash
cd csv_importer_netbox

export NETBOX_URL="http://192.168.0.88:8000"
export NETBOX_TOKEN="SEU_TOKEN"

docker run --rm -it \
  -v "$PWD":/app -w /app \
  -e NETBOX_URL -e NETBOX_TOKEN \
  python:3.12-slim \
  bash -lc 'pip install -r requirements.txt && python netbox/csv_add.py netbox/arquivos_csv --ip-upsert'
```

Observações:

* O `--ip-upsert` torna a importação de IPs idempotente (atualiza registros existentes).
* Se preferir apenas criar IPs, remova `--ip-upsert`.

### B) Python local

```bash
cd csv_importer_netbox
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

export NETBOX_URL="http://192.168.0.88:8000"
export NETBOX_TOKEN="SEU_TOKEN"

python netbox/csv_add.py netbox/arquivos_csv --ip-upsert
```

> O script tenta instalar dependências automaticamente, mas recomenda-se usar o `requirements.txt` para previsibilidade.

---

## `requirements.txt`

```txt
pynetbox==7.5.0
# Opcional: carregamento de variáveis a partir de .env
python-dotenv>=1.0.0
```

---

## Formato dos CSVs (exemplos mínimos)

**Devices** — `4_devices.csv`

```csv
name,site,role,device_type,manufacturer,platform,serial
popce-rtr01,PoP-CE Fortaleza,Router,EX4300-48T,Juniper,Junos,SN123
```

**IP Addresses** — `6_IP_addresses.csv`

```csv
address,vrf,tenant,status,role,description
200.129.0.10/30,VRF-CORE,Cliente A,active,loopback,Uplink backbone
```

Diretrizes:

* `site`, `tenant`, `vrf` aceitam **nome** ou **ID**.
* `device_type` pode ser **modelo**. Se acompanhado de `manufacturer`, a resolução é mais assertiva.
* Para **devices**, o script decide `create` vs `update` usando `name + site`.
* Para **ip_addresses**, com `--ip-upsert`, o *match* é por `address` (e `vrf`, quando presente).

---

## Saída esperada

Durante a execução, o script imprime blocos por prioridade/pasta, o endpoint detectado e um resumo por arquivo, por exemplo:

```
=== PRIORIDADE (1..7) ===
[4] 4_devices.csv → dcim.devices
 - POST dcim.devices: 12 registros
   → ok=10 upd=2
```

* `ok` = criados
* `upd` = atualizados
* `skip` = ignorados (ex.: unicidade já satisfeita)
* `erros` = falhas de criação/atualização

---

## Solução de problemas

* **401 / 403** ao chamar API: verifique `NETBOX_TOKEN` e se o usuário tem permissões necessárias.
* **400 / “must be unique / already exists”**: o objeto já existe. Para devices e IPs, o script já contempla *upsert*; para os demais, a linha é marcada como “skipped”.
* **Erros de CSRF/hosts na interface web**: ajuste `ALLOWED_HOSTS` e `CSRF_TRUSTED_ORIGINS` no `netbox.env`. Lembre-se de incluir o esquema e a porta (ex.: `http://192.168.0.88:8000`).
* **Conectividade**: confirme que o host que executa o importador alcança `NETBOX_URL` (porta 8000, no exemplo).
* **Dependências**: se houver erro de pacote não encontrado, adicione ao `requirements.txt` e execute novamente.

---

## Boas práticas

* Evite fazer commit de tokens no repositório.
* Revogue os tokens utilizados após as cargas.
* Mantenha backups do banco do NetBox antes de cargas grandes.
* Valide um pequeno conjunto de registros antes de executar a carga completa.

---


