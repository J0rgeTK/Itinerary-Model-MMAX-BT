"""
Mapa geográfico de la red Biotren.

Dos modos de vista:
1. Folium con basemap (requiere internet — CartoDB Positron)
2. Plotly esquemático (sin dependencia de internet, solo coordenadas)
"""

from __future__ import annotations

import folium
import pandas as pd
import plotly.graph_objects as go
import streamlit as st
from streamlit_folium import st_folium


ROL_STYLE = {
    "hub":              {"color": "#A32D2D", "radius": 11, "label": "Hub crítico"},
    "depósito":         {"color": "#E89B3F", "radius": 10, "label": "Depósito"},
    "terminal":         {"color": "#E07A5F", "radius": 9,  "label": "Terminal"},
    "terminal/taller":  {"color": "#E07A5F", "radius": 9,  "label": "Terminal / taller"},
    "cruce/cochera":    {"color": "#3B9C5C", "radius": 8,  "label": "Cruce / cochera"},
    "cruce/carga":      {"color": "#E89B3F", "radius": 8,  "label": "Cruce / carga"},
    "apeadero/cochera": {"color": "#3B9C5C", "radius": 8,  "label": "Apeadero / cochera"},
    "cruce":            {"color": "#D9B441", "radius": 7,  "label": "Punto de cruce"},
    "apeadero":         {"color": "#7A786E", "radius": 4,  "label": "Apeadero"},
}


def _merge_data(estaciones_df, coords_df):
    df = estaciones_df.copy()
    coords = coords_df.rename(columns={"estacion_csv": "estacion"})
    df = df.merge(coords[["estacion", "lat", "lon"]], on="estacion",
                  how="left", suffixes=("", "_csv"))
    if "lat_csv" in df.columns:
        df["lat"] = df["lat"].fillna(df["lat_csv"])
        df["lon"] = df["lon"].fillna(df["lon_csv"])
    return df


# ---------- FOLIUM ----------

def render_folium_map(estaciones_df, coords_df, tramos_df):
    df = _merge_data(estaciones_df, coords_df)
    center_lat = df["lat"].mean()
    center_lon = df["lon"].mean()
    m = folium.Map(location=[center_lat, center_lon], zoom_start=11,
                   tiles="CartoDB Positron")
    coord_lookup = {r["estacion"]: (r["lat"], r["lon"]) for _, r in df.iterrows()}

    for _, t in tramos_df.iterrows():
        a = coord_lookup.get(t["desde"]); b = coord_lookup.get(t["hasta"])
        if not a or not b or pd.isna(a[0]) or pd.isna(b[0]):
            continue
        color = "#A32D2D" if t["tipo"] == "vía simple" else "#185FA5"
        weight = 5 if t["tipo"] == "vía simple" else 4
        folium.PolyLine([a, b], color=color, weight=weight, opacity=0.75,
                        tooltip=(f"{t['linea'][:2]}: {t['desde']} ↔ {t['hasta']}  ·  "
                                 f"{t['tipo']}  ·  {t['dist_km']} km")).add_to(m)

    for _, r in df.iterrows():
        if pd.isna(r.get("lat")) or pd.isna(r.get("lon")):
            continue
        rol = r.get("rol", "apeadero")
        style = ROL_STYLE.get(rol, ROL_STYLE["apeadero"])
        popup = (f"<b>{r['estacion']}</b><br>"
                 f"Rol: {rol}<br>"
                 f"Vías: {r.get('n_vias','?')}<br>"
                 f"Andenes hab.: {r.get('andenes_hab','?')}<br>"
                 f"Cruzamiento: {'Sí' if r.get('cruzamiento') else 'No'}<br>"
                 f"Cochera: {r.get('cochera_n', 0)} tren(es)<br>"
                 f"<i>{r.get('obs') or ''}</i>")
        folium.CircleMarker(
            location=[r["lat"], r["lon"]],
            radius=style["radius"], color="#222222", weight=0.8,
            fill=True, fill_color=style["color"], fill_opacity=0.85,
            popup=folium.Popup(popup, max_width=300),
            tooltip=r["estacion"],
        ).add_to(m)

    legend_html = """
    <div style="position: fixed; bottom: 20px; left: 20px; z-index: 9999;
                background: white; padding: 10px 14px; border-radius: 6px;
                box-shadow: 0 2px 6px rgba(0,0,0,0.2); font-family: Arial, sans-serif;
                font-size: 12px;">
        <b>Estaciones</b><br>
        <span style="color:#A32D2D;">●</span> Hub crítico &nbsp;
        <span style="color:#E89B3F;">●</span> Depósito &nbsp;
        <span style="color:#E07A5F;">●</span> Terminal<br>
        <span style="color:#D9B441;">●</span> Cruce &nbsp;
        <span style="color:#3B9C5C;">●</span> Cochera &nbsp;
        <span style="color:#7A786E;">●</span> Apeadero<br>
        <hr style="margin:6px 0;"><b>Vía</b><br>
        <span style="color:#185FA5;">━━</span> Doble vía &nbsp;&nbsp;
        <span style="color:#A32D2D;">━━</span> Vía simple
    </div>
    """
    m.get_root().html.add_child(folium.Element(legend_html))
    return m


# ---------- PLOTLY (sin internet) ----------

def render_plotly_map(estaciones_df, coords_df, tramos_df):
    df = _merge_data(estaciones_df, coords_df)
    coord_lookup = {r["estacion"]: (r["lat"], r["lon"]) for _, r in df.iterrows()}

    fig = go.Figure()
    for tipo, group in tramos_df.groupby("tipo"):
        color = "#A32D2D" if tipo == "vía simple" else "#185FA5"
        width = 5 if tipo == "vía simple" else 3
        x_pts = []; y_pts = []
        for _, t in group.iterrows():
            a = coord_lookup.get(t["desde"]); b = coord_lookup.get(t["hasta"])
            if not a or not b or pd.isna(a[0]) or pd.isna(b[0]):
                continue
            x_pts += [a[1], b[1], None]
            y_pts += [a[0], b[0], None]
        fig.add_trace(go.Scatter(
            x=x_pts, y=y_pts, mode="lines",
            line=dict(color=color, width=width),
            name=f"Tramo {tipo}",
            hoverinfo="skip",
        ))

    for rol, style in ROL_STYLE.items():
        sub = df[df.get("rol", pd.Series(dtype=str)) == rol]
        if sub.empty:
            continue
        custom = sub[["rol", "n_vias", "andenes_hab", "cruzamiento", "cochera_n", "obs"]].astype(str).values
        fig.add_trace(go.Scatter(
            x=sub["lon"], y=sub["lat"], mode="markers+text",
            marker=dict(size=style["radius"] * 1.5, color=style["color"],
                        line=dict(color="#222", width=0.8)),
            text=sub["estacion"], textposition="top right",
            textfont=dict(size=9, color="#333"),
            name=style["label"],
            customdata=custom,
            hovertemplate=(
                "<b>%{text}</b><br>"
                "Rol: %{customdata[0]}<br>"
                "Vías: %{customdata[1]} · Andenes: %{customdata[2]}<br>"
                "Cruzamiento: %{customdata[3]} · Cochera: %{customdata[4]}<br>"
                "<i>%{customdata[5]}</i><extra></extra>"
            ),
        ))

    fig.update_xaxes(title="Longitud", showgrid=True, gridcolor="#EEEEEE", zeroline=False)
    fig.update_yaxes(title="Latitud", showgrid=True, gridcolor="#EEEEEE",
                     scaleanchor="x", scaleratio=1, zeroline=False)
    fig.update_layout(
        height=700, margin=dict(l=40, r=40, t=20, b=40),
        plot_bgcolor="white",
        legend=dict(orientation="v", y=1, x=1.01),
    )
    return fig


def show_map(estaciones_df, coords_df, tramos_df):
    modo = st.radio(
        "Tipo de mapa",
        ["Geográfico (Folium, requiere internet)",
         "Esquemático (Plotly, sin internet)"],
        horizontal=True, key="map_mode",
    )
    if modo.startswith("Geográfico"):
        m = render_folium_map(estaciones_df, coords_df, tramos_df)
        st_folium(m, height=620, use_container_width=True, returned_objects=[])
    else:
        fig = render_plotly_map(estaciones_df, coords_df, tramos_df)
        st.plotly_chart(fig, use_container_width=True, key="map_plotly")
