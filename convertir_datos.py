"""
Conversor de datos entre Excel y JSON.

La aplicación lee directamente data/biotren_datos.xlsx, pero este script
permite convertir entre formatos cuando se necesita:

    python convertir_datos.py xlsx2json   # Excel  -> JSON (genera respaldo)
    python convertir_datos.py json2xlsx   # JSON   -> Excel (reconstruye el libro)

Útil para: respaldos, control de versiones legible (JSON en git),
o regenerar el Excel si se corrompe.
"""

from __future__ import annotations

import datetime as dt
import json
import sys
from pathlib import Path

import pandas as pd

DATA_DIR = Path(__file__).parent / "data"
EXCEL_DB = DATA_DIR / "biotren_datos.xlsx"
SERVICES_JSON = DATA_DIR / "services_full.json"
INFRA_JSON = DATA_DIR / "infraestructura.json"


def _to_secs(v) -> int | None:
    if v is None or (isinstance(v, float) and pd.isna(v)):
        return None
    if isinstance(v, (dt.time, dt.datetime)):
        return v.hour * 3600 + v.minute * 60 + v.second
    if isinstance(v, (int, float)):
        return int(round(v * 86400)) if 0 <= v < 2 else int(v)
    if isinstance(v, str) and ":" in v:
        p = v.split(":")
        return int(p[0]) * 3600 + int(p[1]) * 60 + (int(p[2]) if len(p) > 2 else 0)
    return None


def _secs_to_time(s) -> dt.time | None:
    if s is None or pd.isna(s):
        return None
    s = int(s)
    return dt.time((s // 3600) % 24, (s % 3600) // 60, s % 60)


def xlsx_to_json() -> None:
    """Excel -> JSON (services_full.json + infraestructura.json)."""
    servicios = pd.read_excel(EXCEL_DB, sheet_name="Servicios")
    horarios = pd.read_excel(EXCEL_DB, sheet_name="Horarios")
    estaciones = pd.read_excel(EXCEL_DB, sheet_name="Estaciones")
    tramos = pd.read_excel(EXCEL_DB, sheet_name="Tramos")

    # Reconstruir servicios con passes anidados
    services = []
    horarios = horarios.sort_values(["service_id", "orden"])
    for _, s in servicios.iterrows():
        sid = s["id"]
        sp = horarios[horarios["service_id"] == sid]
        passes = [{
            "station": p["estacion"],
            "llega": _to_secs(p["llega"]),
            "sale": _to_secs(p["sale"]),
            "dist_km": float(p["dist_km"]) if pd.notna(p["dist_km"]) else 0,
        } for _, p in sp.iterrows()]
        services.append({
            "id": sid,
            "linea": s["linea"], "sentido": s["sentido"],
            "tren": s["tren"], "tipo": s["tipo"] if pd.notna(s["tipo"]) else "",
            "origen": s["origen"], "destino": s["destino"],
            "h_salida": _to_secs(s["h_salida"]),
            "h_llegada": _to_secs(s["h_llegada"]),
            "duracion_min": float(s["duracion_min"]) if pd.notna(s["duracion_min"]) else 0,
            "passes": passes,
        })

    dist_l1, dist_l2 = {}, {}
    for svc in services:
        tgt = dist_l1 if svc["linea"] == "L1" else dist_l2
        for p in svc["passes"]:
            tgt.setdefault(p["station"], p["dist_km"])

    SERVICES_JSON.write_text(json.dumps(
        {"services": services, "distancias_l1": dist_l1, "distancias_l2": dist_l2},
        ensure_ascii=False, indent=1), encoding="utf-8")

    infra = {
        "estaciones": estaciones.set_index("estacion").to_dict(orient="index"),
        "tramos": tramos.to_dict(orient="records"),
    }
    INFRA_JSON.write_text(json.dumps(infra, ensure_ascii=False, indent=1),
                          encoding="utf-8")
    print(f"✓ Excel -> JSON: {len(services)} servicios, "
          f"{len(estaciones)} estaciones, {len(tramos)} tramos")


def json_to_xlsx() -> None:
    """JSON -> Excel (reconstruye biotren_datos.xlsx)."""
    # Reutiliza el generador completo
    import subprocess
    script = Path(__file__).parent.parent / "gen_excel_db.py"
    if script.exists():
        subprocess.run([sys.executable, str(script)], check=True)
    else:
        print("Para reconstruir el Excel completo con formato, "
              "use el script gen_excel_db.py")


if __name__ == "__main__":
    modo = sys.argv[1] if len(sys.argv) > 1 else ""
    if modo == "xlsx2json":
        xlsx_to_json()
    elif modo == "json2xlsx":
        json_to_xlsx()
    else:
        print(__doc__)
