
# csv_importer_netbox — Importador e Exportador CSV para NetBox

Ferramenta prática para **importar e exportar objetos do NetBox** a partir de arquivos CSV, utilizando a biblioteca `pynetbox`.
Projetada para execução em **ambientes Docker** (isolados e reproduzíveis) ou em **Python local**.

---

## 📦 Visão geral

O projeto contém dois utilitários principais:

| Script                 | Função                                                    |
| ---------------------- | --------------------------------------------------------- |
| `netbox/csv_add.py`    | Importa arquivos CSV → cria ou atualiza objetos no NetBox |
| `netbox/csv_export.py` | Exporta dados do NetBox → gera CSVs padronizados          |

Ambos suportam `.env` com `NETBOX_URL` e `NETBOX_TOKEN`, auto-instalação de dependências e execução independente.

---

## ⚙️ Funcionalidades principais

### Importador (`csv_add.py`)

* Normaliza cabeçalhos (`minusculo_com_underscore`).
* Resolve automaticamente referências por nome ou ID (`site`, `tenant`, `vrf`).
* Converte tipos (ex.: `mtu`, `speed` → inteiros).
* Política de *upsert*:

  * **Devices:** comparação por `name + site`.
  * **IP Addresses:** comparação por `address` e `vrf` (`--ip-upsert`).
* Instala `pynetbox` e `python-dotenv` em runtime, se ausentes.

### Exportador (`csv_export.py`)

* Cria pasta automática: `netbox_export-<host>-YYYYMMDD_HHMMSS`.
* Exporta todos os objetos principais: *sites, devices, interfaces, IPs, circuits, cables, etc.*
* Extrai corretamente conexões entre interfaces e circuitos (`8_cables.csv`), no formato:

```csv
side_a_device,side_a_type,side_a_name,side_b_device,side_b_type,side_b_name,label,description
RNP-CE,dcim.interface,1/1,RG02-EMBRAPA,dcim.interface,1/1,RNP-CE↔RG02-EMBRAPA,Tronco 10G
```

---

## 🧱 Estrutura do projeto

```
csv_importer_netbox/
├─ requirements.txt
└─ netbox/
   ├─ csv_add.py       # Importador CSV → NetBox
   ├─ csv_export.py    # Exportador NetBox → CSV
   └─ arquivos_csv/    # Modelos CSV de entrada
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
      ├─ 7_circuits.csv
      └─ 8_cables.csv
```

> Os prefixos numéricos ordenam a importação e previnem erros de dependência (ex.: `sites` antes de `devices`).

---

## 🔗 Convenções de nome e endpoints

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
| `8_cables.csv`         | `dcim.cables`            |

---

## 🌍 Variáveis de ambiente

Usadas por ambos os scripts:

```bash
cd csv_importer_netbox
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

export NETBOX_URL="http://SEU_IP_NETBOX:8000"
export NETBOX_TOKEN="SEU_TOKEN"

Também podem estar em `.env` (na raiz ou dentro de `netbox/`).

---


### 1. Importar CSVs → NetBox

```bash
python netbox/csv_add.py netbox/arquivos_csv --ip-upsert
```

### 2. Exportar dados → CSVs

```bash
python netbox/csv_export.py netbox/export
```

Saída típica:

```
[destino] netbox_export-192-168-0-221-20251024_051812
[ok] 8_cables.csv: 27 linhas
```

---

## 🧩 Exemplo de fluxo completo

```bash
# Importar dados
python netbox/csv_add.py netbox/arquivos_csv --ip-upsert

# Exportar snapshot do NetBox
python netbox/csv_export.py netbox/export
```

O diretório `netbox/export/netbox_export-<host>-<timestamp>/` conterá todos os CSVs.

---

## ⚡ requirements.txt

```txt
pynetbox==7.5.0
python-dotenv>=1.0.0
```

---

## 💡 Boas práticas

* Faça backup do banco do NetBox antes de grandes cargas.
* Valide a exportação (`csv_export.py`) antes de importar novamente.
* Use sempre token com permissões restritas.
* Evite salvar tokens em repositórios Git.
* Confirme a consistência de `interfaces` e `cables` antes de usar com o NetReplica.

---
