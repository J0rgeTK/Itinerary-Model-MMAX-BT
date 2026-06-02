"""
Generador y comparador de escenarios de itinerario.

Permite construir escenarios alternativos densificando la oferta en las
ventanas de super punta, validar la factibilidad con la flota disponible
(15 SFE-100 + 3 SFB) respetando que solo los SFB cubren servicios hacia/desde
Laja, y comparar los KPIs contra el itinerario base.

NOTA METODOLÓGICA
-----------------
Esto NO es un optimizador MILP. Es un generador paramétrico: el planificador
propone una hipótesis (p. ej. bajar el headway de super punta) y el sistema
genera el itinerario candidato, asigna material rodante con una heurística
greedy y reporta si la flota alcanza. Es la herramienta correcta para
explorar escenarios y previa a una optimización formal.
"""

from __future__ import annotations

import pandas as pd
import streamlit as st

from components.viz_marey import render_marey
from components.viz_kpis import es_pasajero


# --- Parámetros del modelo -------------------------------------------------

INVERSION_S = 6 * 60          # tiempo mínimo de inversión en terminal (6 min)
N_SFE100 = 15                 # flota de unidades SFE-100
N_SFB = 3                     # flota de unidades SFB (serie 200, únicas a Laja)
CAP_TREN_DEFAULT = 250        # plazas por tren (estimación, configurable)


def _fmt(s):
    if s is None or pd.isna(s):
        return ""
    s = int(s)
    return f"{s // 3600:02d}:{(s % 3600) // 60:02d}"


# ---------------------------------------------------------------------------
# Identificación de servicios que requieren SFB
# ---------------------------------------------------------------------------

def servicios_que_tocan_laja(passes_df: pd.DataFrame) -> set[str]:
    """IDs de servicios cuyo recorrido incluye la estación Laja."""
    return set(passes_df[passes_df["station"] == "Laja"]["service_id"].unique())


# ---------------------------------------------------------------------------
# Perfil de tiempos de un servicio (para clonar servicios nuevos)
# ---------------------------------------------------------------------------

def _perfil(passes_df: pd.DataFrame, service_id: str, h_salida: int) -> list[dict]:
    """Devuelve la lista de paradas de un servicio como offsets respecto a la salida."""
    sp = passes_df[passes_df["service_id"] == service_id].sort_values("orden")
    perfil = []
    for _, p in sp.iterrows():
        perfil.append({
            "station": p["station"],
            "off_llega": (p["llega"] - h_salida) if pd.notna(p["llega"]) else None,
            "off_sale": (p["sale"] - h_salida) if pd.notna(p["sale"]) else None,
            "dist_km": p["dist_km"],
        })
    return perfil


def _servicio_representativo(services_df: pd.DataFrame, passes_df: pd.DataFrame,
                             linea: str, sentido: str, bucle: bool) -> str | None:
    """Elige un servicio base típico para clonar su perfil de tiempos."""
    cand = services_df[(services_df["linea"] == linea) &
                       (services_df["sentido"] == sentido) &
                       (services_df["tipo"].apply(es_pasajero))]
    if cand.empty:
        return None
    if bucle:
        bucles = cand[cand["tipo"].str.contains("BUCLE", na=False)]
        if not bucles.empty:
            cand = bucles
    # Preferir un servicio cuya duración sea la mediana (perfil típico)
    cand = cand.assign(_dur=cand["h_llegada"] - cand["h_salida"])
    cand = cand.sort_values("_dur")
    return cand.iloc[len(cand) // 2]["id"]


# ---------------------------------------------------------------------------
# Generación de un escenario
# ---------------------------------------------------------------------------

def generar_escenario(services_df: pd.DataFrame, passes_df: pd.DataFrame,
                      config: dict) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Construye services_df y passes_df de un escenario densificado.

    config = {
        "L2": [{"t0": 7.0, "t1": 9.0, "headway_min": 8, "sentidos": [...]}, ...],
        "L1": [...],
    }
    Cada bloque reemplaza los servicios de pasajeros base de esa ventana/línea
    por una grilla regular con el headway objetivo.
    """
    laja_ids = servicios_que_tocan_laja(passes_df)

    nuevos_servicios: list[dict] = []
    nuevos_passes: list[dict] = []
    contador = [0]

    def emitir(linea, sentido, tipo, h_salida, perfil):
        contador[0] += 1
        sid = f"E{contador[0]:03d}"
        passes_srv = []
        h_lleg_final = h_salida
        for p in perfil:
            llega = (h_salida + p["off_llega"]) if p["off_llega"] is not None else None
            sale = (h_salida + p["off_sale"]) if p["off_sale"] is not None else None
            if llega is not None:
                h_lleg_final = llega
            passes_srv.append({
                "service_id": sid, "orden": len(passes_srv) + 1,
                "estacion": p["station"], "llega": llega, "sale": sale,
                "dist_km": p["dist_km"],
            })
        nuevos_passes.extend(passes_srv)
        origen = perfil[0]["station"]
        destino = perfil[-1]["station"]
        nuevos_servicios.append({
            "id": sid, "linea": linea, "sentido": sentido, "tren": "",
            "tipo": tipo, "origen": origen, "destino": destino,
            "h_salida": h_salida, "h_llegada": h_lleg_final,
            "duracion_min": round((h_lleg_final - h_salida) / 60, 1),
        })

    # Marcar qué servicios base caen dentro de ventanas densificadas (se reemplazan)
    reemplazados: set[str] = set()

    for linea, bloques in config.items():
        for bloque in bloques:
            t0 = bloque["t0"] * 3600
            t1 = bloque["t1"] * 3600
            headway = bloque["headway_min"] * 60
            sentidos = bloque["sentidos"]
            for sentido in sentidos:
                # Servicios base de pasajeros en esta ventana → se reemplazan
                base_franja = services_df[
                    (services_df["linea"] == linea) &
                    (services_df["sentido"] == sentido) &
                    (services_df["tipo"].apply(es_pasajero)) &
                    (services_df["h_salida"].between(t0, t1))
                ]
                reemplazados.update(base_franja["id"].tolist())

                # Servicio representativo: en L1 super punta usar bucle (no Laja)
                usar_bucle = (linea == "L1")
                rep_id = _servicio_representativo(services_df, passes_df,
                                                  linea, sentido, usar_bucle)
                if rep_id is None:
                    continue
                rep = services_df[services_df["id"] == rep_id].iloc[0]
                perfil = _perfil(passes_df, rep_id, rep["h_salida"])
                tipo_nuevo = rep["tipo"]

                # Grilla regular de salidas
                h = t0
                while h <= t1:
                    emitir(linea, sentido, tipo_nuevo, int(h), perfil)
                    h += headway

    # Construir el escenario: base no reemplazado + nuevos
    base_keep = services_df[~services_df["id"].isin(reemplazados)].copy()
    passes_keep = passes_df[~passes_df["service_id"].isin(reemplazados)].copy()

    esc_services = pd.concat([base_keep, pd.DataFrame(nuevos_servicios)],
                             ignore_index=True)
    esc_passes = pd.concat([passes_keep, pd.DataFrame(nuevos_passes)],
                           ignore_index=True)

    # Recalcular columnas derivadas de horas
    base_date = "2026-04-20"

    def to_dt(s):
        if pd.isna(s):
            return pd.NaT
        return pd.Timestamp(base_date) + pd.Timedelta(seconds=int(s))

    esc_services["h_salida_str"] = esc_services["h_salida"].apply(_fmt)
    esc_services["h_llegada_str"] = esc_services["h_llegada"].apply(_fmt)
    esc_services["h_salida_dt"] = esc_services["h_salida"].apply(to_dt)
    esc_services["h_llegada_dt"] = esc_services["h_llegada"].apply(to_dt)
    esc_services["es_carga"] = esc_services["tipo"].fillna("").str.upper().str.contains("CARGA")

    esc_passes["llega_dt"] = esc_passes["llega"].apply(to_dt)
    esc_passes["sale_dt"] = esc_passes["sale"].apply(to_dt)
    # asegurar columnas de metadatos en passes
    meta = esc_services.set_index("id")[["linea", "sentido", "tren", "tipo"]]
    for col in ["linea", "sentido", "tren", "tipo"]:
        esc_passes[col] = esc_passes["service_id"].map(meta[col])
    # marca de carga para que el Marey del escenario distinga los canales
    esc_passes["es_carga"] = esc_passes["tipo"].fillna("").str.upper().str.contains("CARGA")

    return esc_services, esc_passes


# ---------------------------------------------------------------------------
# Dimensionamiento de flota (concurrencia pico por tipo)
# ---------------------------------------------------------------------------

def asignar_flota(services_df: pd.DataFrame, passes_df: pd.DataFrame) -> dict:
    """Estima los trenes mínimos requeridos por tipo.

    Usa la concurrencia pico: el máximo de servicios del mismo grupo que se
    solapan en el tiempo. Es la cota inferior exacta de la flota necesaria
    (en el instante pico hay esa cantidad de trenes circulando a la vez) y la
    métrica correcta para evaluar factibilidad:
      - Servicios que tocan Laja  → requieren unidades SFB
      - El resto                  → requieren unidades SFE-100

    Se mide el solape puro de los intervalos [salida, llegada]; la flota real
    será algo mayor por reposicionamientos e inversiones, pero esta cota es
    un piso exacto: si supera la flota disponible, el escenario es inviable.
    """
    laja_ids = servicios_que_tocan_laja(passes_df)
    svc = services_df.dropna(subset=["h_salida", "h_llegada"]).copy()
    # Excluir canales de carga: usan material rodante del operador de carga,
    # no la flota SFE-100 / SFB de Biotren.
    if "tipo" in svc.columns:
        svc = svc[~svc["tipo"].str.upper().str.contains("CARGA", na=False)]

    def pico(df_grupo: pd.DataFrame) -> int:
        eventos: list[tuple[float, int]] = []
        for _, s in df_grupo.iterrows():
            eventos.append((s["h_salida"], 1))
            eventos.append((s["h_llegada"], -1))
        eventos.sort()
        mx = cur = 0
        for _, d in eventos:
            cur += d
            mx = max(mx, cur)
        return mx

    svc_sfb = svc[svc["id"].isin(laja_ids)]
    svc_sfe = svc[~svc["id"].isin(laja_ids)]

    n_sfb = pico(svc_sfb)
    n_sfe = pico(svc_sfe)

    return {
        "n_sfe100": n_sfe,
        "n_sfb": n_sfb,
        "factible": n_sfe <= N_SFE100 and n_sfb <= N_SFB,
        "deficit_sfe100": max(0, n_sfe - N_SFE100),
        "deficit_sfb": max(0, n_sfb - N_SFB),
    }


# ---------------------------------------------------------------------------
# Evaluación de un escenario
# ---------------------------------------------------------------------------

def evaluar(services_df: pd.DataFrame, passes_df: pd.DataFrame,
            cap_tren: int = CAP_TREN_DEFAULT) -> dict:
    """Calcula los KPIs de un itinerario (base o escenario)."""
    pax = services_df[services_df["tipo"].apply(es_pasajero)]
    flota = asignar_flota(services_df, passes_df)

    # Plazas-km: nº servicios × capacidad × distancia recorrida
    plazas_km = 0.0
    for _, s in pax.iterrows():
        sp = passes_df[passes_df["service_id"] == s["id"]]
        if not sp.empty:
            dist = sp["dist_km"].max() - sp["dist_km"].min()
            plazas_km += cap_tren * abs(dist)

    # Headway medio en super punta (7:00-9:00 y 17:30-19:30) por línea
    def headway_sp(linea):
        hs = []
        for sentido in pax[pax["linea"] == linea]["sentido"].unique():
            for t0, t1 in [(7 * 3600, 9 * 3600), (17.5 * 3600, 19.5 * 3600)]:
                sub = pax[(pax["linea"] == linea) & (pax["sentido"] == sentido) &
                          (pax["h_salida"].between(t0, t1))].sort_values("h_salida")
                if len(sub) > 1:
                    difs = sub["h_salida"].diff().dropna() / 60
                    hs.extend(difs.tolist())
        return sum(hs) / len(hs) if hs else None

    return {
        "n_pasajeros": len(pax),
        "n_total": len(services_df),
        "plazas_km": plazas_km,
        "n_sfe100": flota["n_sfe100"],
        "n_sfb": flota["n_sfb"],
        "factible": flota["factible"],
        "deficit_sfe100": flota["deficit_sfe100"],
        "deficit_sfb": flota["deficit_sfb"],
        "headway_sp_l1": headway_sp("L1"),
        "headway_sp_l2": headway_sp("L2"),
    }


# ---------------------------------------------------------------------------
# Componente de visualización
# ---------------------------------------------------------------------------

def render_escenarios(services_df: pd.DataFrame, passes_df: pd.DataFrame) -> None:
    """Pestaña de generación y comparación de escenarios."""

    st.info(
        "**Cómo funciona** · Define una hipótesis de densificación para las "
        "ventanas de super punta. El sistema genera el itinerario candidato, "
        "le asigna material rodante respetando que solo los 3 trenes SFB "
        "cubren servicios a Laja, y reporta si la flota alcanza. No es un "
        "optimizador automático: es un generador para explorar escenarios.",
        icon="🔬",
    )

    # ----- Configuración -----
    st.markdown("##### Configuración del escenario")

    col_l2, col_l1 = st.columns(2)

    with col_l2:
        st.markdown("**Línea 2 (Concepción – Coronel)**")
        l2_sp_m = st.checkbox("Densificar super punta mañana", value=True, key="l2spm")
        l2_hw_m = st.slider("Headway objetivo PM mañana (min)", 4, 20, 8,
                            key="l2hwm", disabled=not l2_sp_m)
        l2_sp_t = st.checkbox("Densificar super punta tarde", value=True, key="l2spt")
        l2_hw_t = st.slider("Headway objetivo PT tarde (min)", 4, 20, 8,
                            key="l2hwt", disabled=not l2_sp_t)

    with col_l1:
        st.markdown("**Línea 1 (servicios bucle Concepción – Hualqui)**")
        l1_sp_m = st.checkbox("Densificar super punta mañana", value=True, key="l1spm")
        l1_hw_m = st.slider("Headway objetivo PM mañana (min)", 8, 30, 15,
                            key="l1hwm", disabled=not l1_sp_m)
        l1_sp_t = st.checkbox("Densificar super punta tarde", value=True, key="l1spt")
        l1_hw_t = st.slider("Headway objetivo PT tarde (min)", 8, 30, 15,
                            key="l1hwt", disabled=not l1_sp_t)

    cap_tren = st.number_input("Capacidad por tren (plazas)", 100, 500,
                               CAP_TREN_DEFAULT, step=10,
                               help="Estimación de plazas por unidad (sentadas + pie)")

    # ----- Generar (al pulsar el botón) -----
    if st.button("⚙️ Generar y evaluar escenario", type="primary"):
        config: dict[str, list] = {"L1": [], "L2": []}
        if l2_sp_m:
            config["L2"].append({"t0": 7.0, "t1": 9.0, "headway_min": l2_hw_m,
                                 "sentidos": ["CC→CW", "CW→CC"]})
        if l2_sp_t:
            config["L2"].append({"t0": 17.5, "t1": 19.5, "headway_min": l2_hw_t,
                                 "sentidos": ["CC→CW", "CW→CC"]})
        if l1_sp_m:
            config["L1"].append({"t0": 6.5, "t1": 8.5, "headway_min": l1_hw_m,
                                 "sentidos": ["LJ→TH", "TH→LJ"]})
        if l1_sp_t:
            config["L1"].append({"t0": 17.5, "t1": 19.5, "headway_min": l1_hw_t,
                                 "sentidos": ["LJ→TH", "TH→LJ"]})

        with st.spinner("Generando itinerario y asignando material rodante…"):
            esc_services, esc_passes = generar_escenario(services_df, passes_df, config)
            kpi_base = evaluar(services_df, passes_df, cap_tren)
            kpi_esc = evaluar(esc_services, esc_passes, cap_tren)

        # Guardar en session_state para que los resultados PERSISTAN entre
        # interacciones (cualquier widget provoca un rerun en Streamlit).
        st.session_state["escenario_resultado"] = {
            "esc_services": esc_services,
            "esc_passes": esc_passes,
            "kpi_base": kpi_base,
            "kpi_esc": kpi_esc,
        }

    # ----- Renderizar resultados (desde session_state) -----
    if "escenario_resultado" not in st.session_state:
        st.caption("Ajusta los parámetros y pulsa el botón para generar el escenario.")
        return

    data = st.session_state["escenario_resultado"]
    esc_services = data["esc_services"]
    esc_passes = data["esc_passes"]
    kpi_base = data["kpi_base"]
    kpi_esc = data["kpi_esc"]

    # ----- Veredicto de factibilidad -----
    st.markdown("##### Resultado")
    if kpi_esc["factible"]:
        st.success(
            f"✅ **Escenario FACTIBLE con la flota actual.** "
            f"Requiere {kpi_esc['n_sfe100']} SFE-100 (de 15 disponibles) y "
            f"{kpi_esc['n_sfb']} SFB (de 3 disponibles).",
            icon="✅",
        )
    else:
        faltan = []
        if kpi_esc["deficit_sfe100"] > 0:
            faltan.append(f"{kpi_esc['deficit_sfe100']} SFE-100")
        if kpi_esc["deficit_sfb"] > 0:
            faltan.append(f"{kpi_esc['deficit_sfb']} SFB")
        st.error(
            f"⚠️ **Escenario NO factible con la flota actual.** "
            f"Faltarían: {', '.join(faltan)}. "
            f"Requiere {kpi_esc['n_sfe100']} SFE-100 y {kpi_esc['n_sfb']} SFB.",
            icon="⚠️",
        )

    # ----- Tabla comparativa -----
    st.markdown("##### Comparación Base vs Escenario")

    def delta_str(base, esc, fmt="{:.0f}"):
        if base is None or esc is None:
            return "—"
        d = esc - base
        signo = "+" if d > 0 else ""
        return f"{signo}{fmt.format(d)}"

    filas = [
        ("Servicios de pasajeros", f"{kpi_base['n_pasajeros']}",
         f"{kpi_esc['n_pasajeros']}",
         delta_str(kpi_base['n_pasajeros'], kpi_esc['n_pasajeros'])),
        ("Servicios totales", f"{kpi_base['n_total']}", f"{kpi_esc['n_total']}",
         delta_str(kpi_base['n_total'], kpi_esc['n_total'])),
        ("Plazas-km ofertadas", f"{kpi_base['plazas_km']:,.0f}",
         f"{kpi_esc['plazas_km']:,.0f}",
         delta_str(kpi_base['plazas_km'], kpi_esc['plazas_km'], "{:,.0f}")),
        ("Headway medio super punta L2 (min)",
         f"{kpi_base['headway_sp_l2']:.1f}" if kpi_base['headway_sp_l2'] else "—",
         f"{kpi_esc['headway_sp_l2']:.1f}" if kpi_esc['headway_sp_l2'] else "—",
         delta_str(kpi_base['headway_sp_l2'], kpi_esc['headway_sp_l2'], "{:.1f}")),
        ("Headway medio super punta L1 (min)",
         f"{kpi_base['headway_sp_l1']:.1f}" if kpi_base['headway_sp_l1'] else "—",
         f"{kpi_esc['headway_sp_l1']:.1f}" if kpi_esc['headway_sp_l1'] else "—",
         delta_str(kpi_base['headway_sp_l1'], kpi_esc['headway_sp_l1'], "{:.1f}")),
        ("Trenes SFE-100 (concurrencia pico)", f"{kpi_base['n_sfe100']} / 15",
         f"{kpi_esc['n_sfe100']} / 15",
         delta_str(kpi_base['n_sfe100'], kpi_esc['n_sfe100'])),
        ("Trenes SFB (concurrencia pico)", f"{kpi_base['n_sfb']} / 3",
         f"{kpi_esc['n_sfb']} / 3",
         delta_str(kpi_base['n_sfb'], kpi_esc['n_sfb'])),
    ]
    comp_df = pd.DataFrame(filas, columns=["Indicador", "Base", "Escenario", "Δ"])
    st.dataframe(comp_df, use_container_width=True, hide_index=True)

    # Mejora porcentual de oferta
    if kpi_base["plazas_km"] > 0:
        mejora = (kpi_esc["plazas_km"] / kpi_base["plazas_km"] - 1) * 100
        c1, c2, c3 = st.columns(3)
        c1.metric("Aumento de oferta", f"{mejora:+.1f}%",
                  help="Variación de plazas-km ofertadas")
        c2.metric("Servicios adicionales",
                  f"+{kpi_esc['n_pasajeros'] - kpi_base['n_pasajeros']}")
        nuevos_trenes = (kpi_esc['n_sfe100'] - kpi_base['n_sfe100']) + \
                        (kpi_esc['n_sfb'] - kpi_base['n_sfb'])
        c3.metric("Trenes adicionales necesarios", f"{nuevos_trenes:+d}")

    # ----- Marey del escenario: AMBAS LÍNEAS -----
    st.markdown("##### Diagrama de Marey del escenario")
    st.caption("Itinerario resultante del escenario para ambas líneas. "
               "Compara la densidad de líneas con el itinerario base.")

    st.markdown("**Línea 2 — Concepción ↔ Coronel**")
    fig_l2 = render_marey(esc_passes, linea="L2", sentidos=["CC→CW", "CW→CC"],
                          color_dim="sentido", t_min_h=5.0, t_max_h=23.0)
    st.plotly_chart(fig_l2, use_container_width=True, key="esc_marey_l2")

    st.markdown("**Línea 1 — Laja ↔ Talcahuano**")
    fig_l1 = render_marey(esc_passes, linea="L1", sentidos=["LJ→TH", "TH→LJ"],
                          color_dim="sentido", t_min_h=5.0, t_max_h=23.0)
    st.plotly_chart(fig_l1, use_container_width=True, key="esc_marey_l1")

    # ----- Exportar -----
    st.markdown("##### Exportar escenario")
    export_cols = ["id", "linea", "sentido", "tren", "tipo", "origen", "destino",
                   "h_salida_str", "h_llegada_str", "duracion_min"]
    csv = esc_services[export_cols].to_csv(index=False).encode("utf-8")
    st.download_button("Descargar servicios del escenario (CSV)", data=csv,
                       file_name="escenario_biotren.csv", mime="text/csv")

    if st.button("🗑️ Limpiar escenario"):
        del st.session_state["escenario_resultado"]
        st.rerun()

    with st.expander("Notas metodológicas del cálculo"):
        st.markdown("""
- **Generación**: en cada ventana de super punta se reemplazan los servicios
  de pasajeros base por una grilla regular con el headway objetivo. Cada
  servicio nuevo clona el perfil de velocidad y detenciones de un servicio
  base representativo (en L1, un servicio bucle Concepción–Hualqui).
- **Dimensionamiento de flota**: se mide la **concurrencia pico** por tipo de
  tren — el máximo de servicios que se solapan en el tiempo. Es la cota
  inferior exacta: en el instante pico hay esa cantidad de trenes circulando
  a la vez, así que se necesitan al menos esos. Los servicios que tocan Laja
  exigen unidades **SFB**; el resto, **SFE-100**.
- **Factibilidad**: el escenario es factible si la concurrencia pico es
  ≤ 15 SFE-100 y ≤ 3 SFB. La flota real necesaria será algo mayor por
  reposicionamientos e inversiones; esta cota sirve para descartar de forma
  segura los escenarios inviables.
- **Limitación**: el modelo no incluye reposicionamientos por equipo vacío ni
  capacidad fina de andenes. Es una estimación de primer orden, suficiente
  para explorar escenarios y dimensionar necesidades de flota.
        """)
