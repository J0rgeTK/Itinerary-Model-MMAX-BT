"""
Dashboard de indicadores operacionales.

Distingue servicios de PASAJEROS (comerciales) de servicios NO COMERCIALES
(equipos vacíos y movimientos de mantenimiento).
"""

from __future__ import annotations

import pandas as pd
import plotly.express as px
import streamlit as st


# Tipos que NO son servicio de pasajeros
TIPOS_NO_COMERCIAL_KEYS = ["VACÍO", "VACIO", "MANTENIMIENTO", "MANTENCIÓN", "MANTENCION"]


def es_carga(tipo: str) -> bool:
    """True si el servicio es un canal de carga (operador externo)."""
    return "CARGA" in (tipo or "").upper()


def es_pasajero(tipo: str) -> bool:
    """True si el servicio transporta pasajeros.

    Excluye canales de carga, equipos vacíos y movimientos de mantenimiento.
    """
    t = (tipo or "").upper()
    if es_carga(t):
        return False
    return not any(k in t for k in TIPOS_NO_COMERCIAL_KEYS)


def clasifica_servicio(tipo: str) -> str:
    """Devuelve 'Pasajeros', 'Carga' o 'No comercial'."""
    if es_carga(tipo):
        return "Carga"
    return "Pasajeros" if es_pasajero(tipo) else "No comercial"


def render_kpis(services_df: pd.DataFrame, passes_df: pd.DataFrame,
                 dia_sel: str = "LJ",
                 key_prefix: str = "") -> None:
    """KPI cards and distribution charts, distinguishing passenger services.

    Los canales de carga se excluyen por completo. Si dia_sel se entrega,
    se filtran además los servicios según el día (LJ / V / SAB / DOM).
    """
    # Excluir canales de carga
    df = services_df[~services_df["tipo"].apply(es_carga)].copy()

    # Filtro por día (si los datos lo soportan)
    if "dia" in df.columns and dia_sel:
        # Importación tardía para evitar ciclo
        from components.viz_gantt import aplica_en_dia
        mask = df.apply(lambda r: aplica_en_dia(r, dia_sel), axis=1)
        df = df[mask]

    if df.empty:
        st.info(f"No hay servicios para el día '{dia_sel}' en este itinerario.")
        return

    df["clase"] = df["tipo"].apply(clasifica_servicio)

    pax = df[df["clase"] == "Pasajeros"]
    nc = df[df["clase"] == "No comercial"]

    # ----- Tarjetas KPI -----
    n_total = len(df)
    n_pax = len(pax)
    n_nc = len(nc)
    n_trains = df["tren"].nunique()

    # Pico simultáneo (solo considerando servicios de pasajeros)
    def pico(d: pd.DataFrame) -> int:
        events = []
        for _, s in d.iterrows():
            if pd.notna(s["h_salida"]) and pd.notna(s["h_llegada"]):
                events.append((s["h_salida"], 1))
                events.append((s["h_llegada"], -1))
        events.sort()
        mx = cur = 0
        for _, dd in events:
            cur += dd
            mx = max(mx, cur)
        return mx

    st.markdown("##### Indicadores generales")
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Servicios de pasajeros", n_pax,
              help="Servicios comerciales: excluye equipos vacíos y mantenimiento")
    c2.metric("Servicios totales", n_total,
              help="Incluye equipos vacíos y movimientos de mantenimiento")
    c3.metric("No comerciales", n_nc,
              help="Equipos vacíos + mantenimiento")
    c4.metric("Trenes utilizados", n_trains)

    c5, c6, c7, c8 = st.columns(4)
    pax_l1 = len(pax[pax["linea"] == "L1"])
    pax_l2 = len(pax[pax["linea"] == "L2"])
    c5.metric("Pasajeros L1 / L2", f"{pax_l1} / {pax_l2}")
    c6.metric("Pico simultáneo (pasajeros)", pico(pax))
    c7.metric("Duración media (pasajeros)",
              f"{pax['duracion_min'].mean():.1f} min" if not pax.empty else "—")
    total_pax_h = pax["duracion_min"].sum() / 60
    c8.metric("Horas-servicio pasajeros", f"{total_pax_h:.1f} h")

    # Headways (solo pasajeros)
    c9, c10, c11, c12 = st.columns(4)
    def headway(d: pd.DataFrame, linea: str, sentido: str) -> str:
        sub = d[(d["linea"] == linea) & (d["sentido"] == sentido) &
                (d["h_salida"].between(6.5 * 3600, 8.5 * 3600))]
        if len(sub) > 1:
            f = (sub["h_salida"].max() - sub["h_salida"].min()) / 60 / (len(sub) - 1)
            return f"{f:.1f} min"
        return "—"
    c9.metric("Headway L2 CC→CW (PM)", headway(pax, "L2", "CC→CW"))
    c10.metric("Headway L2 CW→CC (PM)", headway(pax, "L2", "CW→CC"))
    c11.metric("Headway L1 LJ→TH (PM)", headway(pax, "L1", "LJ→TH"))
    c12.metric("Headway L1 TH→LJ (PM)", headway(pax, "L1", "TH→LJ"))

    st.markdown("---")

    # ----- Distribución por franja: pasajeros por línea + no comercial -----
    col_left, col_right = st.columns(2)

    with col_left:
        st.markdown("##### Servicios por franja horaria")
        st.caption("Pasajeros desglosados por línea; equipos vacíos y "
                   "mantenimiento agrupados aparte.")
        d = df.copy()
        d["hora"] = d["h_salida"] / 3600
        d["franja"] = pd.cut(
            d["hora"], bins=[0, 9, 14, 21, 24],
            labels=["Punta Mañana", "Valle", "Punta Tarde", "Noche"],
            include_lowest=True,
        )
        # Categoría combinada: "Pasajeros L1", "Pasajeros L2", "No comercial"
        d["categoria"] = d.apply(
            lambda r: f"Pasajeros {r['linea']}" if r["clase"] == "Pasajeros"
            else "No comercial (EV / mant.)", axis=1,
        )
        cnt = d.groupby(["franja", "categoria"], observed=True).size().reset_index(name="n")
        fig = px.bar(
            cnt, x="franja", y="n", color="categoria", barmode="stack",
            color_discrete_map={
                "Pasajeros L1": "#7030A0",
                "Pasajeros L2": "#1F4E78",
                "No comercial (EV / mant.)": "#BFBFBF",
            },
            labels={"n": "Servicios", "franja": "", "categoria": ""},
        )
        fig.update_layout(height=320, margin=dict(l=20, r=20, t=10, b=20),
                          plot_bgcolor="white", legend_title_text="",
                          legend=dict(orientation="h", y=-0.2))
        fig.update_yaxes(gridcolor="#EEEEEE")
        st.plotly_chart(fig, use_container_width=True, key=f"{key_prefix}_kpi_dist_hora")

    with col_right:
        st.markdown("##### Servicios por tren asignado")
        st.caption("Cada barra desglosa los servicios de pasajeros por línea "
                   "y los movimientos no comerciales.")
        d2 = df.copy()
        d2["categoria"] = d2.apply(
            lambda r: f"Pasajeros {r['linea']}" if r["clase"] == "Pasajeros"
            else "No comercial (EV / mant.)", axis=1,
        )
        tc = d2.groupby(["tren", "categoria"]).size().reset_index(name="n")

        def keyfn(t):
            s = str(t).replace("SFE", "").strip()
            try:
                return (0, int(s))
            except ValueError:
                return (1, s)
        orden = sorted(d2["tren"].unique(), key=keyfn)
        tc["tren"] = pd.Categorical(tc["tren"], categories=orden, ordered=True)
        tc = tc.sort_values("tren")

        fig = px.bar(
            tc, x="tren", y="n", color="categoria", barmode="stack",
            color_discrete_map={
                "Pasajeros L1": "#7030A0",
                "Pasajeros L2": "#1F4E78",
                "No comercial (EV / mant.)": "#BFBFBF",
            },
            labels={"n": "Servicios", "tren": "", "categoria": ""},
        )
        fig.update_layout(height=320, margin=dict(l=20, r=20, t=10, b=20),
                          plot_bgcolor="white", legend_title_text="",
                          legend=dict(orientation="h", y=-0.25))
        fig.update_yaxes(gridcolor="#EEEEEE")
        st.plotly_chart(fig, use_container_width=True, key=f"{key_prefix}_kpi_servicios_tren")

    # ----- Mapa de calor: solo servicios de pasajeros -----
    st.markdown("##### Mapa de calor — servicios de pasajeros por hora y sentido")
    bins = list(range(5, 25))
    rows = []
    for linea, dl in pax.groupby("linea"):
        for sentido, ds in dl.groupby("sentido"):
            for h0, h1 in zip(bins[:-1], bins[1:]):
                mask = (ds["h_salida"] >= h0 * 3600) & (ds["h_salida"] < h1 * 3600)
                rows.append({
                    "linea_sentido": f"{linea} {sentido}",
                    "hora": f"{h0:02d}:00",
                    "n": int(mask.sum()),
                })
    hm = pd.DataFrame(rows)
    if not hm.empty:
        pivot = hm.pivot(index="linea_sentido", columns="hora", values="n").fillna(0)
        fig = px.imshow(pivot, color_continuous_scale="Blues", aspect="auto",
                        labels=dict(color="Servicios"))
        fig.update_layout(height=260, margin=dict(l=20, r=20, t=10, b=30),
                          coloraxis_colorbar_title_text="N°")
        st.plotly_chart(fig, use_container_width=True, key=f"{key_prefix}_kpi_heatmap_pax")

    # ----- Composición no comercial -----
    if not nc.empty:
        with st.expander("Detalle de servicios no comerciales"):
            comp = nc.groupby(["tipo", "linea"]).size().reset_index(name="n")
            st.dataframe(
                comp.rename(columns={"tipo": "Tipo", "linea": "Línea",
                                     "n": "Cantidad"}),
                use_container_width=True, hide_index=True,
            )
            st.caption(f"Total de movimientos no comerciales: {len(nc)} "
                       f"({len(nc)/len(df)*100:.1f}% del total de servicios).")
