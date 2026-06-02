"""
Biotren Itinerario Studio  ·  Dashboard de planificación operacional.

Run with:
    streamlit run app.py
"""

from __future__ import annotations

import streamlit as st

from data_loader import (
    load_services,
    load_infrastructure,
    load_coordinates,
)
from components.viz_marey import render_marey
from components.viz_map import show_map
from components.viz_kpis import render_kpis
from components.viz_gantt import (
    render_train_rotation, render_station_occupancy, render_km_por_tren,
    render_red_heatmap,
)
from components.viz_turnos import render_turnos
from components.viz_escenarios import render_escenarios
from components.viz_services import render_services_table


# =============================================================================
# Configuración general de la página
# =============================================================================

st.set_page_config(
    page_title="Biotren Itinerario Studio",
    page_icon="🚆",
    layout="wide",
    initial_sidebar_state="expanded",
)

st.markdown("""
<style>
    .main .block-container {padding-top: 1rem; padding-bottom: 1rem;}
    section[data-testid="stSidebar"] {background: #F4F6FA;}
    h1 {color: #1F4E78;}
    h2, h3 {color: #1F4E78;}
    [data-testid="stMetricValue"] {color: #1F4E78;}
</style>
""", unsafe_allow_html=True)


# =============================================================================
# Selector de itinerario (Vigente / Propuesta) — global a toda la app
# =============================================================================

st.sidebar.markdown("## 🚆 Biotren Itinerario Studio")
st.sidebar.markdown("---")
st.sidebar.markdown("### 🗂️ Itinerario activo")
escenario_label = st.sidebar.radio(
    "Selecciona el itinerario",
    ["Vigente (circular GOF)", "Propuesta (con CDC)"],
    index=0,
    help="Vigente: itinerario operacional de la circular GOF-S 2/419. "
         "Propuesta: itinerario propuesto, incluye canales de carga (CDC).",
    label_visibility="collapsed",
)
escenario = "vigente" if "Vigente" in escenario_label else "propuesta"

# =============================================================================
# Carga de datos del escenario seleccionado (cacheado por escenario)
# =============================================================================

services_df, passes_df, dist_l1, dist_l2 = load_services(escenario)
estaciones_df, tramos_df, infra_raw = load_infrastructure()
coords_df = load_coordinates()


# =============================================================================
# Barra lateral con info de la fuente
# =============================================================================

st.sidebar.markdown("---")
st.sidebar.markdown("### 📂 Fuentes de datos")
n_carga = int(services_df["es_carga"].sum()) if "es_carga" in services_df.columns else 0
st.sidebar.success(
    f"✓ Itinerario: **{escenario_label.split(' (')[0]}**\n\n"
    f"✓ Servicios: **{len(services_df)}**"
    + (f" (incl. {n_carga} CDC)" if n_carga else "") + "\n\n"
    f"✓ Estaciones: **{len(estaciones_df)}**\n\n"
    f"✓ Tramos: **{len(tramos_df)}**"
)
fuente = "biotren_vigente.xlsx" if escenario == "vigente" else "biotren_datos.xlsx"
st.sidebar.caption(f"Datos cargados desde `data/{fuente}`")
if st.sidebar.button("🔄 Recargar datos del Excel", use_container_width=True):
    st.cache_data.clear()
    st.rerun()
st.sidebar.markdown("---")
st.sidebar.markdown("### ℹ️ Documentación")
st.sidebar.caption(
    "Esta aplicación visualiza el itinerario operacional de Biotren "
    "y permite analizarlo desde múltiples perspectivas: "
    "diagrama de Marey (distancia–tiempo), mapa geográfico, "
    "rotación de trenes, ocupación de estaciones críticas, "
    "indicadores operacionales y catálogo de servicios."
)


# =============================================================================
# Encabezado
# =============================================================================

st.markdown("""
<style>
    /* Compactar márgenes superiores */
    .block-container { padding-top: 2.2rem !important; padding-bottom: 1rem; }
    /* Cabecera tipo "hero" */
    .hero-bar {
        background: linear-gradient(90deg, #1F4E78 0%, #2C6FAE 100%);
        color: white; padding: 16px 22px; border-radius: 10px;
        margin-bottom: 14px;
    }
    .hero-bar h1 {
        margin: 0 0 6px 0; color: white; font-size: 26px;
        font-weight: 700;
    }
    .hero-bar .subt {
        opacity: 0.95; font-size: 13px;
    }
    .hero-bar .badges { margin-top: 10px; display: flex; gap: 8px;
                       flex-wrap: wrap; }
    .hero-bar .badge {
        background: rgba(255,255,255,0.18); padding: 4px 10px;
        border-radius: 20px; font-size: 12px; font-weight: 500;
    }
    /* Tabs más legibles */
    button[data-baseweb="tab"] { font-size: 14px !important; }
    /* Métricas con mejor jerarquía */
    [data-testid="stMetricValue"] {
        color: #1F4E78; font-weight: 700;
    }
    /* Bordes suaves en tablas */
    [data-testid="stDataFrame"] { border-radius: 8px; overflow: hidden; }
</style>
""", unsafe_allow_html=True)

n_servicios = len(services_df)
n_carga = int(services_df["es_carga"].sum()) if "es_carga" in services_df.columns else 0
n_pax = n_servicios - n_carga
trenes_unicos = services_df["tren"].nunique() if "tren" in services_df.columns else 0
escenario_pretty = escenario_label.split(" (")[0]

badges_html = [
    f'<span class="badge">📋 Itinerario: <b>{escenario_pretty}</b></span>',
    f'<span class="badge">🚂 {n_pax} servicios pasajeros</span>',
]
if n_carga:
    badges_html.append(f'<span class="badge">📦 {n_carga} canales de carga</span>')
badges_html.append(f'<span class="badge">🚆 {trenes_unicos} trenes</span>')
badges_html.append(f'<span class="badge">📍 {len(estaciones_df)} estaciones</span>')
badges_str = " ".join(badges_html)

hero = (
    '<div class="hero-bar">'
    '<h1>🚆 Biotren Itinerario Studio</h1>'
    '<div class="subt">Análisis del itinerario operacional · L1 Laja–Talcahuano · L2 Concepción–Coronel</div>'
    f'<div class="badges">{badges_str}</div>'
    '</div>'
)
st.markdown(hero, unsafe_allow_html=True)


# =============================================================================
# 📊 Resumen ejecutivo en portada
# =============================================================================

with st.container():
    # Calcular insights ejecutivos a partir de los datos cargados
    from components.viz_gantt import (aplica_en_dia, calcular_km_por_tren)

    # Servicios por tipo de día
    sv_lj = services_df[services_df.apply(lambda r: aplica_en_dia(r, "LJ"), axis=1)] if "dia" in services_df.columns else services_df
    sv_sab = services_df[services_df.apply(lambda r: aplica_en_dia(r, "SAB"), axis=1)] if "dia" in services_df.columns else pd.DataFrame()
    sv_dom = services_df[services_df.apply(lambda r: aplica_en_dia(r, "DOM"), axis=1)] if "dia" in services_df.columns else pd.DataFrame()
    n_lj = len(sv_lj[~sv_lj["es_carga"].fillna(False)]) if "es_carga" in sv_lj.columns else len(sv_lj)

    # Km totales (semanales)
    try:
        tablas_km = calcular_km_por_tren(services_df, passes_df)
        km_sem = tablas_km["SEMANAL"]["Total"].sum() if tablas_km else 0
    except Exception:
        km_sem = 0

    # Headway promedio L1 y L2 en hora punta
    def headway_promedio(sv_df, linea, h_min=7, h_max=9):
        sub = sv_df[(sv_df["linea"] == linea) & (~sv_df.get("es_carga", False).fillna(False))]
        sub = sub.dropna(subset=["h_salida"])
        sub = sub[(sub["h_salida"] >= h_min * 3600) & (sub["h_salida"] <= h_max * 3600)]
        if len(sub) < 2:
            return None
        salidas = sorted(sub["h_salida"].tolist())
        diffs = [(salidas[i+1] - salidas[i])/60 for i in range(len(salidas)-1)]
        return sum(diffs) / len(diffs) if diffs else None
    hw_l1 = headway_promedio(sv_lj, "L1")
    hw_l2 = headway_promedio(sv_lj, "L2")

    # Resumen
    st.markdown(
        '<div style="background:linear-gradient(180deg,#FFFFFF,#F8FAFC);'
        'padding:18px 22px;border-radius:10px;border:1px solid #E1E8F0;'
        'margin:6px 0 18px 0;">'
        '<div style="font-size:15px;font-weight:600;color:#1F4E78;'
        'margin-bottom:12px;">📊 Resumen ejecutivo del itinerario activo</div>',
        unsafe_allow_html=True,
    )

    rc1, rc2, rc3, rc4, rc5 = st.columns(5)
    rc1.metric("Servicios L-J", n_lj,
               help="Servicios de pasajeros y movimientos no comerciales un día tipo L-J")
    rc2.metric("Servicios Sáb", len(sv_sab))
    rc3.metric("Servicios Dom", len(sv_dom))
    rc4.metric("Trenes en uso", trenes_unicos)
    rc5.metric("Km flota / sem", f"{km_sem:,.0f} km" if km_sem else "—")

    rc6, rc7, rc8 = st.columns(3)
    rc6.metric("Headway prom L1 (7-9h)",
               f"{hw_l1:.0f} min" if hw_l1 else "—",
               help="Intervalo promedio entre salidas en hora punta mañana en L1")
    rc7.metric("Headway prom L2 (7-9h)",
               f"{hw_l2:.0f} min" if hw_l2 else "—")
    rc8.metric("Estaciones de la red", len(estaciones_df))

    st.markdown('</div>', unsafe_allow_html=True)


# =============================================================================
# Tabs principales
# =============================================================================

tab_marey, tab_map, tab_kpis, tab_rot, tab_turnos, tab_est, tab_esc, tab_svc = st.tabs([
    "📈 Diagrama de Marey",
    "🗺️ Mapa de la red",
    "📊 Indicadores",
    "🚂 Rotación de trenes",
    "👷 Turnos teóricos",
    "🏢 Ocupación de estaciones",
    "🔬 Escenarios",
    "📋 Catálogo de servicios",
])


# -----------------------------------------------------------------------------
# Tab 1: Diagrama de Marey
# -----------------------------------------------------------------------------

with tab_marey:
    st.markdown("### Diagrama de Marey (distancia × tiempo)")
    st.caption(
        "Cada línea es un servicio. La pendiente representa la velocidad comercial. "
        "Las zonas rojas marcan tramos de vía simple donde un cruce de líneas "
        "azul–roja indica un cruzamiento real y debe ocurrir en estación con desvío."
    )

    cfg1, cfg2, cfg3 = st.columns([1, 1.3, 2])
    with cfg1:
        linea_sel = st.radio("Línea", ["L1", "L2"], horizontal=True, key="marey_linea")
    with cfg2:
        dia_marey_label = st.radio(
            "Día", ["Lun-Jue", "Viernes", "Sábado", "Domingo"],
            horizontal=True, key="marey_dia",
            help="La oferta de servicios cambia por tipo de día.",
        )
    with cfg3:
        t_range = st.slider(
            "Ventana horaria",
            min_value=0.0, max_value=27.0,
            value=(5.0, 24.0),
            step=0.5, format="%.1f h",
            key="marey_window",
            help="Más allá de 24:00 se muestran los canales de carga "
                 "nocturnos que cruzan la medianoche.",
        )
    DIA_MAP_MAREY = {"Lun-Jue": "LJ", "Viernes": "V",
                      "Sábado": "SAB", "Domingo": "DOM"}

    sentidos_disponibles = ["CC→CW", "CW→CC"] if linea_sel == "L2" else ["LJ→TH", "TH→LJ"]
    cfg4, cfg5, cfg6 = st.columns([1, 1.3, 2])
    with cfg4:
        sentidos_sel = st.multiselect(
            "Sentidos",
            sentidos_disponibles,
            default=sentidos_disponibles,
            key=f"marey_sentidos_{linea_sel}",
        )
    with cfg5:
        color_dim = st.radio("Color por", ["sentido", "tipo", "tren"],
                             horizontal=True, key="marey_color")
    with cfg6:
        trenes_linea = sorted(
            services_df[services_df["linea"] == linea_sel]["tren"].unique(),
            key=lambda t: (0, int(str(t).replace("SFE","").replace("B","").strip())) if str(t).replace("SFE","").replace("B","").strip().isdigit() else (1, t),
        )
        highlight = st.selectbox(
            "Destacar un tren específico (opcional)",
            ["(ninguno)"] + trenes_linea,
            key="marey_highlight",
        )

    highlight_train = None if highlight == "(ninguno)" else highlight

    # Toggle "Visualizar CDC" — solo tiene efecto si el itinerario incluye carga
    tiene_carga = ("es_carga" in passes_df.columns
                   and bool(passes_df["es_carga"].fillna(False).any()))
    if tiene_carga:
        mostrar_cdc = st.toggle(
            "Visualizar CDC (Canales de Carga)", value=True,
            help="Muestra/oculta los canales de carga (operadores externos) "
                 "en el diagrama. Solo aplica al itinerario propuesto.",
        )
    else:
        mostrar_cdc = False
        st.caption("ℹ️ El itinerario vigente no incluye canales de carga.")

    # ===== Cursor temporal + modo comparación =====
    c_extra1, c_extra2 = st.columns([2, 1])
    with c_extra1:
        usar_cursor = st.checkbox(
            "🕒 Mostrar cursor de hora", value=False, key="marey_cursor",
            help="Línea vertical roja sobre el diagrama para inspeccionar "
                 "una hora específica del día.",
        )
        if usar_cursor:
            hora_cursor = st.slider(
                "Hora del cursor", min_value=float(t_range[0]),
                max_value=float(t_range[1]), value=float((t_range[0]+t_range[1])/2),
                step=0.083, format="%.2f h", key="marey_hora_cursor",
                help="0.083 h ≈ 5 min",
            )
        else:
            hora_cursor = None
    with c_extra2:
        modo_comparacion = st.toggle(
            "↔️ Comparar Vigente vs Propuesta", value=False, key="marey_comp",
            help="Muestra ambos itinerarios lado a lado para inspeccionar diferencias.",
        )

    if modo_comparacion:
        # Cargar el escenario opuesto
        from data_loader import load_services as _load
        otro_esc = "propuesta" if escenario == "vigente" else "vigente"
        sv_otro, pa_otro, _, _ = _load(otro_esc)
        st.markdown(f"**Comparando: izquierda = {escenario.title()} · "
                     f"derecha = {otro_esc.title()}**")

        col_izq, col_der = st.columns(2)
        with col_izq:
            st.caption(f"📋 {escenario.title()}")
            fig_a = render_marey(
                passes_df, linea=linea_sel, sentidos=sentidos_sel,
                color_dim=color_dim,
                t_min_h=t_range[0], t_max_h=t_range[1],
                highlight_train=highlight_train,
                show_single_track=True, mostrar_carga=mostrar_cdc,
                dia_sel=DIA_MAP_MAREY[dia_marey_label],
                hora_actual=hora_cursor,
            )
            fig_a.update_layout(height=580)
            st.plotly_chart(fig_a, use_container_width=True, key="marey_izq")
        with col_der:
            st.caption(f"📋 {otro_esc.title()}")
            fig_b = render_marey(
                pa_otro, linea=linea_sel, sentidos=sentidos_sel,
                color_dim=color_dim,
                t_min_h=t_range[0], t_max_h=t_range[1],
                highlight_train=highlight_train,
                show_single_track=True, mostrar_carga=mostrar_cdc,
                dia_sel=DIA_MAP_MAREY[dia_marey_label],
                hora_actual=hora_cursor,
            )
            fig_b.update_layout(height=580)
            st.plotly_chart(fig_b, use_container_width=True, key="marey_der")
    else:
        fig = render_marey(
            passes_df, linea=linea_sel, sentidos=sentidos_sel,
            color_dim=color_dim,
            t_min_h=t_range[0], t_max_h=t_range[1],
            highlight_train=highlight_train,
            show_single_track=True,
            mostrar_carga=mostrar_cdc,
            dia_sel=DIA_MAP_MAREY[dia_marey_label],
            hora_actual=hora_cursor,
        )
        st.plotly_chart(fig, use_container_width=True, key="marey_solo")

    with st.expander("💡 Cómo leer el diagrama"):
        st.markdown("""
- **Pendiente**: mientras más vertical la línea, más lento avanza el tren.
  Tramos planos = detención en estación.
- **Distancia entre líneas paralelas**: separación temporal (headway) entre dos
  servicios consecutivos del mismo sentido. Mide la frecuencia ofertada.
- **Cruce entre línea azul y roja**: dos servicios se encuentran en ese punto y
  hora. En **doble vía** no genera conflicto. En **vía simple** (zonas rojas
  del fondo, en L1) debe coincidir con una estación de cruce: Higueras,
  Arenal, La Leonera, Hualqui, Quilacoya, San Rosendo o Laja.
- **Líneas punteadas color tierra**: son **canales de carga** (operador
  externo). Comparten la infraestructura con los servicios de pasajeros, por
  lo que es relevante verlos aquí para detectar conflictos de vía, pero no se
  incluyen en los indicadores, turnos ni rotación de flota Biotren.
- **Selecciona un tren** en el filtro de arriba para resaltar su trayectoria
  completa a lo largo del día.
        """)


# -----------------------------------------------------------------------------
# Tab 2: Mapa geográfico
# -----------------------------------------------------------------------------

with tab_map:
    st.markdown("### Mapa de la red Biotren")
    st.caption(
        "Estaciones georreferenciadas y tramos coloreados según tipo de vía. "
        "Haz clic en una estación para ver sus atributos operacionales."
    )
    show_map(estaciones_df, coords_df, tramos_df)


# -----------------------------------------------------------------------------
# Tab 3: Indicadores operacionales
# -----------------------------------------------------------------------------

with tab_kpis:
    st.markdown("### Indicadores operacionales del itinerario activo")
    c_dia, c_modo = st.columns([2, 1])
    with c_dia:
        dia_kpis_label = st.radio(
            "Día del servicio", ["Lun-Jue", "Viernes", "Sábado", "Domingo"],
            horizontal=True, key="kpis_dia",
        )
    with c_modo:
        comp_kpi = st.toggle(
            "↔️ Comparar Vigente vs Propuesta", value=False, key="kpi_comp",
            help="Muestra KPIs de ambos itinerarios lado a lado con deltas.",
        )
    DIA_MAP_KPI = {"Lun-Jue": "LJ", "Viernes": "V",
                   "Sábado": "SAB", "Domingo": "DOM"}

    if comp_kpi:
        from data_loader import load_services as _load
        otro_esc = "propuesta" if escenario == "vigente" else "vigente"
        sv_otro, pa_otro, _, _ = _load(otro_esc)
        col_izq, col_der = st.columns(2)
        with col_izq:
            st.markdown(f"#### 📋 {escenario.title()}")
            render_kpis(services_df, passes_df,
                        dia_sel=DIA_MAP_KPI[dia_kpis_label],
                        key_prefix=f"kpi_izq_{escenario}")
        with col_der:
            st.markdown(f"#### 📋 {otro_esc.title()}")
            render_kpis(sv_otro, pa_otro,
                        dia_sel=DIA_MAP_KPI[dia_kpis_label],
                        key_prefix=f"kpi_der_{otro_esc}")
    else:
        render_kpis(services_df, passes_df,
                    dia_sel=DIA_MAP_KPI[dia_kpis_label],
                    key_prefix=f"kpi_solo_{escenario}")


# -----------------------------------------------------------------------------
# Tab 4: Rotación de trenes
# -----------------------------------------------------------------------------

with tab_rot:
    st.markdown("### Rotación diaria de trenes")
    st.caption(
        "Cada fila es un tren SFE/SFB. Las barras son los servicios que "
        "opera a lo largo del día, etiquetadas con su código operacional "
        "(p. ej. CC/4 (N), CW/2 (S)). Los huecos entre barras son tiempos muertos."
    )

    c1, c2, c3 = st.columns([2, 1.2, 1])
    with c1:
        dia_sel_label = st.radio(
            "Día del servicio", ["Lun-Jue", "Viernes", "Sábado", "Domingo"],
            horizontal=True, key="rot_dia",
            help="La rotación cambia según el día. Los servicios marcados como "
                 "'Lun. a Jue.' no aparecen los viernes; los marcados 'Viernes' "
                 "solo aparecen ese día. Sábado y domingo usan sus propios "
                 "itinerarios.",
        )
    with c2:
        color_rot = st.radio(
            "Color por", ["linea", "tipo"], horizontal=True, key="rot_color",
        )
    with c3:
        mostrar_codigos = st.checkbox("Mostrar códigos", value=True,
                                       key="rot_codigos")
    DIA_MAP = {"Lun-Jue": "LJ", "Viernes": "V", "Sábado": "SAB",
               "Domingo": "DOM"}

    # Cursor temporal opcional
    usar_cursor_rot = st.checkbox(
        "🕒 Mostrar cursor de hora en el Gantt", value=False, key="rot_cursor",
        help="Línea vertical roja para identificar qué tren opera a una hora exacta.",
    )
    hora_cursor_rot = None
    if usar_cursor_rot:
        hora_cursor_rot = st.slider(
            "Hora del cursor", min_value=0.0, max_value=24.0, value=12.0,
            step=0.083, format="%.2f h", key="rot_hora_cursor",
        )
    render_train_rotation(services_df, color_dim=color_rot,
                          dia_sel=DIA_MAP[dia_sel_label],
                          mostrar_codigos=mostrar_codigos,
                          hora_actual=hora_cursor_rot)

    # ----- Kilometraje por tren -----
    st.markdown("---")
    st.markdown("### 📏 Kilometraje por tren")
    st.caption(
        "Cálculo de los km recorridos por cada tren, separando los servicios "
        "de pasajeros de los movimientos no comerciales (equipo vacío, "
        "mantenimiento). El cálculo semanal asume 4 días L-J + 1 V + 1 Sáb "
        "+ 1 Dom."
    )
    render_km_por_tren(services_df, passes_df)


# -----------------------------------------------------------------------------
# Tab 5: Turnos teóricos de conducción
# -----------------------------------------------------------------------------

with tab_turnos:
    st.markdown("### Turnos teóricos de conducción")
    st.caption(
        "Turnos construidos automáticamente a partir del itinerario activo, "
        "respetando las restricciones de tracción: jornada ≤ 7:30 h, "
        "conducción ≤ 5:00 h, traslados desde el depósito El Arenal. "
        "La dotación de parejas se calcula con los turnos reales de los 4 "
        "tipos de día (L-J × 4 + V + Sáb + Dom)."
    )
    dia_turnos_label = st.radio(
        "Día a visualizar", ["Lun-Jue", "Viernes", "Sábado", "Domingo"],
        horizontal=True, key="turnos_dia",
        help="El Gantt y las métricas del día se calculan para el día "
             "elegido. La dotación semanal usa los 4 tipos de día.",
    )
    DIA_MAP_TURNOS = {"Lun-Jue": "LJ", "Viernes": "V",
                      "Sábado": "SAB", "Domingo": "DOM"}
    render_turnos(services_df, dia_sel=DIA_MAP_TURNOS[dia_turnos_label])


# -----------------------------------------------------------------------------
# Tab 6: Ocupación de estaciones
# -----------------------------------------------------------------------------

with tab_est:
    st.markdown("### Ocupación de estaciones")
    # Sub-tabs: panorámica vs detalle por estación
    sub_pan, sub_det = st.tabs([
        "🌐 Vista panorámica (toda la red)",
        "📍 Detalle por estación",
    ])

    with sub_pan:
        st.caption(
            "Mapa de calor de **toda la red** mostrando la cantidad de "
            "servicios que pasan por cada estación a cada hora del día. "
            "Permite identificar de un vistazo las estaciones críticas y "
            "los picos de actividad."
        )
        dia_pan_label = st.radio(
            "Día", ["Lun-Jue", "Viernes", "Sábado", "Domingo"],
            horizontal=True, key="pan_dia",
        )
        DIA_MAP_PAN = {"Lun-Jue": "LJ", "Viernes": "V",
                       "Sábado": "SAB", "Domingo": "DOM"}
        from components.viz_gantt import aplica_en_dia as _apl
        if "dia" in services_df.columns:
            mask_p = services_df.apply(
                lambda r: _apl(r, DIA_MAP_PAN[dia_pan_label]), axis=1)
            sv_p = services_df[mask_p]
            pa_p = passes_df[passes_df["service_id"].isin(sv_p["id"])]
        else:
            sv_p, pa_p = services_df, passes_df
        render_red_heatmap(pa_p, sv_p)

    with sub_det:
        st.caption(
            "Para cada estación se muestran los trenes en tránsito y los "
            "trenes estacionados entre dos servicios. La curva inferior "
            "indica cuántos trenes están presentes a la vez."
        )
        coc1, coc2 = st.columns([2, 1])
        with coc1:
            orden_estaciones = [
                "Mercado", "Arenal", "Higueras", "Los Cóndores", "UTF Santa María",
                "Lorenzo Arenas", "CONCEPCIÓN",
                "Chiguayante", "Pedro Medina", "Manquimávida", "La Leonera", "Omer Huet",
                "Hualqui", "Quilacoya", "San Miguel", "Unihue", "Valle Chanco",
                "Los Acacios", "Talcamávida", "Gomero", "Buenuraqui", "San Rosendo", "Laja",
                "Juan Pablo II", "Diagonal Bio Bio", "Alborada", "Costa Mar", "El Parque",
                "Lomas Coloradas", "CRS Henríquez", "Hito Galvarino", "Los Canelos",
                "Huinca", "Cristo Redentor", "Laguna Quiñenco", "CORONEL",
            ]
            estaciones_en_datos = set(passes_df["station"].unique())
            estaciones_disponibles = [e for e in orden_estaciones if e in estaciones_en_datos]
            estaciones_disponibles += sorted(estaciones_en_datos - set(estaciones_disponibles))
            est_sel = st.selectbox("Estación", estaciones_disponibles, key="est_sel")
        with coc2:
            dia_est_label = st.radio(
                "Día", ["Lun-Jue", "Viernes", "Sábado", "Domingo"],
                horizontal=True, key="est_dia",
            )
        DIA_MAP_EST = {"Lun-Jue": "LJ", "Viernes": "V", "Sábado": "SAB", "Domingo": "DOM"}
        from components.viz_gantt import aplica_en_dia as _apl
        if "dia" in services_df.columns:
            mask = services_df.apply(
                lambda r: _apl(r, DIA_MAP_EST[dia_est_label]), axis=1)
            sv_dia = services_df[mask]
            pa_dia = passes_df[passes_df["service_id"].isin(sv_dia["id"])]
        else:
            sv_dia, pa_dia = services_df, passes_df
        render_station_occupancy(pa_dia, sv_dia, est_sel)


# -----------------------------------------------------------------------------
# Tab 7: Escenarios
# -----------------------------------------------------------------------------

with tab_esc:
    st.markdown("### Generador y comparador de escenarios")
    st.caption(
        "Construye escenarios alternativos densificando la oferta en super "
        "punta, valida la factibilidad con la flota disponible (15 SFE-100 + "
        "3 SFB) y compara los indicadores contra el itinerario base."
    )
    render_escenarios(services_df, passes_df)


# -----------------------------------------------------------------------------
# Tab 8: Catálogo de servicios (tabla)
# -----------------------------------------------------------------------------

with tab_svc:
    st.markdown("### Catálogo de servicios del itinerario")
    render_services_table(services_df)


# Footer
st.markdown("---")
st.caption(
    "Biotren Itinerario Studio · MVP · Datos: itinerario GOF-S 2/419 · "
    "Construido con Streamlit + Plotly + Folium."
)
