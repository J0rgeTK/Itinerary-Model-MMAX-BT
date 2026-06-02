"""Vista tabular del itinerario con búsqueda full-text y filtros avanzados."""

from __future__ import annotations

import pandas as pd
import streamlit as st


def render_services_table(services_df: pd.DataFrame) -> None:
    df_full = services_df.copy()
    incluir_carga = False
    df = df_full.copy()
    if "tipo" in df.columns:
        df = df[~df["tipo"].str.upper().str.contains("CARGA", na=False)]

    # ===== Búsqueda full-text =====
    c_busq, c_carga = st.columns([3, 1])
    with c_busq:
        busqueda = st.text_input(
            "🔍 Búsqueda full-text",
            placeholder="Busca por id, código (CC/4), número (20281), tren, origen, destino, tipo, aplicabilidad…",
            key="cat_busqueda",
        )
    with c_carga:
        incluir_carga = st.checkbox(
            "Incluir canales de carga", value=False, key="cat_carga",
        )

    if incluir_carga:
        df = df_full.copy()

    # ===== Filtros avanzados =====
    with st.expander("⚙️ Filtros avanzados", expanded=False):
        c1, c2, c3 = st.columns(3)
        with c1:
            lineas = st.multiselect(
                "Línea", sorted(df["linea"].dropna().unique()),
                default=sorted(df["linea"].dropna().unique()), key="cat_lineas")
            sentidos = st.multiselect(
                "Sentido", sorted(df["sentido"].dropna().unique()),
                default=sorted(df["sentido"].dropna().unique()), key="cat_sentidos")
        with c2:
            tipos = st.multiselect(
                "Tipo de servicio", sorted(df["tipo"].dropna().unique()),
                default=[], key="cat_tipos", help="Vacío = todos")
            trenes = st.multiselect(
                "Tren", sorted(df["tren"].dropna().unique()),
                default=[], key="cat_trenes", help="Vacío = todos")
        with c3:
            dia_disp = (sorted(df["dia"].dropna().unique())
                        if "dia" in df.columns else [])
            dias = st.multiselect("Día", dia_disp, default=dia_disp, key="cat_dias")
            ventana = st.slider(
                "Ventana horaria (salida)", min_value=0.0, max_value=27.0,
                value=(0.0, 27.0), step=0.5, format="%.1f h", key="cat_ventana")

    # ===== Aplicar filtros =====
    if lineas: df = df[df["linea"].isin(lineas)]
    if sentidos: df = df[df["sentido"].isin(sentidos)]
    if tipos: df = df[df["tipo"].isin(tipos)]
    if trenes: df = df[df["tren"].isin(trenes)]
    if "dia" in df.columns and dias: df = df[df["dia"].isin(dias)]
    if "h_salida" in df.columns:
        df = df[(df["h_salida"].fillna(-1) >= ventana[0] * 3600) &
                (df["h_salida"].fillna(99999999) <= ventana[1] * 3600)]

    # ===== Búsqueda full-text =====
    if busqueda:
        q = busqueda.lower().strip()
        cols_s = ["id", "linea", "sentido", "tren", "tipo", "origen", "destino"]
        for c in ["codigo_servicio", "numero_servicio", "aplicabilidad"]:
            if c in df.columns: cols_s.append(c)
        mask = pd.Series(False, index=df.index)
        for c in cols_s:
            if c in df.columns:
                mask = mask | df[c].fillna("").astype(str).str.lower().str.contains(q, na=False)
        df = df[mask]

    # ===== Tabla =====
    cols = ["id", "linea", "sentido", "tren", "tipo", "origen", "destino",
            "h_salida_str", "h_llegada_str", "duracion_min"]
    for c in ["codigo_servicio", "numero_servicio", "aplicabilidad", "dia"]:
        if c in df.columns: cols.append(c)
    cols = [c for c in cols if c in df.columns]

    df_disp = df[cols].rename(columns={
        "id": "ID", "linea": "Línea", "sentido": "Sentido", "tren": "Tren",
        "tipo": "Tipo", "origen": "Origen", "destino": "Destino",
        "h_salida_str": "Salida", "h_llegada_str": "Llegada",
        "duracion_min": "Duración (min)", "codigo_servicio": "Código",
        "numero_servicio": "N° serv.", "aplicabilidad": "Aplicabilidad",
        "dia": "Día",
    }).sort_values(["Línea", "Sentido", "Salida"])

    # ===== KPIs =====
    c_k1, c_k2, c_k3, c_k4 = st.columns(4)
    c_k1.metric("Resultados", f"{len(df_disp):,}")
    c_k2.metric("Líneas", df_disp["Línea"].nunique() if "Línea" in df_disp.columns else 0)
    c_k3.metric("Trenes únicos", df_disp["Tren"].nunique() if "Tren" in df_disp.columns else 0)
    if "Día" in df_disp.columns:
        c_k4.metric("Días cubiertos", df_disp["Día"].nunique())
    else:
        c_k4.metric("Sentidos", df_disp["Sentido"].nunique() if "Sentido" in df_disp.columns else 0)

    st.dataframe(df_disp, use_container_width=True, height=520, hide_index=True)
    total_full = len(services_df)
    cap = (f"{len(df_disp):,} servicios mostrados"
           + (f" de {total_full:,} totales" if len(df_disp) != total_full else "")
           + (f". Búsqueda: '{busqueda}'." if busqueda else "."))
    st.caption(cap)

    csv = df_disp.to_csv(index=False).encode("utf-8")
    st.download_button("📥 Descargar selección como CSV", data=csv,
                       file_name="servicios_biotren_filtrados.csv", mime="text/csv")

    if not df_disp.empty:
        with st.expander("🔎 Ver detalle de un servicio"):
            sid = st.selectbox("Servicio", df_disp["ID"].tolist(), key="cat_det")
            if sid:
                d = df[df["id"] == sid].iloc[0].to_dict()
                st.markdown(f"### {sid} · {d.get('linea','')} · {d.get('sentido','')}")
                d1, d2, d3 = st.columns(3)
                d1.markdown(
                    f"**Código:** {d.get('codigo_servicio','—') or '—'}<br>"
                    f"**N° servicio:** {d.get('numero_servicio','—') or '—'}<br>"
                    f"**Tipo:** {d.get('tipo','—')}", unsafe_allow_html=True)
                d2.markdown(
                    f"**Tren:** {d.get('tren','—')}<br>"
                    f"**Día:** {d.get('dia','—')}<br>"
                    f"**Aplicabilidad:** {d.get('aplicabilidad','—') or '—'}",
                    unsafe_allow_html=True)
                d3.markdown(
                    f"**Origen:** {d.get('origen','—')}<br>"
                    f"**Destino:** {d.get('destino','—')}<br>"
                    f"**Salida:** {d.get('h_salida_str','—')}<br>"
                    f"**Llegada:** {d.get('h_llegada_str','—')}", unsafe_allow_html=True)
