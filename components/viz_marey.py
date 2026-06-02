"""
Diagrama de Marey (distancia–tiempo) para Biotren.

Cada servicio es una línea entre puntos (tiempo, distancia acumulada).
Detecta automáticamente conflictos potenciales en tramos de vía simple.
"""

from __future__ import annotations

import pandas as pd
import plotly.graph_objects as go
import streamlit as st


# Mapping of stations to cumulative km, by line
# (these match the keys in services_full.json)
DIST_L1 = {
    "Mercado": 0.0, "Arenal": 1.52, "Higueras": 3.50, "Los Cóndores": 5.65,
    "UTF Santa María": 8.72, "Lorenzo Arenas": 11.74, "CONCEPCIÓN": 14.32,
    "Chiguayante": 24.22, "Pedro Medina": 25.55, "Manquimávida": 27.24,
    "La Leonera": 28.65, "Omer Huet": 30.5, "Hualqui": 35.83, "Quilacoya": 44.29,
    "San Miguel": 47.01, "Unihue": 51.71, "Valle Chanco": 53.89, "Los Acacios": 56.44,
    "Talcamávida": 57.78, "Gomero": 65.02, "Buenuraqui": 70.84, "San Rosendo": 78.59,
    "Laja": 80.81,
}
DIST_L2 = {
    "CONCEPCIÓN": 0.0, "Juan Pablo II": 3.4, "Diagonal Bio Bio": 5.2, "Alborada": 6.7,
    "Costa Mar": 7.66, "El Parque": 9.15, "Lomas Coloradas": 10.87,
    "CRS Henríquez": 12.59, "Hito Galvarino": 18.89, "Los Canelos": 21.86,
    "Huinca": 22.74, "Cristo Redentor": 23.89, "Laguna Quiñenco": 25.51, "CORONEL": 27.0,
}

# Tramos de vía simple por línea — para detección de conflictos visuales
SINGLE_TRACK_L1 = [
    ("Mercado", "Arenal"),
    ("La Leonera", "Omer Huet"), ("Omer Huet", "Hualqui"),
    ("Hualqui", "Quilacoya"), ("Quilacoya", "San Miguel"), ("San Miguel", "Unihue"),
    ("Unihue", "Valle Chanco"), ("Valle Chanco", "Los Acacios"),
    ("Los Acacios", "Talcamávida"), ("Talcamávida", "Gomero"), ("Gomero", "Buenuraqui"),
    ("Buenuraqui", "San Rosendo"), ("San Rosendo", "Laja"),
]
SINGLE_TRACK_L2: list[tuple[str, str]] = []  # L2 es doble vía completa

# Estaciones donde NO se puede cruzar (regla operacional precisada):
#  - Mercado
#  - Tramo Quilacoya–San Rosendo: estaciones intermedias sin desvío
CRUZAMIENTO_PROHIBIDO = {
    "Mercado",
    "San Miguel", "Unihue", "Valle Chanco", "Los Acacios",
    "Talcamávida", "Gomero", "Buenuraqui",
}
# Arenal: cruzamiento condicional (posible salvo cruce de servicios
# que se dirigen a Mercado mientras otro sale de Mercado)
CRUZAMIENTO_CONDICIONAL = {"Arenal"}

# Color por tipo
COLOR_BY_TIPO = {
    "BT PM": "#1F4E78", "BT PM BUCLE": "#1F4E78",
    "BT VALLE": "#5B9BD5",
    "BT PT": "#C0392B", "BT PT BUCLE": "#C0392B",
    "EQUIPO VACÍO": "#A0A0A0",
    "LJ-TH": "#7030A0", "ESPECIAL TL": "#7030A0",
}
COLOR_BY_SENTIDO = {
    "CC→CW": "#1F4E78", "CW→CC": "#C0392B",
    "LJ→TH": "#1F4E78", "TH→LJ": "#C0392B",
}


def render_marey(
    passes_df: pd.DataFrame,
    linea: str,
    sentidos: list[str],
    color_dim: str = "sentido",
    t_min_h: float = 5.0,
    t_max_h: float = 24.0,
    highlight_train: str | None = None,
    show_single_track: bool = True,
    mostrar_carga: bool = True,
    dia_sel: str | None = None,
    hora_actual: float | None = None,
) -> go.Figure:
    """Build the Marey chart for the given line and time window."""

    df = passes_df[passes_df["linea"] == linea].copy()
    if sentidos:
        df = df[df["sentido"].isin(sentidos)]
    # Filtro de canales de carga (toggle "Visualizar CDC" en la UI)
    if not mostrar_carga and "es_carga" in df.columns:
        df = df[~df["es_carga"].fillna(False)]
    # Filtro por día seleccionado (LJ / V / SAB / DOM)
    if dia_sel and "dia" in df.columns:
        # Reusar la lógica de aplica_en_dia
        from components.viz_gantt import aplica_en_dia
        # services únicos en el df
        ids_validos = set()
        for sid, grupo in df.groupby("service_id"):
            srow = grupo.iloc[0]
            if aplica_en_dia(srow, dia_sel):
                ids_validos.add(sid)
        df = df[df["service_id"].isin(ids_validos)]

    # Y axis: stations ordered by their cumulative distance
    dist_map = DIST_L1 if linea == "L1" else DIST_L2
    stations_ordered = sorted(dist_map.items(), key=lambda kv: kv[1])
    y_max = max(d for _, d in stations_ordered) + 1
    y_min = -1

    fig = go.Figure()

    # Background: tramos de vía simple
    if show_single_track and linea == "L1":
        for a, b in SINGLE_TRACK_L1:
            ya = dist_map.get(a)
            yb = dist_map.get(b)
            if ya is None or yb is None:
                continue
            y0, y1 = sorted([ya, yb])
            # Zona de vía simple SIN posibilidad de cruzamiento (Quilacoya–San Rosendo)
            # se resalta con un rojo más intenso; el resto de vía simple, suave.
            sin_cruce = (a in CRUZAMIENTO_PROHIBIDO and b in CRUZAMIENTO_PROHIBIDO) or \
                        (a == "Quilacoya" and b in CRUZAMIENTO_PROHIBIDO) or \
                        (b == "San Rosendo" and a in CRUZAMIENTO_PROHIBIDO)
            fillcolor = "rgba(220,40,40,0.13)" if sin_cruce else "rgba(255,120,80,0.05)"
            fig.add_hrect(y0=y0, y1=y1, fillcolor=fillcolor,
                          line_width=0, layer="below")

    # Drop services outside time window
    t_min_s = t_min_h * 3600
    t_max_s = t_max_h * 3600
    df = df[df["sale"].between(t_min_s, t_max_s) | df["llega"].between(t_min_s, t_max_s)]

    # Iterate by service
    for service_id, grp in df.groupby("service_id"):
        # IMPORTANTE: ordenar por la secuencia real del recorrido ("orden"),
        # no por distancia. Para servicios sur→norte (CW→CC, TH→LJ) el orden
        # de recorrido es inverso a la distancia acumulada; ordenar por "orden"
        # garantiza que cada estación aporte primero su LLEGADA y luego su
        # SALIDA en la secuencia temporal correcta del viaje.
        if "orden" in grp.columns:
            grp = grp.sort_values("orden")
        else:
            grp = grp.sort_values("dist_km")
        # Build x (datetime) and y (km) arrays following the trip sequence:
        # en cada estación, primero la llegada y después la salida.
        x_vals = []
        y_vals = []
        for _, row in grp.iterrows():
            if pd.notna(row["llega_dt"]):
                x_vals.append(row["llega_dt"])
                y_vals.append(row["dist_km"])
            if pd.notna(row["sale_dt"]) and (not x_vals or row["sale_dt"] != x_vals[-1]):
                x_vals.append(row["sale_dt"])
                y_vals.append(row["dist_km"])
        if len(x_vals) < 2:
            continue

        sample = grp.iloc[0]
        sentido = sample["sentido"]
        tipo = sample["tipo"]
        tren = sample["tren"]

        # Canal de carga: estilo propio (línea punteada color tierra),
        # independiente del color_dim, para distinguirlo siempre.
        es_carga = "CARGA" in str(tipo).upper()
        if es_carga:
            color = "#8C5A2B"
            dash = "dot"
        else:
            dash = "solid"
            if color_dim == "tipo":
                color = COLOR_BY_TIPO.get(tipo, "#888888")
            elif color_dim == "tren":
                # Deterministic color from train name
                h = abs(hash(tren)) % 360
                color = f"hsl({h},60%,45%)"
            else:
                color = COLOR_BY_SENTIDO.get(sentido, "#888888")

        width = 3 if highlight_train and tren == highlight_train else (
            1.6 if es_carga else 1.4
        )
        opacity = 1.0 if highlight_train and tren == highlight_train else (
            0.35 if highlight_train else (0.7 if es_carga else 0.85)
        )

        # Custom data for hover — agrego código, número y aplicabilidad
        etiqueta = "Canal de carga" if es_carga else "Servicio Biotren"
        codigo = grupo["codigo_servicio"].iloc[0] if "codigo_servicio" in grupo.columns else ""
        numero = grupo["numero_servicio"].iloc[0] if "numero_servicio" in grupo.columns else ""
        apl = grupo["aplicabilidad"].iloc[0] if "aplicabilidad" in grupo.columns else ""
        # Etiqueta principal: usar código si existe, si no service_id
        etiqueta_principal = codigo if codigo and str(codigo) not in ("", "nan") else service_id
        custom = [[etiqueta_principal, tren, tipo, sentido, etiqueta,
                   numero or "—", apl or "—"] for _ in x_vals]

        fig.add_trace(go.Scatter(
            x=x_vals, y=y_vals,
            mode="lines+markers",
            line=dict(color=color, width=width, dash=dash),
            marker=dict(size=3 if es_carga else 4, color=color),
            opacity=opacity,
            name=f"{etiqueta_principal} ({tren})",
            customdata=custom,
            hovertemplate=(
                "<b>%{customdata[0]}</b> · %{customdata[4]}<br>"
                "Tren: %{customdata[1]}<br>"
                "Tipo: %{customdata[2]}<br>"
                "Sentido: %{customdata[3]}<br>"
                "N° servicio: %{customdata[5]}<br>"
                "Aplicabilidad: %{customdata[6]}<br>"
                "Hora: %{x|%H:%M}<br>"
                "Km: %{y:.2f}<extra></extra>"
            ),
            showlegend=False,
        ))

    # Y axis with station names
    # L2: Concepción (km 0) arriba → Coronel abajo  → range = [y_max, y_min]
    # L1: Laja (km máx) arriba → Mercado (km 0) abajo → range = [y_min, y_max]
    if linea == "L1":
        y_range = [y_min, y_max]
        y_title = "Estación (Laja → Talcahuano ↓)"
    else:
        y_range = [y_max, y_min]
        y_title = "Estación (Concepción → Coronel ↓)"
    fig.update_yaxes(
        tickmode="array",
        tickvals=[d for _, d in stations_ordered],
        ticktext=[n for n, _ in stations_ordered],
        range=y_range,
        title=y_title,
        gridcolor="#EEEEEE",
    )
    fig.update_xaxes(
        title="Tiempo del día",
        tickformat="%H:%M",
        dtick=30 * 60 * 1000,  # cada 30 min
        gridcolor="#EEEEEE",
    )

    # Leyenda: trazas "proxy" invisibles solo para mostrar la simbología.
    # Indica los sentidos y, si hay canales de carga en la vista, su estilo.
    hay_carga = ("es_carga" in df.columns
                 and bool(df["es_carga"].fillna(False).any()))
    leyenda = []
    if linea == "L2":
        leyenda = [("CC → CW", COLOR_BY_SENTIDO["CC→CW"], "solid"),
                   ("CW → CC", COLOR_BY_SENTIDO["CW→CC"], "solid")]
    else:
        leyenda = [("LJ → TH", COLOR_BY_SENTIDO["LJ→TH"], "solid"),
                   ("TH → LJ", COLOR_BY_SENTIDO["TH→LJ"], "solid")]
    if hay_carga:
        leyenda.append(("Canal de carga", "#8C5A2B", "dot"))
    for nombre, color, dash in leyenda:
        fig.add_trace(go.Scatter(
            x=[None], y=[None], mode="lines",
            line=dict(color=color, width=2, dash=dash),
            name=nombre, showlegend=True,
        ))

    fig.update_layout(
        height=720, hovermode="closest",
        margin=dict(l=140, r=20, t=40, b=40),
        plot_bgcolor="white",
        legend=dict(orientation="h", yanchor="bottom", y=1.01,
                    xanchor="right", x=1, title_text=""),
        title=f"Diagrama de Marey — {linea}  ·  ventana {int(t_min_h):02d}:{int(t_min_h%1*60):02d}–{int(t_max_h):02d}:{int(t_max_h%1*60):02d}",
    )

    # Línea vertical de "hora actual" (cursor temporal)
    if hora_actual is not None and t_min_h <= hora_actual <= t_max_h:
        from datetime import datetime, timedelta
        h = float(hora_actual)
        x_hora = datetime(2026, 4, 20) + timedelta(hours=h)
        # Usamos add_shape + add_annotation por separado para evitar bug
        # de plotly que calcula media de timestamps internamente
        fig.add_shape(
            type="line", x0=x_hora, x1=x_hora, xref="x",
            y0=0, y1=1, yref="paper",
            line=dict(color="#E63946", width=2, dash="solid"),
        )
        fig.add_annotation(
            x=x_hora, xref="x", y=1.0, yref="paper",
            text=f"  {int(h):02d}:{int(h%1*60):02d}",
            showarrow=False, font=dict(size=12, color="#E63946"),
            xanchor="left", yanchor="bottom",
        )

    return fig
