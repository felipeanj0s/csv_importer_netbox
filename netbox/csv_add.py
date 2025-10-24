#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
NetBox CSV Importer — Full (csv_add.py) — rev3

Mudanças chave vs rev2:
- Suporte a circuits.circuit_terminations (7_circuit_terminations.csv)
- Resolução de provider/type por cache (nome ou slug)
- Cache de devices tolerante a espaço/hífen/acentos (lookup robusto)
- circuits: aceita name/circuit_id -> cid; parse de tags; resolve provider/type
- cables: aceita dcim.interface e circuits.circuittermination em A/B
"""

from __future__ import annotations

import argparse, csv, os, pathlib, re, sys, subprocess, unicodedata
from typing import Any, Dict, List, Optional, Tuple

# ---------------- deps ----------------
REQUIRED_PACKAGES = ["pynetbox>=7.5.0"]
OPTIONAL_PACKAGES = ["python-dotenv>=1.0.0"]

def _pip_install(pkgs: List[str]) -> None:
    if not pkgs: return
    cmd = [sys.executable, "-m", "pip", "install", "--no-input", "--no-cache-dir", *pkgs]
    subprocess.run(cmd, check=True)

def _ensure_deps() -> None:
    import importlib.util as iu
    missing=[]
    if iu.find_spec("pynetbox") is None:
        missing.append(REQUIRED_PACKAGES[0])
    if pathlib.Path(".env").exists() or pathlib.Path("./netbox/.env").exists():
        if iu.find_spec("dotenv") is None:
            missing.append(OPTIONAL_PACKAGES[0])
    if missing:
        print(f"[INFO] Instalando dependências: {', '.join(missing)}")
        _pip_install(missing)

_ensure_deps()

import pynetbox  # type: ignore
from pynetbox.core.query import RequestError  # type: ignore
try:
    from dotenv import load_dotenv  # type: ignore
except Exception:
    load_dotenv = None

# ---------------- conf ----------------
NETBOX_URL_DEFAULT   = "http://192.168.0.88:8000"
NETBOX_TOKEN_DEFAULT = "7fa821d37d939f537349df6381e22aaff8babe22"

NETBOX_URL   = os.getenv("NETBOX_URL", NETBOX_URL_DEFAULT)
NETBOX_TOKEN = os.getenv("NETBOX_TOKEN", NETBOX_TOKEN_DEFAULT)

def _load_dotenv_if_any() -> None:
    global NETBOX_URL, NETBOX_TOKEN
    if load_dotenv:
        for p in (pathlib.Path(".env"), pathlib.Path("./netbox/.env")):
            if p.exists():
                load_dotenv(p)
        NETBOX_URL   = os.getenv("NETBOX_URL", NETBOX_URL)
        NETBOX_TOKEN = os.getenv("NETBOX_TOKEN", NETBOX_TOKEN)

_load_dotenv_if_any()

# ---------------- maps ----------------
NAME2EP: Dict[str, str] = {
    "manufacturers":        "dcim.manufacturers",
    "platforms":            "dcim.platforms",
    "device_roles":         "dcim.device_roles",
    "device_types":         "dcim.device_types",
    "netbox_tenants":       "tenancy.tenants",
    "sites":                "dcim.sites",
    "devices":              "dcim.devices",
    "interfaces":           "dcim.interfaces",
    "vrfs":                 "ipam.vrfs",
    "ip_addresses":         "ipam.ip_addresses",
    "providers":            "circuits.providers",
    "circuit_types":        "circuits.circuit_types",
    "circuits":             "circuits.circuits",
    "circuit_terminations": "circuits.circuit_terminations",
    "cables":               "dcim.cables",
}

REF_FIELDS: Dict[str, List[str]] = {
    "dcim.devices":       ["role", "platform", "site", "tenant", "location", "rack"],
    "dcim.platforms":     ["manufacturer"],
    "dcim.device_types":  ["manufacturer"],
    "dcim.interfaces":    ["parent"],  # device é resolvido manualmente
    "ipam.vrfs":          ["tenant"],
    "circuits.circuits":  ["provider", "type", "tenant"],
}

INT_FIELDS: Dict[str, List[str]] = {
    "dcim.device_types": ["u_height", "weight", "airflow"],
    "dcim.interfaces":   ["speed", "mtu"],
}

# ---------------- utils ----------------
def norm(s: str) -> str:
    return re.sub(r"\s+", "_", s.strip().lower())

def is_int_str(s: Any) -> bool:
    return isinstance(s, str) and s.isdigit()

def to_ref(v: Any) -> Any:
    if isinstance(v, int): return v
    if is_int_str(v): return int(v)
    if isinstance(v, str) and v.strip(): return {"name": v.strip()}
    return v

def _to_bool(v: Any) -> Optional[bool]:
    if isinstance(v, bool): return v
    if isinstance(v, str):
        t = v.strip().lower()
        if t in ("true","yes","1","y","on"): return True
        if t in ("false","no","0","n","off"): return False
    return None

def clean_row(row: Dict[str, Any]) -> Dict[str, Any]:
    out: Dict[str, Any] = {}
    for k, v in row.items():
        if k is None:
            continue
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
        r.fieldnames = [norm(h) if h is not None else None for h in r.fieldnames]
        return [clean_row(x) for x in r]

def dup_err_text(e: Any) -> str:
    try: return (e if isinstance(e, str) else str(e)).lower()
    except Exception: return str(e).lower()

def is_dup_error(e: RequestError) -> bool:
    t = dup_err_text(e.error)
    tokens = (
        "already exists",
        "must be unique",
        "duplicate ip address",
        "unique constraint",
        "violates unique",
        "constraint",
        "is violated",
        "with this manufacturer and name already exists",
        "with this manufacturer and slug already exists",
        "with this provider and circuit id already exists",
        "tenant_unique_name",
        "tenant_unique_slug",
    )
    return any(tok in t for tok in tokens)

# ---------------- normalization helpers ----------------
def _norm_key(s: str) -> str:
    s = str(s)
    s = unicodedata.normalize("NFKD", s)
    s = "".join(ch for ch in s if not unicodedata.combining(ch))
    s = s.strip().lower()
    s = s.replace("–","-").replace("—","-")
    s = re.sub(r"\s+", " ", s)
    return s

def _variants(name: str):
    n = _norm_key(name)
    yield n
    yield n.replace(" ", "-")
    yield n.replace("-", " ")
    yield n.replace(" ", "")
    yield n.replace("-", "")

# ---------------- caches ----------------
DEV_CACHE: Dict[str, int]   = {}
MFR_CACHE: Dict[str, int]   = {}
PROV_CACHE: Dict[str, int]  = {}
CTYPE_CACHE: Dict[str, int] = {}

def _cache_put(cache: Dict[str,int], key: Any, val: int) -> None:
    if key is None: return
    k = _norm_key(key)
    cache[k] = val
    cache[k.replace("-", " ")] = val
    cache[k.replace(" ", "-")] = val
    cache[k.replace(" ", "")]  = val
    cache[k.replace("-", "")]  = val

def _build_caches(nb) -> None:
    DEV_CACHE.clear()
    for d in nb.dcim.devices.all():
        if getattr(d, "name", None):
            _cache_put(DEV_CACHE, d.name, d.id)

    MFR_CACHE.clear()
    for m in nb.dcim.manufacturers.all():
        _cache_put(MFR_CACHE, getattr(m,"name",None), m.id)
        _cache_put(MFR_CACHE, getattr(m,"slug",None), m.id)

    PROV_CACHE.clear()
    for p in nb.circuits.providers.all():
        _cache_put(PROV_CACHE, getattr(p,"name",None), p.id)
        _cache_put(PROV_CACHE, getattr(p,"slug",None), p.id)

    CTYPE_CACHE.clear()
    for ct in nb.circuits.circuit_types.all():
        _cache_put(CTYPE_CACHE, getattr(ct,"name",None), ct.id)
        _cache_put(CTYPE_CACHE, getattr(ct,"slug",None), ct.id)

def res_site_id(nb, name: str) -> Optional[int]:
    try:
        o = nb.dcim.sites.get(name=name)
        if o: return o.id
    except ValueError:
        ls = list(nb.dcim.sites.filter(name=name))
        if ls: return ls[0].id
    return None

def res_tenant_id(nb, name: str) -> Optional[int]:
    try:
        o = nb.tenancy.tenants.get(name=name)
        if o: return o.id
    except ValueError:
        ls = list(nb.tenancy.tenants.filter(name=name))
        if ls: return ls[0].id
    return None

def res_vrf_id(nb, vrf_name: str, tenant_name: Optional[str]) -> Optional[int]:
    if tenant_name:
        tid = res_tenant_id(nb, tenant_name)
        if tid:
            try:
                o = nb.ipam.vrfs.get(name=vrf_name, tenant_id=tid)
                if o: return o.id
            except ValueError:
                ls = list(nb.ipam.vrfs.filter(name=vrf_name, tenant_id=tid))
                if ls: return ls[0].id
    try:
        o = nb.ipam.vrfs.get(name=vrf_name)
        if o: return o.id
    except ValueError:
        ls = list(nb.ipam.vrfs.filter(name=vrf_name))
        if ls: return ls[0].id
    return None

def _id_by(cache: Dict[str,int], value: Any) -> Optional[int]:
    if isinstance(value, int): return value
    if isinstance(value, str):
        for v in _variants(value):
            if v in cache: return cache[v]
    if isinstance(value, dict):
        for k in ("id","name","slug"):
            if k in value:
                return _id_by(cache, value[k])
    return None

def res_device_id(name: str) -> Optional[int]:
    if not name: return None
    for v in _variants(name):
        if v in DEV_CACHE:
            return DEV_CACHE[v]
    return None

def res_interface_id(nb, device_name: str, if_name: str) -> Optional[int]:
    did = res_device_id(device_name)
    if not did: return None
    try:
        o = nb.dcim.interfaces.get(device_id=did, name=str(if_name).strip())
        if o: return o.id
    except ValueError:
        ls = list(nb.dcim.interfaces.filter(device_id=did, name=str(if_name).strip()))
        if ls: return ls[0].id
    return None

def res_circuit_id(nb, cid_or_name: str) -> Optional[int]:
    if not cid_or_name: return None
    if is_int_str(cid_or_name):
        try:
            o = nb.circuits.circuits.get(int(cid_or_name))
            if o: return o.id
        except Exception:
            pass
    try:
        o = nb.circuits.circuits.get(cid=cid_or_name)
        if o: return o.id
    except ValueError:
        ls = list(nb.circuits.circuits.filter(cid=cid_or_name))
        if ls: return ls[0].id
    return None

def res_circuit_term_id(nb, circuit_cid: str, side: str) -> Optional[int]:
    cid = res_circuit_id(nb, circuit_cid)
    if not cid: return None
    s = (side or "").strip().upper()
    if s not in ("A","Z"): return None
    try:
        o = nb.circuits.circuit_terminations.get(circuit_id=cid, term_side=s)
        if o: return o.id
    except ValueError:
        ls = list(nb.circuits.circuit_terminations.filter(circuit_id=cid, term_side=s))
        if ls: return ls[0].id
    return None

# ---------------- transform ----------------
def _pop_first(d: Dict[str, Any], keys: List[str]) -> Optional[Any]:
    for k in keys:
        if k in d:
            return d.pop(k)
    return None

def _parse_tags(v: Any) -> Optional[List[str]]:
    if v is None: return None
    if isinstance(v, list): return v
    if isinstance(v, str):
        parts = [x.strip() for x in v.split(",") if x.strip()]
        return parts or None
    return None

def transform_row(nb, ep: str, r: Dict[str, Any]) -> Dict[str, Any]:
    r = dict(r)

    # remoção de vazios
    for k in ("description","comments","label"):
        if r.get(k) in (None,"","null","None"):
            r.pop(k, None)

    # aliases
    if ep == "dcim.devices" and "device_role" in r and "role" not in r:
        r["role"] = r.pop("device_role")

    # manufacturers em platforms/device_types via cache
    if ep in ("dcim.platforms","dcim.device_types") and "manufacturer" in r:
        mid = _id_by(MFR_CACHE, r["manufacturer"])
        r["manufacturer"] = mid if mid else {"name": r["manufacturer"]}

    # interfaces
    if ep == "dcim.interfaces":
        dev_val = _pop_first(r, ["device","device_name","device__name","host"])
        if isinstance(dev_val, dict): dev_val = dev_val.get("name")
        did = res_device_id(str(dev_val) if dev_val else "")
        if not did:
            raise ValueError(f"Device não encontrado para interface: {dev_val}")
        r["device"] = did

        for k in ("enabled","mark_connected","mgmt_only"):
            if k in r:
                bv = _to_bool(r[k])
                if bv is None: r.pop(k, None)
                else: r[k] = bv

        if "duplex" in r and str(r["duplex"]).strip().lower() in ("true","false"):
            r.pop("duplex", None)

        for k in ("speed","mtu"):
            if k in r:
                s = str(r[k]).strip()
                if s.isdigit(): r[k] = int(s)
                else: r.pop(k, None)

        if "name" in r and isinstance(r["name"], str):
            r["name"] = r["name"].strip()

    # cables
    if ep == "dcim.cables":
        def _side(side: str) -> Tuple[str, Optional[int]]:
            typ = _pop_first(r,[f"side_{side}_type", f"{side}_type"]) or "dcim.interface"
            typ = str(typ).strip().lower()
            if typ not in ("dcim.interface","circuits.circuittermination"):
                raise ValueError(f"Tipo de terminação não suportado: {typ}")

            if typ == "dcim.interface":
                dev = _pop_first(r,[f"side_{side}_device", f"{side}_device"])
                nam = _pop_first(r,[f"side_{side}_name", f"{side}_name", f"{side}_interface"])
                if not (dev and nam):
                    raise ValueError(f"Faltam campos para interface do lado {side.upper()}")
                iid = res_interface_id(nb, str(dev), str(nam))
                if not iid:
                    raise ValueError(f"Interface não encontrada: {dev} {nam}")
                return "dcim.interface", iid

            # circuits.circuittermination
            circ = _pop_first(r,[f"side_{side}_circuit", f"{side}_circuit"])
            sidx = _pop_first(r,[f"side_{side}_side", f"{side}_side"])
            if not (circ and sidx):
                raise ValueError(f"Faltam campos de circuito/side no lado {side.upper()} (ex.: {side}_circuit, {side}_side)")
            tid = res_circuit_term_id(nb, str(circ), str(sidx))
            if not tid:
                raise ValueError(f"CircuitTermination não encontrado: {circ} {sidx}")
            return "circuits.circuittermination", tid

        a_typ, a_id = _side("a")
        b_typ, b_id = _side("b")

        # NetBox v4+: usar a_/b_terminations
        r["a_terminations"] = [{"object_type": a_typ, "object_id": a_id}]
        r["b_terminations"] = [{"object_type": b_typ, "object_id": b_id}]

        # limpar chaves antigas se vieram no CSV
        for k in ("termination_a_type","termination_a_id","termination_b_type","termination_b_id"):
            r.pop(k, None)

        if "tags" in r:
            tv = _parse_tags(r["tags"])
            if tv is None: r.pop("tags", None)
            else: r["tags"] = tv

    # devices: device_type pode vir como nome
    if ep == "dcim.devices" and "device_type" in r:
        model = r.pop("device_type")
        manuf = r.get("manufacturer") or r.get("device_manufacturer")
        r["device_type"] = (
            int(model) if is_int_str(model)
            else ({"model": model, "manufacturer": to_ref(manuf)} if manuf else {"model": model})
        )

    # circuits: aceitar name/circuit_id -> cid; tags; provider/type por cache
    if ep == "circuits.circuits":
        if "cid" not in r:
            alt = _pop_first(r, ["name","circuit_id","circuitid","circuit"])
            if alt: r["cid"] = alt
        prov_val = _pop_first(r, ["provider","provider_name","provider__name","provider_slug","provider__slug"])
        if prov_val is not None:
            pid = _id_by(PROV_CACHE, prov_val)
            r["provider"] = pid if pid is not None else {"name": prov_val}
        typ_val = _pop_first(r, ["type","circuit_type","type_name","type__name","type_slug","type__slug"])
        if typ_val is not None:
            tid = _id_by(CTYPE_CACHE, typ_val)
            r["type"] = tid if tid is not None else {"name": typ_val}
        if "tags" in r:
            tv = _parse_tags(r["tags"])
            if tv is None: r.pop("tags", None)
            else: r["tags"] = tv

    # circuit terminations: circuit por cid; term_side upper; site por nome
    if ep == "circuits.circuit_terminations":
        cval = _pop_first(r, ["circuit","cid","circuit_id"])
        cid = res_circuit_id(nb, str(cval) if cval else "")
        if not cid:
            raise ValueError(f"Circuito não encontrado (cid/name): {cval}")
        r["circuit"] = cid
        if "term_side" in r:
            r["term_side"] = str(r["term_side"]).strip().upper()
        if "site" in r and isinstance(r["site"], str):
            sid = res_site_id(nb, r["site"])
            if sid: r["site"] = sid

    # ip addresses: resolver VRF e tenant
    if ep == "ipam.ip_addresses":
        ten = r.get("tenant")
        ten_name = (ten.get("name") if isinstance(ten, dict) else ten) if ten else None
        if r.get("vrf"):
            vid = res_vrf_id(nb, str(r["vrf"]), ten_name)
            r["vrf"] = vid if vid is not None else {"name": r["vrf"]}
        if ten_name:
            r["tenant"] = to_ref(ten_name)

    # refs/ints genéricos
    for k in REF_FIELDS.get(ep, []):
        if k in r:
            r[k] = to_ref(r[k])
    for k in INT_FIELDS.get(ep, []):
        if k in r and is_int_str(r[k]):
            r[k] = int(r[k])

    return r

# ---------------- upserts ----------------
def upsert_device(nb, row: Dict[str, Any]) -> str:
    name = row.get("name")
    site = row.get("site")
    sname = site.get("name") if isinstance(site, dict) else site
    if not (name and sname):
        nb.dcim.devices.create(row); return "created"

    sid = res_site_id(nb, sname)
    if not sid:
        nb.dcim.devices.create(row); return "created"

    try:
        ex = nb.dcim.devices.get(name=name, site_id=sid)
    except ValueError:
        lst = list(nb.dcim.devices.filter(name=name, site_id=sid))
        ex = lst[0] if lst else None

    if ex:
        payload = {k: v for k, v in row.items() if k not in ("name", "site")}
        if not payload: return "skipped"
        try:
            ex.update(payload); return "updated"
        except RequestError as e:
            return "skipped" if is_dup_error(e) else "error"
    nb.dcim.devices.create(row); return "created"

def upsert_interface(nb, row: Dict[str, Any]) -> str:
    did = row.get("device") if isinstance(row.get("device"), int) else None
    name = str(row.get("name") or "").strip()
    if did and name:
        try:
            ex = nb.dcim.interfaces.get(device_id=did, name=name)
        except ValueError:
            lst = list(nb.dcim.interfaces.filter(device_id=did, name=name))
            ex = lst[0] if lst else None
        if ex:
            payload = {k: v for k, v in row.items() if k not in ("device", "name")}
            if not payload: return "skipped"
            try:
                ex.update(payload); return "updated"
            except RequestError as e:
                return "skipped" if is_dup_error(e) else "error"
    nb.dcim.interfaces.create(row); return "created"

def upsert_ip(endpoint, row: Dict[str, Any]) -> str:
    addr = row.get("address")
    vrf_id = row.get("vrf") if isinstance(row.get("vrf"), int) else None
    if not addr: return "skipped"

    try:
        ex = endpoint.get(address=addr, vrf_id=vrf_id) if vrf_id else endpoint.get(address=addr)
    except ValueError:
        lst = list(endpoint.filter(address=addr, vrf_id=vrf_id) if vrf_id else endpoint.filter(address=addr))
        ex = lst[0] if lst else None

    if ex:
        payload_keys = ("status","role","tenant","description","dns_name","nat_inside","nat_outside","tags")
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

# ---------------- routing ----------------
def detect_endpoint(relname: str) -> Optional[str]:
    base = pathlib.Path(relname).name.lower()
    base = re.sub(r"^\d+_", "", base).replace("-", "_")
    key  = base.replace(".csv", "")
    return NAME2EP.get(key)

def collect_csvs(base: pathlib.Path) -> List[Tuple[int, str, pathlib.Path]]:
    items=[]
    for p in base.rglob("*.csv"):
        rel = p.relative_to(base).as_posix()
        m = re.match(r"^(\d+)_", p.name)
        order = int(m.group(1)) if m and 1 <= int(m.group(1)) <= 8 else 9999
        items.append((order, rel, p))
    items.sort(key=lambda x: (x[0], x[1]))
    return items

def get_endpoint_object(nb, ep_path: str):
    mod, attr = ep_path.split(".", 1)
    return getattr(getattr(nb, mod), attr)

# ---------------- exec ----------------
def process_file(nb, ep: str, rows: List[Dict[str, Any]], ip_upsert: bool) -> Tuple[int,int,int,int]:
    endpoint = get_endpoint_object(nb, ep)
    created = updated = skipped = errors = 0

    for r in rows:
        try:
            if ep == "dcim.devices":
                res = upsert_device(nb, r)
            elif ep == "dcim.interfaces":
                res = upsert_interface(nb, r)
            elif ep == "ipam.ip_addresses" and ip_upsert:
                res = upsert_ip(endpoint, r)
            else:
                endpoint.create(r); res = "created"
        except RequestError as e:
            print(f"   [ERRO API] {ep}: {e.error}", file=sys.stderr)
            res = "skipped" if is_dup_error(e) else "error"
        except Exception as e:
            print(f"   [ERRO] {ep}: {e}", file=sys.stderr)
            res = "error"

        if   res == "created": created += 1
        elif res == "updated": updated += 1
        elif res == "skipped": skipped += 1
        else:                  errors  += 1

    return created, updated, skipped, errors

def _check_conn_or_exit(nb) -> None:
    try:
        _ = nb.dcim.sites.count()
    except Exception as e:
        print(f"[ERRO] Falha ao conectar no NetBox ({NETBOX_URL}). Verifique URL/TOKEN. Detalhes: {e}", file=sys.stderr)
        sys.exit(2)

def run(nb, base: pathlib.Path, ip_upsert: bool) -> None:
    _build_caches(nb)

    items = collect_csvs(base)
    if not items:
        print("Nenhum CSV encontrado."); return

    current_header = None
    for order, rel, path in items:
        header = "PRIORIDADE (1..8)" if order != 9999 else "OUTROS"
        if header != current_header:
            current_header = header
            print(f"\n=== {header} ===")

        ep = detect_endpoint(rel)
        if not ep:
            print(f"[{order}] {rel} → endpoint não identificado, pulando.")
            continue

        print(f"[{order}] {rel} → {ep}")
        raw_rows = load_csv_rows(path)

        rows: List[Dict[str, Any]] = []
        for idx, rr in enumerate(raw_rows, start=1):
            try:
                rows.append(transform_row(nb, ep, rr))
            except Exception as e:
                print(f"   [ERRO] transform {ep} linha {idx}: {e}", file=sys.stderr)

        print(f" - POST {ep}: {len(rows)} registros")
        c,u,s,e = process_file(nb, ep, rows, ip_upsert)
        tail = f"ok={c}"
        if u: tail += f" upd={u}"
        if s: tail += f" skip={s}"
        if e: tail += f" erros={e}"
        print(f"   → {tail}")

def main() -> None:
    ap = argparse.ArgumentParser(description="NetBox CSV Importer — Full rev3")
    ap.add_argument("base_dir", help="Pasta com os CSVs (ex.: ./netbox/arquivos_csv)")
    ap.add_argument("--ip-upsert", action="store_true", help="Atualiza IPs existentes (address [+ vrf])")
    args = ap.parse_args()

    base = pathlib.Path(args.base_dir).resolve()
    if not base.exists():
        print(f"Pasta não encontrada: {base}")
        sys.exit(1)

    if not NETBOX_URL or not NETBOX_TOKEN:
        print("[ERRO] Defina NETBOX_URL e NETBOX_TOKEN (variáveis de ambiente ou .env).", file=sys.stderr)
        sys.exit(1)

    nb = pynetbox.api(NETBOX_URL, token=NETBOX_TOKEN)
    _check_conn_or_exit(nb)

    print(f"Processando pasta: {base}")
    run(nb, base, args.ip_upsert)

if __name__ == "__main__":
    main()
