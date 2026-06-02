"""
Cargador de datos para Biotren Itinerario Studio.

FUENTE PRIMARIA: data/biotren_datos.xlsx
    Hojas: Servicios, Horarios, Estaciones, Tramos

El libro Excel es editable directamente por el usuario; al reiniciar la app
los cambios se reflejan automáticamente.

Si el archivo Excel no existe, intenta usar los JSON/CSV heredados como respaldo.
"""

from __future__ import annotations

import datetime as dt
import json
from pathlib import Path
from typing import Any

import pandas as pd
import streamlit as st


DATA_DIR = Path(__file__).parent / "data"
EXCEL_DB = DATA_DIR / "biotren_datos.xlsx"           # propuesta
EXCEL_VIGENTE = DATA_DIR / "biotren_vigente.xlsx"    # itinerario vigente (circular GOF)


def _excel_for(escenario: str) -> Path:
    """Devuelve la ruta del Excel según el escenario seleccionado."""
    return EXCEL_VIGENTE if escenario == "vigente" else EXCEL_DB


# ---------------------------------------------------------------------------
# Utilidades de conversión de tiempo
# ---------------------------------------------------------------------------

def _to_secs(v: Any) -> int | None:
    """Convierte un valor de hora (time, datetime, str 'HH:MM', número) a segundos."""
    if v is None or (isinstance(v, float) and pd.isna(v)):
        return None
    if isinstance(v, dt.time):
        return v.hour * 3600 + v.minute * 60 + v.second
    if isinstance(v, dt.datetime):
        return v.hour * 3600 + v.minute * 60 + v.second
    if isinstance(v, (int, float)):
        # Excel puede entregar la hora como fracción de día (0..1)
        if 0 <= v < 2:
            return int(round(v * 86400))
        return int(v)
    if isinstance(v, str):
        s = v.strip()
        if not s:
            return None
        try:
            parts = s.split(":")
            if len(parts) >= 2:
                h, m = int(parts[0]), int(parts[1])
                sec = int(parts[2]) if len(parts) > 2 else 0
                return h * 3600 + m * 60 + sec
        except ValueError:
            return None
    return None


def _secs_to_hhmm(s: int | float | None) -> str:
    if s is None or pd.isna(s):
        return ""
    s = int(s)
    return f"{s // 3600:02d}:{(s % 3600) // 60:02d}"


def _secs_to_datetime(s: int | float | None, base_date: str = "2026-04-20"):
    if s is None or pd.isna(s):
        return pd.NaT
    return pd.Timestamp(base_date) + pd.Timedelta(seconds=int(s))


# ---------------------------------------------------------------------------
# Carga desde Excel
# ---------------------------------------------------------------------------

@st.cache_data(show_spinner="Cargando itinerario…")
def load_services(escenario: str = "propuesta"
                  ) -> tuple[pd.DataFrame, pd.DataFrame, dict[str, float], dict[str, float]]:
    """Carga servicios y horarios desde el Excel del escenario indicado.

    escenario: "vigente" (circular GOF) o "propuesta" (con canales de carga).

    Returns:
        services_df : un registro por servicio
        passes_df   : un registro por paso (servicio x estacion) - formato largo
        dist_l1, dist_l2 : mapas estacion -> km acumulado por linea
    """
    excel = _excel_for(escenario)
    servicios = pd.read_excel(excel, sheet_name="Servicios")
    horarios = pd.read_excel(excel, sheet_name="Horarios")

    # Columnas opcionales (pueden faltar en archivos antiguos)
    for col, default in [("codigo_servicio", ""), ("numero_servicio", ""),
                         ("aplicabilidad", ""), ("dia", "LV")]:
        if col not in servicios.columns:
            servicios[col] = default
    servicios["codigo_servicio"] = servicios["codigo_servicio"].fillna("").astype(str)
    servicios["numero_servicio"] = servicios["numero_servicio"].fillna("").astype(str)
    servicios["aplicabilidad"] = servicios["aplicabilidad"].fillna("").astype(str)
    servicios["dia"] = servicios["dia"].fillna("LV").astype(str)

    # --- Servicios ---
    servicios["h_salida"] = servicios["h_salida"].apply(_to_secs)
    servicios["h_llegada"] = servicios["h_llegada"].apply(_to_secs)
    servicios["tipo"] = servicios["tipo"].fillna("")

    # Marca de canal de carga (operador externo, p. ej. TRANSAP).
    # Se usa para incluirlos en el Marey pero excluirlos de indicadores,
    # turnos, catálogo y rotación de flota Biotren.
    servicios["es_carga"] = servicios["tipo"].str.upper().str.contains("CARGA")

    # Desenrollar cruces de medianoche: si la llegada es anterior a la salida,
    # el servicio cruzó las 00:00 → se le suma 24 h para que los tiempos sean
    # monótonos (necesario para el diagrama de Marey y la duración correcta).
    cruza = servicios["h_llegada"] < servicios["h_salida"]
    servicios.loc[cruza, "h_llegada"] = servicios.loc[cruza, "h_llegada"] + 86400
    ids_cruzan = set(servicios.loc[cruza, "id"])

    servicios["h_salida_str"] = servicios["h_salida"].apply(_secs_to_hhmm)
    servicios["h_llegada_str"] = servicios["h_llegada"].apply(_secs_to_hhmm)
    servicios["h_salida_dt"] = servicios["h_salida"].apply(_secs_to_datetime)
    servicios["h_llegada_dt"] = servicios["h_llegada"].apply(_secs_to_datetime)
    if "duracion_min" not in servicios.columns or servicios["duracion_min"].isna().all():
        servicios["duracion_min"] = (
            (servicios["h_llegada"] - servicios["h_salida"]) / 60
        ).round(1)

    # --- Horarios (pasadas) ---
    horarios["llega"] = horarios["llega"].apply(_to_secs)
    horarios["sale"] = horarios["sale"].apply(_to_secs)
    horarios = horarios.sort_values(["service_id", "orden"])

    # Desenrollar medianoche también en las pasadas de los servicios afectados:
    # cualquier tiempo anterior a la primera salida del servicio se +24 h.
    if ids_cruzan:
        salidas = servicios.set_index("id")["h_salida"]
        for sid in ids_cruzan:
            h0_base = salidas.get(sid)
            # h0_base ya pudo desenrollarse; usar la salida original (mín del grupo)
            mask = horarios["service_id"] == sid
            sub = horarios.loc[mask]
            if sub.empty:
                continue
            h_ini = sub[["llega", "sale"]].min().min()
            for col in ("llega", "sale"):
                vals = horarios.loc[mask, col]
                horarios.loc[mask, col] = vals.where(
                    vals.isna() | (vals >= h_ini), vals + 86400
                )

    # Enriquecer horarios con metadatos del servicio
    meta = servicios.set_index("id")[["linea", "sentido", "tren", "tipo",
                                       "es_carga", "dia", "aplicabilidad",
                                       "codigo_servicio", "numero_servicio"]]
    passes = horarios.merge(meta, left_on="service_id", right_index=True, how="left")
    passes = passes.rename(columns={"estacion": "station"})
    passes["llega_dt"] = passes["llega"].apply(_secs_to_datetime)
    passes["sale_dt"] = passes["sale"].apply(_secs_to_datetime)

    # Mapas de distancia por línea (desde los horarios)
    dist_l1: dict[str, float] = {}
    dist_l2: dict[str, float] = {}
    for _, row in passes.iterrows():
        target = dist_l1 if row["linea"] == "L1" else dist_l2
        if row["station"] not in target and pd.notna(row["dist_km"]):
            target[row["station"]] = float(row["dist_km"])

    return servicios, passes, dist_l1, dist_l2


@st.cache_data(show_spinner=False)
def load_infrastructure() -> tuple[pd.DataFrame, pd.DataFrame, dict[str, Any]]:
    """Carga estaciones y tramos desde biotren_datos.xlsx."""
    estaciones = pd.read_excel(EXCEL_DB, sheet_name="Estaciones")
    tramos = pd.read_excel(EXCEL_DB, sheet_name="Tramos")

    # Normalizar nombres de columnas esperadas
    estaciones = estaciones.rename(columns={"estacion": "estacion"})
    # 'nombre' es alias de 'estacion' (compatibilidad)
    estaciones["nombre"] = estaciones["estacion"]

    raw = {
        "estaciones": estaciones.set_index("estacion").to_dict(orient="index"),
        "tramos": tramos.to_dict(orient="records"),
    }
    return estaciones, tramos, raw


@st.cache_data(show_spinner=False)
def load_coordinates() -> pd.DataFrame:
    """Coordenadas de estaciones — derivadas de la hoja Estaciones del Excel."""
    estaciones = pd.read_excel(EXCEL_DB, sheet_name="Estaciones")
    df = estaciones.rename(columns={"estacion": "estacion_csv"})
    cols = ["estacion_csv", "comuna", "lat", "lon"]
    cols = [c for c in cols if c in df.columns]
    return df[cols].drop_duplicates(subset=["estacion_csv"]).reset_index(drop=True)


# ---------------------------------------------------------------------------
# Clasificación de franjas horarias
# ---------------------------------------------------------------------------

def franja_de_hora(h_segs: int | float | None) -> str:
    if h_segs is None or pd.isna(h_segs):
        return "?"
    h = h_segs / 3600
    if h < 9:
        return "Punta Mañana"
    if h < 14:
        return "Valle"
    if h < 21:
        return "Punta Tarde"
    return "Noche"
