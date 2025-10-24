#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
NetBox CSV Exporter (csv_export.py)
Cria pasta: netbox_export-<host>-YYYYMMDD_HHMMSS

Uso:
  python netbox/csv_export.py                 # cria subpasta no cwd
  python netbox/csv_export.py /caminho/base   # cria subpasta dentro de /caminho/base
Vars:
  NETBOX_URL, NETBOX_TOKEN (ou .env)
"""
from __future__ import annotations
import csv, os, sys, pathlib, subprocess, datetime, urllib.parse
from typing import Any, Dict, List, Tuple, Iterable

# ---------------- deps ----------------
REQUIRED = ["pynetbox>=7.5.0"]
OPTIONAL  = ["python-dotenv>=1.0.0"]

def _pip(pkgs: List[str]) -> None:
    if not pkgs: return
    subprocess.run([sys.executable,"-m","pip","install","--no-input","--no-cache-dir",*pkgs], check=True)

def _ensure():
    import importlib.util as iu
    miss=[]
    if iu.find_spec("pynetbox") is None: miss.append(REQUIRED[0])
    if pathlib.Path(".env").exists() or pathlib.Path("./netbox/.env").exists():
        if iu.find_spec("dotenv") is None: miss.append(OPTIONAL[0])
    if miss: _pip(miss)
_ensure()

import pynetbox  # type: ignore
try:
    from dotenv import load_dotenv  # type: ignore
except Exception:
    load_dotenv=None

# ---------------- conf ----------------
NETBOX_URL = os.getenv("NETBOX_URL","http://192.168.0.88:8000")
NETBOX_TOKEN = os.getenv("NETBOX_TOKEN","7fa821d37d939f537349df6381e22aaff8babe22")
if load_dotenv:
    for p in (pathlib.Path(".env"), pathlib.Path("./netbox/.env")):
        if p.exists(): load_dotenv(p)
    NETBOX_URL = os.getenv("NETBOX_URL", NETBOX_URL)
    NETBOX_TOKEN = os.getenv("NETBOX_TOKEN", NETBOX_TOKEN)

# --------------- utils ----------------
def _wcsv(path: pathlib.Path, headers: List[str], rows: Iterable[Dict[str,Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=headers, extrasaction="ignore")
        w.writeheader()
        for r in rows:
            w.writerow({k: ("" if v is None else v) for k,v in r.items()})

def _name(obj, attr="name"):
    try: return getattr(obj, attr)
    except Exception: return None

def _host_from_url(u: str) -> str:
    try:
        p = urllib.parse.urlparse(u)
        return (p.hostname or "netbox").replace(".", "-")
    except Exception:
        return "netbox"

def _make_outdir(base: pathlib.Path) -> pathlib.Path:
    stamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    host  = _host_from_url(NETBOX_URL)
    d = base / f"netbox_export-{host}-{stamp}"
    d.mkdir(parents=True, exist_ok=True)
    return d

# ------------- extractors -------------
def rows_manufacturers(nb):
    for o in nb.dcim.manufacturers.all():
        yield {"name": _name(o), "slug": _name(o,"slug"), "description": _name(o,"description")}

def rows_platforms(nb):
    for o in nb.dcim.platforms.all():
        yield {"name": _name(o), "slug": _name(o,"slug"), "manufacturer": _name(getattr(o,"manufacturer",None))}

def rows_device_roles(nb):
    for o in nb.dcim.device_roles.all():
        yield {"name": _name(o), "slug": _name(o,"slug"), "color": _name(o,"color")}

def rows_device_types(nb):
    for o in nb.dcim.device_types.all():
        yield {
            "model": _name(o,"model"),
            "slug": _name(o,"slug"),
            "manufacturer": _name(getattr(o,"manufacturer",None)),
            "u_height": getattr(o,"u_height",None),
            "weight": getattr(o,"weight",None),
            "airflow": getattr(o,"airflow",None),
        }

def rows_tenants(nb):
    for o in nb.tenancy.tenants.all():
        yield {"name": _name(o),"slug": _name(o,"slug"),"description": _name(o,"description")}

def rows_sites(nb):
    for o in nb.dcim.sites.all():
        yield {"name": _name(o),"slug": _name(o,"slug"),"status": _name(o,"status"),"region": _name(getattr(o,"region",None))}

def rows_devices(nb):
    for o in nb.dcim.devices.all():
        yield {
            "name": _name(o),
            "site": _name(getattr(o,"site",None)),
            "role": _name(getattr(o,"role",None)),
            "device_type": _name(getattr(o,"device_type",None),"model"),
            "manufacturer": _name(getattr(o.device_type,"manufacturer",None)) if getattr(o,"device_type",None) else None,
            "platform": _name(getattr(o,"platform",None)),
            "tenant": _name(getattr(o,"tenant",None)),
            "serial": _name(o,"serial"),
        }

def rows_interfaces(nb):
    for o in nb.dcim.interfaces.all():
        yield {
            "device": _name(getattr(o,"device",None)),
            "name": _name(o,"name"),
            "type": _name(o,"type"),
            "speed": getattr(o,"speed",None),
            "duplex": _name(o,"duplex"),
            "enabled": getattr(o,"enabled",None),
            "mtu": getattr(o,"mtu",None),
            "mgmt_only": getattr(o,"mgmt_only",None),
            "label": _name(o,"label"),
            "description": _name(o,"description"),
        }

def rows_vrfs(nb):
    for o in nb.ipam.vrfs.all():
        yield {"name": _name(o),"rd": _name(o,"rd"),"tenant": _name(getattr(o,"tenant",None))}

def rows_ip_addresses(nb):
    for o in nb.ipam.ip_addresses.all():
        yield {
            "address": _name(o,"address"),
            "vrf": _name(getattr(o,"vrf",None)),
            "tenant": _name(getattr(o,"tenant",None)),
            "status": _name(o,"status"),
            "role": _name(o,"role"),
            "description": _name(o,"description"),
            "dns_name": _name(o,"dns_name"),
        }

def rows_providers(nb):
    for o in nb.circuits.providers.all():
        yield {"name": _name(o),"slug": _name(o,"slug")}

def rows_circuit_types(nb):
    for o in nb.circuits.circuit_types.all():
        yield {"name": _name(o),"slug": _name(o,"slug")}

def rows_circuits(nb):
    for o in nb.circuits.circuits.all():
        yield {
            "name": _name(o),
            "provider": _name(getattr(o,"provider",None)),
            "type": _name(getattr(o,"type",None)),
            "status": _name(o,"status"),
            "tenant": _name(getattr(o,"tenant",None)),
            "description": _name(o,"description"),
        }

def rows_cables(nb):
    # 1) Coleta terminações por cabo a partir de INTERFACES
    cable_map: Dict[int, List[Tuple[str, str, str]]] = {}  # cable_id -> [(type, dev_or_cid, name_or_side), ...]
    for iface in nb.dcim.interfaces.all():
        cab = getattr(iface, "cable", None)
        if not cab: 
            continue
        cid = getattr(cab, "id", None) or getattr(iface, "cable_id", None)
        if not cid:
            continue
        dev = _name(getattr(iface, "device", None))
        nam = _name(iface, "name")
        if not (dev and nam):
            continue
        cable_map.setdefault(int(cid), []).append(("dcim.interface", dev, nam))

    # 2) Coleta terminações por cabo a partir de CIRCUIT TERMINATIONS
    try:
        for term in nb.circuits.circuit_terminations.all():
            cab = getattr(term, "cable", None)
            if not cab:
                continue
            cid = getattr(cab, "id", None) or getattr(term, "cable_id", None)
            if not cid:
                continue
            circ = _name(getattr(term, "circuit", None))  # usa cid/name do circuito
            side = _name(term, "term_side")
            if not (circ and side):
                continue
            cable_map.setdefault(int(cid), []).append(("circuits.circuittermination", circ, side))
    except Exception:
        pass  # se circuits não estiverem em uso, ignore

    # 3) Busca label/description dos cabos e emite linhas somente quando houver 2 pontas
    for cab_id, terms in cable_map.items():
        if len(terms) != 2:
            continue  # precisa ter exatamente duas terminações

        try:
            cab = nb.dcim.cables.get(cab_id)
        except Exception:
            cab = None

        label = _name(cab, "label") if cab else None
        desc  = _name(cab, "description") if cab else None

        tA, tB = terms[0], terms[1]

        # Interface <-> Interface
        if tA[0] == "dcim.interface" and tB[0] == "dcim.interface":
            yield {
                "side_a_device": tA[1],
                "side_a_type": "dcim.interface",
                "side_a_name": tA[2],
                "side_b_device": tB[1],
                "side_b_type": "dcim.interface",
                "side_b_name": tB[2],
                "label": label,
                "description": desc,
            }
            continue

        # Misto com CircuitTermination
        out: Dict[str, Any] = {"label": label, "description": desc}
        # A
        if tA[0] == "dcim.interface":
            out.update({"side_a_type":"dcim.interface","side_a_device":tA[1],"side_a_name":tA[2]})
        else:
            out.update({"side_a_type":"circuits.circuittermination","side_a_circuit":tA[1],"side_a_side":tA[2]})
        # B
        if tB[0] == "dcim.interface":
            out.update({"side_b_type":"dcim.interface","side_b_device":tB[1],"side_b_name":tB[2]})
        else:
            out.update({"side_b_type":"circuits.circuittermination","side_b_circuit":tB[1],"side_b_side":tB[2]})
        yield out

# ------------- plan -------------
PLAN = [
    ("1_manufacturers.csv", ["name","slug","description"], rows_manufacturers),
    ("2_platforms.csv", ["name","slug","manufacturer"], rows_platforms),
    ("3_device_roles.csv", ["name","slug","color"], rows_device_roles),
    ("3_device_types.csv", ["model","slug","manufacturer","u_height","weight","airflow"], rows_device_types),
    ("3_netbox_tenants.csv", ["name","slug","description"], rows_tenants),
    ("3_sites.csv", ["name","slug","status","region"], rows_sites),
    ("4_devices.csv", ["name","site","role","device_type","manufacturer","platform","tenant","serial"], rows_devices),
    ("5_interfaces.csv", ["device","name","type","speed","duplex","enabled","mtu","mgmt_only","label","description"], rows_interfaces),
    ("5_VRFs.csv", ["name","rd","tenant"], rows_vrfs),
    ("6_IP_addresses.csv", ["address","vrf","tenant","status","role","description","dns_name"], rows_ip_addresses),
    ("6_providers.csv", ["name","slug"], rows_providers),
    ("6_circuit_types.csv", ["name","slug"], rows_circuit_types),
    ("7_circuits.csv", ["name","provider","type","status","tenant","description"], rows_circuits),
    ("8_cables.csv", [
        "side_a_device","side_a_type","side_a_name",
        "side_b_device","side_b_type","side_b_name",
        "side_a_circuit","side_a_side","side_b_circuit","side_b_side",
        "label","description"
    ], rows_cables),
]

# ------------- main -------------
def main():
    base = pathlib.Path(sys.argv[1]).resolve() if len(sys.argv)>1 else pathlib.Path.cwd()
    outdir = _make_outdir(base)
    if not NETBOX_URL or not NETBOX_TOKEN:
        print("[ERRO] Defina NETBOX_URL e NETBOX_TOKEN (.env ou ambiente).", file=sys.stderr)
        sys.exit(1)
    nb = pynetbox.api(NETBOX_URL, token=NETBOX_TOKEN)
    try:
        _ = nb.dcim.sites.count()
    except Exception as e:
        print(f"[ERRO] Conexão NetBox falhou: {e}", file=sys.stderr)
        sys.exit(2)

    print(f"[destino] {outdir}")
    for fname, headers, fn in PLAN:
        rows = list(fn(nb))
        _wcsv(outdir/fname, headers, rows)
        print(f"[ok] {fname}: {len(rows)} linhas")

if __name__ == "__main__":
    main()
