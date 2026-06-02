"""
Diagramas Gantt: rotación de trenes y ocupación de estaciones.

La ocupación de estaciones considera dos situaciones:
  - Tren en tránsito (pasa o se detiene brevemente para servicio comercial)
  - Tren estacionado / guardado (entre el fin de un servicio y el inicio del
    siguiente del mismo tren, cuando ambos ocurren en la misma estación)
"""

from __future__ import annotations

import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import streamlit as st


COLOR_BY_LINEA = {"L1": "#7030A0", "L2": "#1F4E78"}
COLOR_BY_TIPO = {
    "BT PM": "#FFE699", "BT PM BUCLE": "#FFD966",
    "BT VALLE": "#C5E0B4",
    "BT PT": "#F4B084", "BT PT BUCLE": "#E2A050",
    "EQUIPO VACÍO": "#D9D9D9",
    "LJ-TH": "#B4C7E7", "ESPECIAL TL": "#9DC3E6",
    "MANTENIMIENTO": "#8497B0",
}
BASE = pd.Timestamp("2026-04-20")


# ---------------------------------------------------------------------------
# Rotación de trenes
# ---------------------------------------------------------------------------

def aplica_en_dia(servicio: pd.Series, dia_sel: str) -> bool:
    """Determina si un servicio aplica en el día seleccionado.

    dia_sel: 'LJ' (Lun-Jue), 'V' (Viernes), 'SAB' (Sábado), 'DOM' (Domingo).
    Combina la marca 'dia' del servicio con la 'aplicabilidad'.
    """
    dia_data = (servicio.get("dia") or "LV").upper()
    apl = (servicio.get("aplicabilidad") or "").upper()

    if dia_sel == "SAB":
        return dia_data == "SAB"
    if dia_sel == "DOM":
        return dia_data == "DOM"
    if dia_sel == "LJ":
        if dia_data != "LV":
            return False
        # Solo-viernes: no aplica L-J
        if "VIERNES" in apl and "LUN" not in apl:
            return False
        return True
    if dia_sel == "V":
        if dia_data != "LV":
            return False
        # Solo-L-J: no aplica viernes
        if ("JUE" in apl) and ("VIE" not in apl) and ("SAB" not in apl) and ("SÁB" not in apl):
            return False
        return True
    return True


def render_train_rotation(services_df: pd.DataFrame, color_dim: str = "linea",
                          dia_sel: str = "LJ",
                          mostrar_codigos: bool = True,
                          hora_actual: float | None = None) -> None:
    """Diagrama de rotación de trenes, con filtro por día y código visible."""
    # Excluir canales de carga: la rotación es de la flota Biotren (SFE/SFB)
    df = services_df.dropna(subset=["h_salida_dt", "h_llegada_dt"]).copy()
    if "tipo" in df.columns:
        df = df[~df["tipo"].str.upper().str.contains("CARGA", na=False)]

    # Filtrar por día seleccionado
    if "dia" in df.columns:
        mascara = df.apply(lambda r: aplica_en_dia(r, dia_sel), axis=1)
        df = df[mascara]
    if df.empty:
        st.info(f"No hay servicios asignados para el día '{dia_sel}' "
                "en el itinerario activo.")
        return

    # Etiqueta visible: código operacional (CC/4 (N)) o fallback al id
    df["etiqueta"] = df["codigo_servicio"].where(
        df["codigo_servicio"].str.len() > 0, df["id"]
    ) if "codigo_servicio" in df.columns else df["id"]

    def sort_key(t: str):
        s = str(t).replace("SFE", "").replace("B", "").strip()
        try:
            return (0, int(s))
        except ValueError:
            return (1, str(t))

    train_order = sorted(df["tren"].unique(), key=sort_key)
    df["tren"] = pd.Categorical(df["tren"], categories=train_order, ordered=True)

    color_arg = "linea" if color_dim == "linea" else "tipo"
    color_map = COLOR_BY_LINEA if color_dim == "linea" else COLOR_BY_TIPO

    fig = px.timeline(
        df, x_start="h_salida_dt", x_end="h_llegada_dt", y="tren",
        color=color_arg, color_discrete_map=color_map,
        text="etiqueta" if mostrar_codigos else None,
        hover_data={"etiqueta": True, "id": True, "sentido": True, "tipo": True,
                    "h_salida_str": True, "h_llegada_str": True,
                    "h_salida_dt": False, "h_llegada_dt": False, "tren": False},
        labels={"etiqueta": "Código", "id": "ID", "tipo": "Tipo",
                "linea": "Línea"},
    )
    # Configurar visibilidad del texto del código
    if mostrar_codigos:
        fig.update_traces(
            textposition="inside", insidetextanchor="middle",
            textfont=dict(size=10, color="white"),
            cliponaxis=False,
        )
    fig.update_yaxes(autorange="reversed", title=None)
    fig.update_xaxes(title="Hora del día", tickformat="%H:%M",
                     dtick=60 * 60 * 1000, gridcolor="#EEEEEE")
    fig.update_layout(height=600, margin=dict(l=20, r=20, t=30, b=40),
                      plot_bgcolor="white", legend_title_text="",
                      bargap=0.15)
    # Línea vertical "hora actual"
    if hora_actual is not None:
        from datetime import datetime, timedelta
        h = float(hora_actual)
        x_hora = datetime(2026, 4, 20) + timedelta(hours=h)
        fig.add_shape(
            type="line", x0=x_hora, x1=x_hora, xref="x",
            y0=0, y1=1, yref="paper",
            line=dict(color="#E63946", width=2, dash="solid"),
        )
        fig.add_annotation(
            x=x_hora, xref="x", y=1.0, yref="paper",
            text=f"  {int(h):02d}:{int(h%1*60):02d}",
            showarrow=False, font=dict(size=11, color="#E63946"),
            xanchor="left", yanchor="bottom",
        )
    st.plotly_chart(fig, use_container_width=True, key="rot_gantt")


# ---------------------------------------------------------------------------
# Kilometraje por tren (cálculo desde dist_l1/dist_l2 implícito en passes)
# ---------------------------------------------------------------------------

def calcular_km_por_tren(services_df: pd.DataFrame,
                          passes_df: pd.DataFrame) -> dict:
    """Calcula los km por tren, devolviendo una tabla por tipo de día y otra semanal.

    Returns:
        dict con claves 'LJ', 'V', 'SAB', 'DOM', 'SEMANAL'. Cada valor es un
        DataFrame con columnas: Tren, Pasajeros, Equipo vacío, Mantenimiento, Total.
        Los días corresponden a km de un día calendario (Sáb y Dom = ese día).
        SEMANAL = LJ×4 + V + SAB + DOM (km recorridos en una semana operativa).
    """
    df = services_df.copy()
    if "tipo" in df.columns:
        df = df[~df["tipo"].str.upper().str.contains("CARGA", na=False)]
    df = df.dropna(subset=["h_salida", "h_llegada", "tren"])
    if df.empty:
        return {}

    # km por servicio: extremos de las pasadas
    extremos = (passes_df.dropna(subset=["dist_km"])
                .groupby("service_id")["dist_km"]
                .agg(["min", "max"]))
    extremos["km"] = (extremos["max"] - extremos["min"]).abs().round(2)
    df = df.merge(extremos[["km"]], left_on="id", right_index=True, how="left")
    df["km"] = df["km"].fillna(0)

    # Clasificar servicio
    def clase(t):
        t = (t or "").upper()
        if "VACÍO" in t or "VACIO" in t:
            return "Equipo vacío"
        if "MANTEN" in t:
            return "Mantenimiento"
        return "Pasajeros"
    df["clase"] = df["tipo"].apply(clase)

    # Distribuir cada servicio a sus días aplicables
    DIAS = ["LJ", "V", "SAB", "DOM"]
    rows = []
    for _, r in df.iterrows():
        dia = (r.get("dia") or "LV").upper()
        apl = (r.get("aplicabilidad") or "").upper()
        if dia == "SAB":
            dias_aplica = ["SAB"]
        elif dia == "DOM":
            dias_aplica = ["DOM"]
        else:  # LV
            lj = not ("VIERNES" in apl and "LUN" not in apl)
            v = not (("JUE" in apl) and ("VIE" not in apl)
                     and ("SAB" not in apl) and ("SÁB" not in apl))
            dias_aplica = []
            if lj: dias_aplica.append("LJ")
            if v: dias_aplica.append("V")
        for d in dias_aplica:
            rows.append({"tren": r["tren"], "clase": r["clase"],
                          "km": r["km"], "dia_clave": d})
    long_df = pd.DataFrame(rows)
    if long_df.empty:
        return {}

    # Función helper: tabla por día
    CLASES = ["Pasajeros", "Equipo vacío", "Mantenimiento"]
    def tabla_dia(dia_clave: str) -> pd.DataFrame:
        sub = long_df[long_df["dia_clave"] == dia_clave]
        if sub.empty:
            # Tabla vacía con todos los trenes en 0
            trenes = sorted(long_df["tren"].unique())
            return pd.DataFrame([
                {"Tren": t, **{c: 0.0 for c in CLASES}, "Total": 0.0}
                for t in trenes
            ])
        piv = sub.pivot_table(index="tren", columns="clase",
                               values="km", aggfunc="sum", fill_value=0)
        # Asegurar que las 3 columnas existen
        for c in CLASES:
            if c not in piv.columns:
                piv[c] = 0.0
        piv = piv[CLASES]  # ordenar
        piv["Total"] = piv.sum(axis=1)
        piv = piv.round(2)
        piv = piv.reset_index().rename(columns={"tren": "Tren"})
        return piv

    tablas = {d: tabla_dia(d) for d in DIAS}

    # Tabla SEMANAL: LJ×4 + V + SAB + DOM
    semanal_rows = []
    for tren in sorted(long_df["tren"].unique()):
        row = {"Tren": tren}
        for c in CLASES:
            km_lj = float(tablas["LJ"][tablas["LJ"]["Tren"] == tren][c].sum())
            km_v = float(tablas["V"][tablas["V"]["Tren"] == tren][c].sum())
            km_sab = float(tablas["SAB"][tablas["SAB"]["Tren"] == tren][c].sum())
            km_dom = float(tablas["DOM"][tablas["DOM"]["Tren"] == tren][c].sum())
            row[c] = round(km_lj * 4 + km_v + km_sab + km_dom, 2)
        row["Total"] = round(row["Pasajeros"] + row["Equipo vacío"]
                             + row["Mantenimiento"], 2)
        semanal_rows.append(row)
    tablas["SEMANAL"] = pd.DataFrame(semanal_rows)

    # Ordenar trenes en todas las tablas: SFE 1, 2, ... SFE B1, B2, ...
    def sort_key(t):
        s = str(t)
        if "B" in s.upper():
            num = ''.join(filter(str.isdigit, s))
            return (1, int(num) if num else 999)
        num = ''.join(filter(str.isdigit, s))
        return (0, int(num) if num else 999)

    for k, t in tablas.items():
        if not t.empty:
            t["_sk"] = t["Tren"].apply(sort_key)
            t.sort_values("_sk", inplace=True, ignore_index=True)
            t.drop(columns="_sk", inplace=True)

    return tablas


def render_km_por_tren(services_df: pd.DataFrame,
                        passes_df: pd.DataFrame) -> None:
    """Sección ejecutiva de kilometraje por tren.

    Layout:
      1) Tarjetas KPI con composición porcentual del total semanal.
      2) Insights ejecutivos (tren más / menos kilometrado, promedio).
      3) Sub-pestañas por tipo de día con gráfico de barras apilado +
         tabla numérica con barras visuales en celdas.
      4) Vista comparativa entre días.
      5) Descargas individuales y consolidada.
    """
    tablas = calcular_km_por_tren(services_df, passes_df)
    if not tablas:
        st.info("No hay datos suficientes para calcular kilometraje.")
        return

    # ===== Paleta de colores ejecutiva =====
    COL_PAX = "#1F4E78"      # azul corporativo
    COL_EV = "#E8A33D"       # ámbar
    COL_MANT = "#9C9C9C"     # gris
    COL_TOTAL = "#2C6FAE"    # azul medio

    sem = tablas["SEMANAL"]
    pax_sem = sem["Pasajeros"].sum()
    ev_sem = sem["Equipo vacío"].sum()
    mant_sem = sem["Mantenimiento"].sum()
    total_sem = sem["Total"].sum()
    pct_pax = (pax_sem / total_sem * 100) if total_sem else 0
    pct_ev = (ev_sem / total_sem * 100) if total_sem else 0
    pct_mant = (mant_sem / total_sem * 100) if total_sem else 0

    # ===== Hero KPIs ejecutivos =====
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("🚆 Pasajeros (sem)", f"{pax_sem:,.0f} km",
              f"{pct_pax:.1f}% del total", delta_color="off")
    c2.metric("📦 Equipo vacío (sem)", f"{ev_sem:,.0f} km",
              f"{pct_ev:.1f}% del total", delta_color="off")
    c3.metric("🔧 Mantenimiento (sem)", f"{mant_sem:,.0f} km",
              f"{pct_mant:.1f}% del total", delta_color="off")
    c4.metric("📊 Total flota (sem)", f"{total_sem:,.0f} km",
              f"{len(sem)} trenes", delta_color="off")

    # ===== Insights ejecutivos =====
    sem_no_zero = sem[sem["Total"] > 0]
    if not sem_no_zero.empty:
        tren_top = sem_no_zero.loc[sem_no_zero["Total"].idxmax()]
        tren_low = sem_no_zero.loc[sem_no_zero["Total"].idxmin()]
        prom = sem_no_zero["Total"].mean()
        median = sem_no_zero["Total"].median()
        ratio_max_min = (tren_top["Total"] / tren_low["Total"]
                         if tren_low["Total"] > 0 else float('inf'))

        st.markdown(
            '<div style="background:#F5F8FC;padding:14px 18px;'
            'border-left:4px solid #1F4E78;border-radius:6px;'
            'margin:14px 0;">'
            '<div style="font-weight:600;color:#1F4E78;'
            'margin-bottom:8px;">🎯 Insights ejecutivos · semana operacional</div>'
            f'<div style="font-size:13px;line-height:1.7;color:#2C3E50;">'
            f'• <b>Tren más utilizado:</b> {tren_top["Tren"]} con '
            f'<b>{tren_top["Total"]:,.0f} km/sem</b> ({tren_top["Pasajeros"]:,.0f} pax · '
            f'{tren_top["Equipo vacío"]:,.0f} EV).<br>'
            f'• <b>Tren menos utilizado:</b> {tren_low["Tren"]} con '
            f'<b>{tren_low["Total"]:,.0f} km/sem</b>.<br>'
            f'• <b>Promedio de flota:</b> {prom:,.0f} km/sem · '
            f'mediana {median:,.0f} km/sem.<br>'
            f'• <b>Brecha máx/mín:</b> el tren más usado recorre '
            f'<b>{ratio_max_min:.1f}×</b> los km del menos usado.'
            '</div></div>',
            unsafe_allow_html=True,
        )

    # ===== Sub-pestañas por día =====
    st.markdown("##### 📋 Kilometraje detallado por tipo de día")
    st.caption(
        "Cada pestaña muestra la composición visual y la tabla numérica de "
        "los km recorridos. Las barras dentro de cada celda permiten "
        "comparar visualmente entre trenes."
    )

    DIAS_INFO = [
        ("LJ", "Lun-Jue", "(km de un día tipo L-J)"),
        ("V", "Viernes", "(km del día Viernes)"),
        ("SAB", "Sábado", "(km del día Sábado)"),
        ("DOM", "Domingo", "(km del día Domingo)"),
        ("SEMANAL", "Semanal", "(L-J×4 + V + Sáb + Dom)"),
    ]

    sub_tabs = st.tabs([label for _, label, _ in DIAS_INFO])
    for (clave, label, suffix), tab in zip(DIAS_INFO, sub_tabs):
        with tab:
            t = tablas[clave].copy()
            tot_pax = t["Pasajeros"].sum()
            tot_ev = t["Equipo vacío"].sum()
            tot_mant = t["Mantenimiento"].sum()
            tot_tot = t["Total"].sum()

            # Card resumen
            st.markdown(
                f'<div style="background:linear-gradient(135deg,#1F4E78,#2C6FAE);'
                f'color:white;padding:14px 20px;border-radius:8px;'
                f'margin:6px 0 14px 0;">'
                f'<div style="font-size:12px;opacity:0.85;'
                f'text-transform:uppercase;letter-spacing:0.5px;">'
                f'Kilometraje {label} <span style="opacity:0.7;">'
                f'{suffix}</span></div>'
                f'<div style="font-size:28px;font-weight:700;'
                f'margin-top:4px;">{tot_tot:,.0f} km</div>'
                f'<div style="font-size:13px;margin-top:6px;opacity:0.95;">'
                f'🚆 {tot_pax:,.0f} pasajeros · 📦 {tot_ev:,.0f} EV · '
                f'🔧 {tot_mant:,.0f} mantenimiento</div>'
                f'</div>',
                unsafe_allow_html=True,
            )

            # Gráfico de barras horizontal apilado
            t_chart = t[t["Total"] > 0].sort_values("Total", ascending=True)
            if not t_chart.empty:
                fig = go.Figure()
                fig.add_bar(
                    y=t_chart["Tren"], x=t_chart["Pasajeros"],
                    name="🚆 Pasajeros", orientation="h",
                    marker=dict(color=COL_PAX,
                                line=dict(width=0)),
                    hovertemplate="<b>%{y}</b><br>Pasajeros: %{x:,.0f} km<extra></extra>",
                )
                fig.add_bar(
                    y=t_chart["Tren"], x=t_chart["Equipo vacío"],
                    name="📦 Equipo vacío", orientation="h",
                    marker=dict(color=COL_EV, line=dict(width=0)),
                    hovertemplate="<b>%{y}</b><br>Equipo vacío: %{x:,.0f} km<extra></extra>",
                )
                fig.add_bar(
                    y=t_chart["Tren"], x=t_chart["Mantenimiento"],
                    name="🔧 Mantenimiento", orientation="h",
                    marker=dict(color=COL_MANT, line=dict(width=0)),
                    hovertemplate="<b>%{y}</b><br>Mantenimiento: %{x:,.0f} km<extra></extra>",
                )
                # Etiquetas de total al final de cada barra
                fig.add_scatter(
                    y=t_chart["Tren"], x=t_chart["Total"],
                    mode="text",
                    text=[f"  {v:,.0f} km" for v in t_chart["Total"]],
                    textposition="middle right",
                    textfont=dict(size=10, color="#2C3E50"),
                    showlegend=False, hoverinfo="skip",
                )
                fig.update_layout(
                    barmode="stack",
                    height=max(380, 30 * len(t_chart) + 80),
                    margin=dict(l=20, r=120, t=20, b=40),
                    xaxis_title="Kilómetros recorridos",
                    yaxis_title=None,
                    plot_bgcolor="white",
                    legend=dict(orientation="h", y=1.06, yanchor="bottom",
                                x=0, font=dict(size=12)),
                    bargap=0.25,
                )
                fig.update_xaxes(gridcolor="#EEEEEE", zeroline=False,
                                  showline=False)
                fig.update_yaxes(showline=False)
                st.plotly_chart(fig, use_container_width=True,
                                 key=f"km_bar_{clave}")
            else:
                st.info(f"No hay kilometraje registrado para {label}.")

            # Tabla numérica con barras visuales
            with st.expander("📋 Ver tabla numérica con barras visuales"):
                t_view = t.copy()
                t_view.loc[len(t_view)] = ["TOTAL", tot_pax, tot_ev,
                                            tot_mant, tot_tot]
                # Renombrar para iconos en cabecera
                t_view = t_view.rename(columns={
                    "Pasajeros": "🚆 Pasajeros",
                    "Equipo vacío": "📦 Equipo vacío",
                    "Mantenimiento": "🔧 Mantenimiento",
                    "Total": "📊 Total",
                })
                # Estilizar (excluyendo última fila TOTAL para el bar)
                trenes_idx = t_view.index[:-1].tolist()
                styled = (
                    t_view.style
                    .format({
                        "🚆 Pasajeros": "{:,.1f}",
                        "📦 Equipo vacío": "{:,.1f}",
                        "🔧 Mantenimiento": "{:,.1f}",
                        "📊 Total": "{:,.1f}",
                    })
                    .bar(subset=pd.IndexSlice[trenes_idx, "🚆 Pasajeros"],
                          color=COL_PAX + "55", vmin=0)
                    .bar(subset=pd.IndexSlice[trenes_idx, "📦 Equipo vacío"],
                          color=COL_EV + "55", vmin=0)
                    .bar(subset=pd.IndexSlice[trenes_idx, "🔧 Mantenimiento"],
                          color=COL_MANT + "55", vmin=0)
                    .bar(subset=pd.IndexSlice[trenes_idx, "📊 Total"],
                          color=COL_TOTAL + "55", vmin=0)
                    .set_properties(
                        subset=pd.IndexSlice[[t_view.index[-1]], :],
                        **{"background-color": "#F0F4F9",
                           "font-weight": "bold"},
                    )
                )
                st.dataframe(
                    styled, use_container_width=True, hide_index=True,
                    height=min(540, 38 * (len(t_view) + 1) + 50),
                )

            # Descarga
            import io
            buf = io.BytesIO()
            with pd.ExcelWriter(buf, engine="openpyxl") as writer:
                t.to_excel(writer, sheet_name=f"km_{clave}", index=False)
            st.download_button(
                f"📥 Descargar km {label} (XLSX)",
                data=buf.getvalue(),
                file_name=f"km_por_tren_{clave.lower()}.xlsx",
                mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                key=f"dl_km_{clave}",
            )

    # ===== Comparativa entre tipos de día =====
    st.markdown("---")
    st.markdown("##### 🔄 Comparativa visual entre tipos de día")
    st.caption(
        "Total de km por tipo de día (escala diaria), para entender qué "
        "día concentra más operación. Los días con menos operación "
        "(domingo) son oportunidad de mantenimiento programado."
    )
    comp_data = pd.DataFrame([
        {"Día": "Lun-Jue", "Pasajeros": tablas["LJ"]["Pasajeros"].sum(),
         "Equipo vacío": tablas["LJ"]["Equipo vacío"].sum(),
         "Mantenimiento": tablas["LJ"]["Mantenimiento"].sum()},
        {"Día": "Viernes", "Pasajeros": tablas["V"]["Pasajeros"].sum(),
         "Equipo vacío": tablas["V"]["Equipo vacío"].sum(),
         "Mantenimiento": tablas["V"]["Mantenimiento"].sum()},
        {"Día": "Sábado", "Pasajeros": tablas["SAB"]["Pasajeros"].sum(),
         "Equipo vacío": tablas["SAB"]["Equipo vacío"].sum(),
         "Mantenimiento": tablas["SAB"]["Mantenimiento"].sum()},
        {"Día": "Domingo", "Pasajeros": tablas["DOM"]["Pasajeros"].sum(),
         "Equipo vacío": tablas["DOM"]["Equipo vacío"].sum(),
         "Mantenimiento": tablas["DOM"]["Mantenimiento"].sum()},
    ])
    fig_comp = go.Figure()
    fig_comp.add_bar(x=comp_data["Día"], y=comp_data["Pasajeros"],
                     name="🚆 Pasajeros",
                     marker=dict(color=COL_PAX, line=dict(width=0)))
    fig_comp.add_bar(x=comp_data["Día"], y=comp_data["Equipo vacío"],
                     name="📦 Equipo vacío",
                     marker=dict(color=COL_EV, line=dict(width=0)))
    fig_comp.add_bar(x=comp_data["Día"], y=comp_data["Mantenimiento"],
                     name="🔧 Mantenimiento",
                     marker=dict(color=COL_MANT, line=dict(width=0)))
    fig_comp.update_layout(
        barmode="stack", height=340,
        margin=dict(l=20, r=20, t=20, b=40),
        plot_bgcolor="white",
        yaxis_title="Km recorridos en el día",
        legend=dict(orientation="h", y=1.05, yanchor="bottom"),
        bargap=0.35,
    )
    fig_comp.update_xaxes(showline=False)
    fig_comp.update_yaxes(gridcolor="#EEEEEE")
    st.plotly_chart(fig_comp, use_container_width=True, key="km_compa_dia")

    # ===== Descarga consolidada =====
    st.markdown("---")
    import io
    buf = io.BytesIO()
    with pd.ExcelWriter(buf, engine="openpyxl") as writer:
        for clave, label, _ in DIAS_INFO:
            tablas[clave].to_excel(writer, sheet_name=label[:31], index=False)
    st.download_button(
        "📦 Descargar todas las tablas (XLSX único, 5 hojas)",
        data=buf.getvalue(),
        file_name="km_por_tren_biotren_completo.xlsx",
        mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        key="dl_km_all",
    )


# ---------------------------------------------------------------------------
# Ocupación de estaciones
# ---------------------------------------------------------------------------

def _stationed_intervals(services_df: pd.DataFrame, station: str) -> pd.DataFrame:
    """Detecta periodos en que un tren está estacionado/guardado en la estación.

    Para cada tren se ordenan sus servicios; si un servicio termina en la
    estación y el siguiente del mismo tren parte desde la misma estación,
    el intervalo intermedio se considera "tren estacionado".
    """
    rows = []
    for tren, grp in services_df.dropna(subset=["h_salida", "h_llegada"]).groupby("tren"):
        grp = grp.sort_values("h_salida")
        svcs = grp.to_dict("records")
        for i in range(len(svcs) - 1):
            actual = svcs[i]
            siguiente = svcs[i + 1]
            if actual["destino"] == station and siguiente["origen"] == station:
                t0 = actual["h_llegada"]
                t1 = siguiente["h_salida"]
                if t1 > t0:
                    rows.append({
                        "tren": tren,
                        "t_in": t0,
                        "t_out": t1,
                        "estado": "Estacionado / guardado",
                        "detalle": f"{actual['id']} → {siguiente['id']}",
                        "dur_min": round((t1 - t0) / 60, 1),
                    })
    return pd.DataFrame(rows)


def render_red_heatmap(passes_df: pd.DataFrame,
                        services_df: pd.DataFrame) -> None:
    """Heatmap panorámico estaciones × hora con cantidad de trenes presentes.

    Visualización compacta de TODA la red simultáneamente para detectar
    estaciones críticas y picos de ocupación.
    """
    # Excluir canales de carga (operadores externos)
    if "es_carga" in passes_df.columns:
        df = passes_df[~passes_df["es_carga"].fillna(False)].copy()
    else:
        df = passes_df.copy()
    df = df.dropna(subset=["station"])

    if df.empty:
        st.info("No hay datos para el mapa de calor.")
        return

    # Calcular pico de ocupación por estación × hora
    # Para cada paso, marcar tren presente durante el intervalo [llega, sale]
    # (si llega == None, usar sale; si sale == None, usar llega)
    import numpy as np

    orden_estaciones = [
        "Mercado", "Arenal", "Higueras", "Los Cóndores", "UTF Santa María",
        "Lorenzo Arenas", "CONCEPCIÓN",
        "Chiguayante", "Pedro Medina", "Manquimávida", "La Leonera",
        "Omer Huet", "Hualqui", "Quilacoya", "San Miguel", "Unihue",
        "Valle Chanco", "Los Acacios", "Talcamávida", "Gomero", "Buenuraqui",
        "San Rosendo", "Laja",
        "Juan Pablo II", "Diagonal Bio Bio", "Alborada", "Costa Mar",
        "El Parque", "Lomas Coloradas", "CRS Henríquez", "Hito Galvarino",
        "Los Canelos", "Huinca", "Cristo Redentor",
        "Desvío Lagunillas (HDLC)", "Laguna Quiñenco", "CORONEL",
    ]
    estaciones_en_datos = set(df["station"].dropna().unique())
    est_ordenadas = [e for e in orden_estaciones if e in estaciones_en_datos]
    est_ordenadas += sorted(estaciones_en_datos - set(est_ordenadas))

    HORAS = list(range(0, 27))
    # Matriz estaciones × horas (cantidad de servicios que pasan en esa hora)
    matriz = np.zeros((len(est_ordenadas), len(HORAS)), dtype=int)
    est_idx = {e: i for i, e in enumerate(est_ordenadas)}

    for _, row in df.iterrows():
        est = row.get("station")
        if est not in est_idx:
            continue
        # Convertir el tiempo de paso a hora (0-26)
        for col in ["arr_seconds", "dep_seconds"]:
            t = row.get(col)
            if pd.isna(t) or t is None:
                continue
            try:
                hora = int(t // 3600)
                if 0 <= hora < len(HORAS):
                    matriz[est_idx[est]][hora] += 1
            except (ValueError, TypeError):
                continue

    # Cada servicio se cuenta dos veces (llega + sale) si tiene ambos. Divido /2.
    matriz = (matriz / 2).astype(int)

    # Estaciones con más actividad → para insight
    total_por_est = matriz.sum(axis=1)
    top_est_idx = np.argsort(total_por_est)[::-1][:5]
    top_estaciones = [(est_ordenadas[i], total_por_est[i]) for i in top_est_idx
                       if total_por_est[i] > 0]

    # Resumen ejecutivo
    if top_estaciones:
        st.markdown(
            '<div style="background:#F5F8FC;padding:12px 16px;'
            'border-left:4px solid #1F4E78;border-radius:6px;'
            'margin:8px 0 14px 0;font-size:13px;">'
            '<b>🎯 Estaciones con mayor actividad (servicios/día):</b><br>'
            + " · ".join([f"<b>{e}</b> ({n})" for e, n in top_estaciones])
            + '</div>',
            unsafe_allow_html=True,
        )

    # Heatmap
    fig = go.Figure(data=go.Heatmap(
        z=matriz,
        x=[f"{h:02d}:00" for h in HORAS],
        y=est_ordenadas,
        colorscale=[
            [0.0, "#FFFFFF"],
            [0.1, "#E8EEF7"],
            [0.4, "#7FA9CD"],
            [0.7, "#2C6FAE"],
            [1.0, "#1F4E78"],
        ],
        colorbar=dict(title="Servicios", thickness=12, len=0.7),
        hovertemplate="<b>%{y}</b><br>Hora: %{x}<br>Servicios: %{z}<extra></extra>",
        xgap=1, ygap=1,
    ))
    fig.update_layout(
        height=max(520, 18 * len(est_ordenadas) + 100),
        margin=dict(l=20, r=20, t=40, b=40),
        title="Mapa de actividad: estaciones × hora del día",
    )
    fig.update_xaxes(side="top", tickangle=-45)
    fig.update_yaxes(autorange="reversed")
    st.plotly_chart(fig, use_container_width=True, key="red_heatmap")


def render_station_occupancy(passes_df: pd.DataFrame, services_df: pd.DataFrame,
                              station: str) -> None:
    """Gantt de ocupación de una estación: tránsito + estacionamiento."""

    # --- Trenes en tránsito (pasos con servicio) ---
    df = passes_df[passes_df["station"] == station].copy()

    transit_rows = []
    if not df.empty:
        df["t_in"] = df["llega"].fillna(df["sale"])
        df["t_out"] = df["sale"].fillna(df["llega"])
        # ocupación mínima de 1 minuto si solo hay un timestamp
        df["t_out"] = df["t_out"].where(df["t_out"] != df["t_in"], df["t_in"] + 60)
        meta = services_df.set_index("id")[["tipo"]]
        df = df.merge(meta, left_on="service_id", right_index=True,
                      how="left", suffixes=("", "_svc"))
        for _, r in df.iterrows():
            transit_rows.append({
                "tren": r["tren"],
                "t_in": r["t_in"],
                "t_out": r["t_out"],
                "estado": "En tránsito (servicio)",
                "detalle": f"{r['service_id']} · {r['sentido']}",
                "dur_min": round((r["t_out"] - r["t_in"]) / 60, 1),
            })
    transit_df = pd.DataFrame(transit_rows)

    # --- Trenes estacionados / guardados ---
    stationed_df = _stationed_intervals(services_df, station)

    # --- Combinar ---
    all_occ = pd.concat([transit_df, stationed_df], ignore_index=True)
    if all_occ.empty:
        st.info(f"Sin actividad registrada en {station} en el itinerario actual.")
        return

    all_occ["t_in_dt"] = BASE + pd.to_timedelta(all_occ["t_in"], unit="s")
    all_occ["t_out_dt"] = BASE + pd.to_timedelta(all_occ["t_out"], unit="s")

    fig = px.timeline(
        all_occ.sort_values("t_in"),
        x_start="t_in_dt", x_end="t_out_dt", y="estado",
        color="estado",
        color_discrete_map={
            "En tránsito (servicio)": "#1F4E78",
            "Estacionado / guardado": "#E89B3F",
        },
        hover_data={"tren": True, "detalle": True, "dur_min": True,
                    "t_in_dt": False, "t_out_dt": False, "estado": False},
        labels={"tren": "Tren", "detalle": "Detalle", "dur_min": "Duración (min)"},
    )
    fig.update_xaxes(title="Hora del día", tickformat="%H:%M",
                     dtick=60 * 60 * 1000, gridcolor="#EEEEEE")
    fig.update_yaxes(title=None)
    fig.update_layout(height=300, margin=dict(l=20, r=20, t=30, b=40),
                      plot_bgcolor="white", legend_title_text="",
                      title=f"Ocupación de la estación {station}")
    st.plotly_chart(fig, use_container_width=True,
                     key=f"occ_gantt_{station}")

    # --- Curva de ocupación simultánea ---
    eventos = []
    for _, r in all_occ.iterrows():
        eventos.append((r["t_in"], 1))
        eventos.append((r["t_out"], -1))
    eventos.sort()
    serie_t, serie_n = [], []
    cur = 0
    for t, d in eventos:
        cur += d
        serie_t.append(BASE + pd.to_timedelta(t, unit="s"))
        serie_n.append(cur)
    ocup_df = pd.DataFrame({"hora": serie_t, "trenes": serie_n})
    pico = max(serie_n) if serie_n else 0

    fig2 = px.area(ocup_df, x="hora", y="trenes",
                   labels={"hora": "Hora del día", "trenes": "Trenes presentes"})
    fig2.update_traces(line_color="#2E75B6", fillcolor="rgba(46,117,182,0.25)")
    fig2.update_xaxes(tickformat="%H:%M", dtick=60 * 60 * 1000, gridcolor="#EEEEEE")
    fig2.update_yaxes(gridcolor="#EEEEEE", dtick=1)
    fig2.update_layout(height=240, margin=dict(l=20, r=20, t=30, b=40),
                       plot_bgcolor="white",
                       title=f"Trenes presentes simultáneamente en {station}")
    st.plotly_chart(fig2, use_container_width=True,
                     key=f"occ_curve_{station}")

    # --- Métricas ---
    n_transito = len(transit_df)
    n_estacionado = len(stationed_df)
    h_transito = transit_df["dur_min"].sum() / 60 if not transit_df.empty else 0
    h_estacionado = stationed_df["dur_min"].sum() / 60 if not stationed_df.empty else 0

    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Pasos en tránsito", n_transito)
    c2.metric("Episodios estacionado", n_estacionado,
              help="Veces que un tren queda guardado entre dos servicios")
    c3.metric("Ocupación pico", pico,
              help="Máx. de trenes presentes a la vez (tránsito + guardado)")
    c4.metric("Horas-tren guardado", f"{h_estacionado:.1f} h")
