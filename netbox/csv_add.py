#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
NetBox CSV Importer — Mini (simples & didático)

Fluxo:
  1) Vasculha a pasta por CSVs (ordem 1..7 opcional).
  2) Deduz o endpoint do NetBox pelo nome do arquivo.
  3) Normaliza cabeçalhos (minusculo_com_underscore).
  4) Ajusta referências (site, tenant, vrf, role/device_type) e tipos (int).
  5) Cria ou atualiza registros (devices: name+site; ip addresses: address+vrf).

Uso:
  python3 nb_csv_importer_mini.py ./netbox/arquivos_csv --ip-upsert

CSV mínimos:

(1) 4_devices.csv
name,site,role,device_type,manufacturer,platform,serial
rtr-core-01,DM-NYC,Router,MX480,Juniper,Junos,SN123

(2) 6_IP_addresses.csv
address,vrf,tenant,status,role,description
10.0.0.1/24,VRF-CORE,Cliente A,active,loopback,IP de loopback

Observações:
- "site", "tenant", "vrf" podem ser NOME (string) ou ID.
- "device_type" pode ser NOME do modelo; se fabricante vier, melhor.
- Para criar um device o NetBox exige: site, role, device_type (ids ou objetos resolvidos).
"""

import argparse
import csv
import pathlib
import re
import sys
from typing import Any, Dict, List, Optional, Tuple

import pynetbox
from pynetbox.core.query import RequestError

# ====== CONFIGURE AQUI =========================================================
NETBOX_URL   = "http://192.168.0.222:8000"
NETBOX_TOKEN = "0bcd479997b36d42edea7509a4c86043ee52a3d7"
# ==============================================================================

# Nome do arquivo -> endpoint do NetBox
# (o script remove prefixos numéricos como "1_", "2_" e converte para minúsculas)
NAME2EP = {
    "manufacturers":    "dcim.manufacturers",      # 1_manufacturers.csv
    "platforms":        "dcim.platforms",          # 2_platforms.csv
    "device_roles":     "dcim.device_roles",       # 3_device_roles.csv
    "device_types":     "dcim.device_types",       # 3_device_types.csv
    "netbox_tenants":   "tenancy.tenants",         # 3_netbox_tenants.csv
    "sites":            "dcim.sites",              # 3_sites.csv
    "devices":          "dcim.devices",            # 4_devices.csv
    "interfaces":       "dcim.interfaces",         # 5_interfaces.csv
    "vrfs":             "ipam.vrfs",               # 5_VRFs.csv
    "ip_addresses":     "ipam.ip_addresses",       # 6_IP_addresses.csv
    "providers":        "circuits.providers",      # 6_providers.csv
    "circuit_types":    "circuits.circuit_types",  # 6_circuit_types.csv
    "circuits":         "circuits.circuits",       # 7_circuits.csv
    # 8_cables.csv é ignorado automaticamente
}

# Campos que são referências (aceitam id ou {"name": ...})
REF_FIELDS = {
    "dcim.devices":       ["role", "platform", "site", "tenant", "location", "rack"],
    "dcim.platforms":     ["manufacturer"],
    "dcim.device_types":  ["manufacturer"],
    "dcim.interfaces":    ["device", "parent"],
    "ipam.vrfs":          ["tenant"],
    "circuits.circuits":  ["provider", "type", "tenant"],
}

# Campos inteiros
INT_FIELDS = {
    "dcim.device_types": ["u_height", "weight", "airflow"],
    "dcim.interfaces":   ["speed", "mtu"],
}

# -------------------- utilitários --------------------

def norm(s: str) -> str:
    return re.sub(r"\s+", "_", s.strip().lower())

def is_int_str(s: Any) -> bool:
    return isinstance(s, str) and s.isdigit()

def to_ref(v: Any) -> Any:
    """Converte valor em referência aceita pelo pynetbox:
       - int -> int (id)
       - "123" -> 123 (id)
       - "Nome" -> {"name": "Nome"}"""
    if isinstance(v, int):
        return v
    if is_int_str(v):
        return int(v)
    if isinstance(v, str) and v.strip():
        return {"name": v.strip()}
    return v

def clean_row(row: Dict[str, Any]) -> Dict[str, Any]:
    out = {}
    for k, v in row.items():
        if isinstance(v, str):
            v = v.strip()
        if v not in (None, "", "null", "None"):
            out[k] = v
    return out

def load_csv_rows(path: pathlib.Path) -> List[Dict[str, Any]]:
    with path.open(newline="", encoding="utf-8-sig") as f:
        r = csv.DictReader(f)
        if not r.fieldnames:
            return []
        r.fieldnames = [norm(h) for h in r.fieldnames]
        return [clean_row(x) for x in r]

def dup_err_text(e: Any) -> str:
    try:
        return (e if isinstance(e, str) else str(e)).lower()
    except Exception:
        return str(e).lower()

def is_dup_error(e: RequestError) -> bool:
    t = dup_err_text(e.error)
    return ("already exists" in t) or ("must be unique" in t) or ("duplicate ip address" in t)

# -------------------- resoluções simples --------------------

def res_site_id(nb, name: str) -> Optional[int]:
    obj = nb.dcim.sites.get(name=name)
    return obj.id if obj else None

def res_tenant_id(nb, name: str) -> Optional[int]:
    obj = nb.tenancy.tenants.get(name=name)
    return obj.id if obj else None

def res_vrf_id(nb, vrf_name: str, tenant_name: Optional[str]) -> Optional[int]:
    """Tenta VRF por (name, tenant) e depois só por name."""
    if tenant_name:
        tid = res_tenant_id(nb, tenant_name)
        if tid:
            try:
                obj = nb.ipam.vrfs.get(name=vrf_name, tenant_id=tid)
                if obj:
                    return obj.id
            except ValueError:
                matches = list(nb.ipam.vrfs.filter(name=vrf_name, tenant_id=tid))
                if matches:
                    return matches[0].id
    # fallback: sem tenant
    try:
        obj = nb.ipam.vrfs.get(name=vrf_name)
        if obj:
            return obj.id
    except ValueError:
        matches = list(nb.ipam.vrfs.filter(name=vrf_name))
        if matches:
            return matches[0].id
    return None

# -------------------- transformação por endpoint --------------------

def transform_row(nb, ep: str, r: Dict[str, Any]) -> Dict[str, Any]:
    r = dict(r)  # cópia

    # limpezas simples
    for k in ("description", "comments", "tags", "label"):
        if r.get(k) in (None, "", "null", "None"):
            r.pop(k, None)

    # alias: alguns CSVs usam "device_role"
    if ep == "dcim.devices" and "device_role" in r and "role" not in r:
        r["role"] = r.pop("device_role")

    # converter referências
    for k in REF_FIELDS.get(ep, []):
        if k in r:
            r[k] = to_ref(r[k])

    # tipos inteiros
    for k in INT_FIELDS.get(ep, []):
        if k in r and is_int_str(r[k]):
            r[k] = int(r[k])

    # device_type pode vir como nome (e opcionalmente o fabricante)
    if ep == "dcim.devices" and "device_type" in r:
        model = r.pop("device_type")
        manuf = r.get("manufacturer") or r.get("device_manufacturer")
        r["device_type"] = (
            int(model) if is_int_str(model)
            else ({"model": model, "manufacturer": to_ref(manuf)} if manuf else {"model": model})
        )

    # ip addresses: resolver VRF e tenant por nome
    if ep == "ipam.ip_addresses":
        ten = r.get("tenant")
        ten_name = (ten.get("name") if isinstance(ten, dict) else ten) if ten else None
        if r.get("vrf"):
            vid = res_vrf_id(nb, str(r["vrf"]), ten_name)
            r["vrf"] = vid if vid is not None else {"name": r["vrf"]}
        if ten_name:
            r["tenant"] = to_ref(ten_name)

    return r

# -------------------- operações (create / update) --------------------

def upsert_device(nb, row: Dict[str, Any]) -> str:
    """Devices: match por (name + site). Se existir → update parcial; senão → create."""
    name = row.get("name")
    site = row.get("site")
    sname = site.get("name") if isinstance(site, dict) else site
    if not (name and sname):
        nb.dcim.devices.create(row); return "created"

    sid = res_site_id(nb, sname)
    if not sid:
        nb.dcim.devices.create(row); return "created"

    ex = nb.dcim.devices.get(name=name, site_id=sid)
    if ex:
        payload = {k: v for k, v in row.items() if k not in ("name", "site")}
        if not payload:
            return "skipped"
        try:
            ex.update(payload); return "updated"
        except RequestError as e:
            return "skipped" if is_dup_error(e) else "error"
    nb.dcim.devices.create(row); return "created"

def upsert_ip(endpoint, row: Dict[str, Any]) -> str:
    """IPs: match por address (+ vrf_id se houver). Atualiza campos comuns."""
    addr = row.get("address")
    vrf_id = row.get("vrf") if isinstance(row.get("vrf"), int) else None
    if not addr:
        return "skipped"

    try:
        ex = endpoint.get(address=addr, vrf_id=vrf_id) if vrf_id else endpoint.get(address=addr)
    except ValueError:
        lst = list(endpoint.filter(address=addr, vrf_id=vrf_id) if vrf_id else endpoint.filter(address=addr))
        ex = lst[0] if lst else None

    if ex:
        payload_keys = ("status", "role", "tenant", "description", "dns_name", "nat_inside", "nat_outside")
        payload = {k: row[k] for k in payload_keys if k in row}
        if payload:
            try:
                ex.update(payload); return "updated"
            except RequestError as e:
                return "skipped" if is_dup_error(e) else "error"
        return "skipped"

    try:
        endpoint.create(row); return "created"
    except RequestError as e:
        return "skipped" if is_dup_error(e) else "error"

# -------------------- descoberta de CSVs e roteamento --------------------

def detect_endpoint(relname: str) -> Optional[str]:
    """
    Regras:
      - remove prefixo numérico (ex.: '1_devices.csv' -> 'devices')
      - troca hífen por underscore e tira a extensão
    """
    base = pathlib.Path(relname).name.lower()
    base = re.sub(r"^\d+_", "", base)
    base = base.replace("-", "_")
    key  = base.replace(".csv", "")
    return NAME2EP.get(key)

def collect_csvs(base: pathlib.Path) -> List[Tuple[int, str, pathlib.Path]]:
    """Retorna [(ordem, relativo, caminho)] ignorando 'cables'."""
    items: List[Tuple[int, str, pathlib.Path]] = []
    for p in base.rglob("*.csv"):
        nl = p.name.lower()
        if "cables" in nl:  # ignorar cabos neste importador
            continue
        rel = p.relative_to(base).as_posix()
        m = re.match(r"^(\d+)_", p.name)
        order = int(m.group(1)) if m and 1 <= int(m.group(1)) <= 7 else 9999
        items.append((order, rel, p))
    items.sort(key=lambda x: (x[0], x[1]))
    return items

def get_endpoint_object(nb, ep_path: str):
    mod, attr = ep_path.split(".", 1)
    return getattr(getattr(nb, mod), attr)

# -------------------- execução --------------------

def process_file(nb, ep: str, rows: List[Dict[str, Any]], ip_upsert: bool) -> Tuple[int, int, int, int]:
    endpoint = get_endpoint_object(nb, ep)
    created = updated = skipped = errors = 0

    for r in rows:
        try:
            if ep == "dcim.devices":
                res = upsert_device(nb, r)
            elif ep == "ipam.ip_addresses" and ip_upsert:
                res = upsert_ip(endpoint, r)
            else:
                endpoint.create(r); res = "created"
        except RequestError as e:
            res = "skipped" if is_dup_error(e) else "error"
        except Exception:
            res = "error"

        if   res == "created": created += 1
        elif res == "updated": updated += 1
        elif res == "skipped": skipped += 1
        else:                  errors  += 1

    return created, updated, skipped, errors

def run(nb, base: pathlib.Path, ip_upsert: bool):
    items = collect_csvs(base)
    if not items:
        print("Nenhum CSV encontrado."); return

    current_header = None
    for order, rel, path in items:
        header = "PRIORIDADE (1..7)" if order != 9999 else "OUTROS"
        if header != current_header:
            current_header = header
            print(f"\n=== {header} ===")

        ep = detect_endpoint(rel)
        if not ep:
            print(f"[{order}] {rel} → endpoint não identificado, pulando.")
            continue

        print(f"[{order}] {rel} → {ep}")
        raw_rows = load_csv_rows(path)
        rows = [transform_row(nb, ep, r) for r in raw_rows]

        print(f" - POST {ep}: {len(rows)} registros")
        c,u,s,e = process_file(nb, ep, rows, ip_upsert)
        tail = f"ok={c}"
        if u: tail += f" upd={u}"
        if s: tail += f" skip={s}"
        if e: tail += f" erros={e}"
        print(f"   → {tail}")

def main():
    ap = argparse.ArgumentParser(description="NetBox CSV Importer — Mini")
    ap.add_argument("base_dir", help="Pasta com os CSVs (ex.: ./netbox/arquivos_csv)")
    ap.add_argument("--ip-upsert", action="store_true", help="Atualiza IPs existentes (address [+ vrf])")
    args = ap.parse_args()

    base = pathlib.Path(args.base_dir).resolve()
    if not base.exists():
        print(f"Pasta não encontrada: {base}")
        sys.exit(1)

    nb = pynetbox.api(NETBOX_URL, token=NETBOX_TOKEN)
    print(f"Processando pasta: {base}")
    run(nb, base, args.ip_upsert)

if __name__ == "__main__":
    main()
