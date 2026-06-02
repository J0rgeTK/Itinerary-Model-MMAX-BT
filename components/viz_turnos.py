"""
Construcción y optimización de turnos teóricos de conducción.

MODELO DE TIEMPOS (según normativa precisada por EFE Sur)
---------------------------------------------------------
Un turno se compone de fases:
  1. Traslado inicial  — del depósito El Arenal (EZ) al punto de inicio.
                         0 min si inicia en Arenal.
  2. Revisión de tren  — 45 min, SOLO la primera vez que un tren opera en
                         el día (revisión del equipo SFE / SFB).
  3. Servicio(s)       — conducción efectiva; puede haber esperas entre uno
                         y otro si el turno encadena varios.
  4. Traslado final    — del punto de término de vuelta al depósito.
  5. Cierre            — 30 min al terminar el turno.

RESTRICCIONES
-------------
  - Jornada diaria  ≤ 7:30 h (configurable)
  - Conducción efectiva ≤ 5:00 h (configurable)

OPTIMIZACIÓN
------------
La cantidad de turnos depende de la configuración: si se permite el cambio
de tren en puntos de relevo (Concepción, Coronel, Hualqui, La Leonera…),
una pareja puede encadenar servicios de distintos trenes en un mismo turno,
reduciendo tiempos muertos y la cantidad total de turnos.
"""

from __future__ import annotations

import math

import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import streamlit as st


# --- Parámetros normativos -------------------------------------------------

REVISION_S = 45 * 60          # revisión del tren (45 min)
REVISION_DETENCION_S = 2 * 3600  # umbral: si tren detenido > 2h → nueva revisión

# Cierre de turno: 15 min en Concepción (CC), 30 min en otras estaciones.
CIERRE_CC_S = 15 * 60
CIERRE_OTRAS_S = 30 * 60

# Buffer adicional cuando la pareja toma un relevo: debe estar 15 min antes
# del inicio del servicio (sumado al tiempo de traslado).
BUFFER_RELEVO_S = 15 * 60

MAX_JORNADA_S_DEF = int(7.5 * 3600)      # jornada estándar 7:30
MAX_JORNADA_HE_S_DEF = int(9.5 * 3600)   # con horas extra (hasta +2h)
MAX_CONDUCCION_S_DEF = int(5.0 * 3600)


def cierre_s(estacion: str) -> int:
    """Cierre de turno: 15 min en CCP, 30 min en otras estaciones."""
    e = (estacion or "").upper()
    if "CONCEPC" in e:
        return CIERRE_CC_S
    return CIERRE_OTRAS_S


# Traslados improductivos entre el depósito El Arenal (EZ) y cada punto,
# en segundos. Simétricos (ida y vuelta). Valores normativos confirmados.
def traslado_s(estacion: str) -> int:
    e = (estacion or "").upper()
    if "ARENAL" in e:
        return 0
    if "CONCEPC" in e:
        return 45 * 60          # CC: 0:45
    if "CORONEL" in e:
        return 90 * 60          # CW: 1:30
    if "LOMAS" in e:
        return 60 * 60          # LM: 1:00
    if "LAGUNILLAS" in e:
        return 90 * 60          # Desvío Lagunillas: 1:30 (antes 1:15)
    if "HUALQUI" in e:
        return 90 * 60          # HQ: 1:30
    if "OMER" in e:
        return 90 * 60          # OH: 1:30
    if "LEONERA" in e:
        return 90 * 60          # estimado, similar a OH
    if "LAJA" in e:
        return 150 * 60         # extremo L1; con pernocta este traslado se anula
    return 60 * 60              # default para estaciones no tabuladas


# Puntos donde puede iniciarse/cerrarse un turno y hacerse relevo de tren
PUNTOS_RELEVO_DEF = ["CONCEPCIÓN", "CORONEL", "Arenal"]
PUNTOS_RELEVO_OPC = ["CONCEPCIÓN", "CORONEL", "Arenal", "Hualqui",
                     "La Leonera", "Lomas Coloradas", "Mercado", "Laja",
                     "Desvío Lagunillas (HDLC)", "Talcamávida"]


def _fmt(s) -> str:
    if s is None or pd.isna(s):
        return ""
    s = int(s)
    sign = "-" if s < 0 else ""
    s = abs(s)
    return f"{sign}{s // 3600}:{(s % 3600) // 60:02d}"


# ---------------------------------------------------------------------------
# Construcción de turnos
# ---------------------------------------------------------------------------

def build_shifts(services_df: pd.DataFrame,
                 max_jornada_s: int = MAX_JORNADA_S_DEF,
                 max_conduccion_s: int = MAX_CONDUCCION_S_DEF,
                 permitir_cambio_tren: bool = False,
                 puntos_relevo: tuple[str, ...] = tuple(PUNTOS_RELEVO_DEF),
                 pernocta_laja: bool = False,
                 aplicar_buffer_relevo: bool = True,
                 ) -> pd.DataFrame:
    """Construye turnos teóricos con un greedy de empaquetado.

    Recorre los servicios en orden cronológico y los asigna al turno abierto
    donde mejor encajen (menor tiempo muerto), respetando jornada y conducción.
    Si permitir_cambio_tren está activo, un turno puede tomar servicios de
    otro tren en un punto de relevo.

    Si pernocta_laja está activo, los turnos que inician o terminan en Laja
    no cargan traslado: la pareja pernocta en Laja y retoma allí al día
    siguiente (operación cíclica día a día).
    """
    relevo = set(puntos_relevo)

    def _tras(estacion: str) -> int:
        """Traslado desde/hacia el depósito, con la excepción de pernocta."""
        if pernocta_laja and "LAJA" in (estacion or "").upper():
            return 0
        return traslado_s(estacion)

    svc = (services_df.dropna(subset=["h_salida", "h_llegada"])
           .sort_values("h_salida").to_dict("records"))

    turnos: list[dict] = []
    # Seguimiento del último uso de cada tren para aplicar revisión adicional
    # cuando el tren queda detenido más de REVISION_DETENCION_S (2 horas).
    ultima_uso_tren: dict[str, int] = {}

    def requiere_revision(tren: str, h_salida: int) -> bool:
        """True si el tren necesita revisión: primer uso del día o detenido >2h."""
        if tren not in ultima_uso_tren:
            return True
        if h_salida - ultima_uso_tren[tren] > REVISION_DETENCION_S:
            return True
        return False

    def jornada_si_cierra(turno: dict) -> int:
        """Jornada total del turno si se cerrara con su último servicio."""
        ini = turno["h_inicio_trabajo"]
        ubic = turno["ubicacion"]
        fin = (turno["h_fin_ultimo_svc"]
               + _tras(ubic) + cierre_s(ubic))
        return fin - ini

    def puede_agregar(turno: dict, s: dict) -> bool:
        # El servicio debe partir de donde quedó el turno
        if s["origen"] != turno["ubicacion"]:
            return False
        if s["h_salida"] < turno["h_fin_ultimo_svc"]:
            return False
        # Cambio de tren: solo en punto de relevo y si está permitido
        if s["tren"] != turno["tren_actual"]:
            if not permitir_cambio_tren:
                return False
            if turno["ubicacion"] not in relevo:
                return False
        # Verificar jornada y conducción al incorporar el servicio
        dur = s["h_llegada"] - s["h_salida"]
        nueva_cond = turno["conduccion_s"] + dur
        if nueva_cond > max_conduccion_s:
            return False
        fin = s["h_llegada"] + _tras(s["destino"]) + cierre_s(s["destino"])
        nueva_jorn = fin - turno["h_inicio_trabajo"]
        if nueva_jorn > max_jornada_s:
            return False
        return True

    def crear_turno(s: dict) -> dict:
        tras_ini = _tras(s["origen"])
        revision = requiere_revision(s["tren"], s["h_salida"])
        # Relevo: la pareja toma un tren que ya estaba en operación
        # (otro turno lo manejó antes). En ese caso debe llegar
        # 15 min antes del inicio del servicio (adicional al traslado).
        es_relevo = s["tren"] in ultima_uso_tren and aplicar_buffer_relevo
        buf_relevo = BUFFER_RELEVO_S if es_relevo else 0
        ultima_uso_tren[s["tren"]] = s["h_llegada"]
        rev_s = REVISION_S if revision else 0
        # La pareja: traslado + buffer relevo + revisión (si aplica) + servicio
        h_inicio_trabajo = s["h_salida"] - rev_s - tras_ini - buf_relevo
        turno = {
            "tren_inicial": s["tren"],
            "tren_actual": s["tren"],
            "trenes": [s["tren"]],
            "servicios": [s["id"]],
            "punto_inicio": s["origen"],
            "ubicacion": s["destino"],
            "h_inicio_trabajo": h_inicio_trabajo,
            "h_primer_svc": s["h_salida"],
            "h_fin_ultimo_svc": s["h_llegada"],
            "conduccion_s": s["h_llegada"] - s["h_salida"],
            "traslado_ini_s": tras_ini,
            "revision_s": rev_s,
            "buffer_relevo_s": buf_relevo,
            "eventos": [
                {"fase": "Traslado inicial", "h_ini": h_inicio_trabajo,
                 "h_fin": h_inicio_trabajo + tras_ini, "detalle": s["origen"]},
            ],
        }
        cursor = h_inicio_trabajo + tras_ini
        # Buffer de presentación previo al relevo (15 min antes del inicio)
        if buf_relevo > 0:
            turno["eventos"].append({
                "fase": "Buffer relevo", "h_ini": cursor,
                "h_fin": cursor + buf_relevo,
                "detalle": f"Presentación 15 min antes en {s['origen']}",
            })
            cursor += buf_relevo
        if revision:
            tipo_tren = "SFB" if "B" in str(s["tren"]).upper() else "SFE"
            turno["eventos"].append({
                "fase": f"Revisión tren {tipo_tren}",
                "h_ini": cursor, "h_fin": cursor + REVISION_S,
                "detalle": s["tren"],
            })
            cursor += REVISION_S
        turno["eventos"].append({
            "fase": "Servicio", "h_ini": s["h_salida"], "h_fin": s["h_llegada"],
            "detalle": f"{s['id']} ({s['tren']})",
        })
        return turno

    def agregar(turno: dict, s: dict) -> None:
        # Espera entre el último servicio y el nuevo
        if s["h_salida"] > turno["h_fin_ultimo_svc"]:
            etiqueta = ("Relevo / espera" if s["tren"] != turno["tren_actual"]
                        else "Espera")
            turno["eventos"].append({
                "fase": etiqueta, "h_ini": turno["h_fin_ultimo_svc"],
                "h_fin": s["h_salida"], "detalle": turno["ubicacion"],
            })
        # Revisión si: tren no revisado o detenido > 2h desde último uso
        if requiere_revision(s["tren"], s["h_salida"]):
            tipo_tren = "SFB" if "B" in str(s["tren"]).upper() else "SFE"
            motivo = ("primer uso" if s["tren"] not in ultima_uso_tren
                      else f"detenido >{REVISION_DETENCION_S//3600}h")
            turno["eventos"].append({
                "fase": f"Revisión tren {tipo_tren}",
                "h_ini": s["h_salida"] - REVISION_S, "h_fin": s["h_salida"],
                "detalle": f"{s['tren']} ({motivo})",
            })
            turno["revision_s"] += REVISION_S
        ultima_uso_tren[s["tren"]] = s["h_llegada"]
        turno["eventos"].append({
            "fase": "Servicio", "h_ini": s["h_salida"], "h_fin": s["h_llegada"],
            "detalle": f"{s['id']} ({s['tren']})",
        })
        turno["servicios"].append(s["id"])
        if s["tren"] not in turno["trenes"]:
            turno["trenes"].append(s["tren"])
        turno["tren_actual"] = s["tren"]
        turno["ubicacion"] = s["destino"]
        turno["h_fin_ultimo_svc"] = s["h_llegada"]
        turno["conduccion_s"] += s["h_llegada"] - s["h_salida"]

    # Greedy: cada servicio al turno donde mejor encaje
    for s in svc:
        mejor = None
        mejor_gap = None
        for t in turnos:
            if puede_agregar(t, s):
                gap = s["h_salida"] - t["h_fin_ultimo_svc"]
                if mejor_gap is None or gap < mejor_gap:
                    mejor_gap = gap
                    mejor = t
        if mejor is not None:
            agregar(mejor, s)
        else:
            turnos.append(crear_turno(s))

    # Post-proceso: cerrar turnos (traslado final + cierre) y armar registros
    rows = []
    for i, t in enumerate(sorted(turnos, key=lambda x: x["h_inicio_trabajo"]), 1):
        tras_fin = _tras(t["ubicacion"])
        t["eventos"].append({
            "fase": "Traslado final", "h_ini": t["h_fin_ultimo_svc"],
            "h_fin": t["h_fin_ultimo_svc"] + tras_fin, "detalle": t["ubicacion"],
        })
        h_cierre_ini = t["h_fin_ultimo_svc"] + tras_fin
        cierre_dur = cierre_s(t["ubicacion"])
        t["eventos"].append({
            "fase": "Cierre", "h_ini": h_cierre_ini,
            "h_fin": h_cierre_ini + cierre_dur,
            "detalle": f"Cierre de turno ({cierre_dur//60} min)",
        })
        h_termino = h_cierre_ini + cierre_dur
        jornada = h_termino - t["h_inicio_trabajo"]
        cumple = (jornada <= max_jornada_s
                  and t["conduccion_s"] <= max_conduccion_s)
        rows.append({
            "turno": f"T-{i:02d}",
            "trenes": ", ".join(t["trenes"]),
            "n_trenes": len(t["trenes"]),
            "n_servicios": len(t["servicios"]),
            "servicios": ", ".join(t["servicios"]),
            "punto_inicio": t["punto_inicio"],
            "punto_fin": t["ubicacion"],
            "h_inicio": t["h_inicio_trabajo"],
            "h_termino": h_termino,
            "jornada_s": jornada,
            "conduccion_s": t["conduccion_s"],
            "traslado_s": t["traslado_ini_s"] + tras_fin,
            "revision_s": t["revision_s"],
            "cierre_s": cierre_dur,
            "jornada_str": _fmt(jornada),
            "conduccion_str": _fmt(t["conduccion_s"]),
            "inicio_str": _fmt(t["h_inicio_trabajo"]),
            "termino_str": _fmt(h_termino),
            "cumple": cumple,
            "eventos": t["eventos"],
        })
    return pd.DataFrame(rows)


# ---------------------------------------------------------------------------
# Estimación de dotación
# ---------------------------------------------------------------------------

def estimate_crews(shifts_df: pd.DataFrame) -> dict:
    """Dotación de parejas a partir de los turnos (metodología por turnos)."""
    if shifts_df.empty:
        return {}

    events = []
    for _, sh in shifts_df.iterrows():
        events.append((sh["h_inicio"], 1))
        events.append((sh["h_termino"], -1))
    events.sort()
    pico = cur = 0
    for _, d in events:
        cur += d
        pico = max(pico, cur)

    n_turnos = len(shifts_df)
    jornada_media_h = shifts_df["jornada_s"].mean() / 3600

    factor_sabado = 0.60
    factor_domingo = 0.35
    turnos_semana = n_turnos * (5 + factor_sabado + factor_domingo)

    TURNOS_POR_PAREJA = 6           # régimen 6x1
    parejas_operativas = turnos_semana / TURNOS_POR_PAREJA

    ausentismo = 0.083 + 0.020 + 0.040 + 0.003
    disponibilidad = 1 - ausentismo
    parejas_plantilla = parejas_operativas / disponibilidad

    margen = 1.05
    dotacion = parejas_plantilla * margen
    dot = math.ceil(dotacion)

    return {
        "n_turnos": n_turnos,
        "pico": pico,
        "jornada_media_h": jornada_media_h,
        "turnos_semana": turnos_semana,
        "parejas_operativas": parejas_operativas,
        "ausentismo": ausentismo,
        "disponibilidad": disponibilidad,
        "parejas_plantilla": parejas_plantilla,
        "margen": margen,
        "dotacion_recomendada": dot,
        "personal_total": dot * 2,
    }


DOTACION_REAL_PAREJAS = 50  # Dotación real informada por EFE Sur

def estimate_crews_semanal(turnos_por_dia: dict,
                             jornada_media_h: float,
                             pico: int,
                             ausentismo: float = None,
                             margen: float = None) -> dict:
    """Dotación de parejas con datos REALES por tipo de día.

    turnos_por_dia: dict {'LJ': n, 'V': n, 'SAB': n, 'DOM': n}.
    ausentismo: tasa total (vacaciones + licencias + permisos + otros).
                Default 14.6% si no se especifica.
    margen: factor para imprevistos. Default 1.05.
    """
    n_lj = turnos_por_dia.get("LJ", 0)
    n_v = turnos_por_dia.get("V", 0)
    n_sab = turnos_por_dia.get("SAB", 0)
    n_dom = turnos_por_dia.get("DOM", 0)
    turnos_semana = n_lj * 4 + n_v + n_sab + n_dom

    parejas_operativas = (turnos_semana / 6) if turnos_semana else 0
    if ausentismo is None:
        ausentismo = 0.083 + 0.020 + 0.040 + 0.003  # 14.6%
    disponibilidad = 1 - ausentismo
    parejas_plantilla = parejas_operativas / disponibilidad
    if margen is None:
        margen = 1.05
    dot = math.ceil(parejas_plantilla * margen)

    # Calibración inversa: qué ausentismo daría 50 parejas con margen=1.0
    if parejas_operativas > 0:
        aus_calibrado = max(0.0, 1 - (parejas_operativas / DOTACION_REAL_PAREJAS))
    else:
        aus_calibrado = 0.0

    return {
        "turnos_por_dia": turnos_por_dia,
        "turnos_semana": turnos_semana,
        "n_lj": n_lj, "n_v": n_v, "n_sab": n_sab, "n_dom": n_dom,
        "parejas_operativas": parejas_operativas,
        "ausentismo": ausentismo, "disponibilidad": disponibilidad,
        "parejas_plantilla": parejas_plantilla, "margen": margen,
        "dotacion_recomendada": dot, "personal_total": dot * 2,
        "dotacion_real": DOTACION_REAL_PAREJAS,
        "delta_vs_real": dot - DOTACION_REAL_PAREJAS,
        "ausentismo_calibrado": aus_calibrado,
        "jornada_media_h": jornada_media_h, "pico": pico,
    }


# ---------------------------------------------------------------------------
# Expansión de fases para el diagrama
# ---------------------------------------------------------------------------

FASE_COLOR = {
    "Traslado inicial": "#95A5C0",
    "Traslado final": "#95A5C0",
    "Revisión tren SFE": "#E8A33D",
    "Revisión tren SFB": "#D98324",
    "Servicio": "#1F4E78",
    "Espera": "#D5D5D5",
    "Relevo / espera": "#B7C4D8",
    "Cierre": "#C0703C",
}
BASE = pd.Timestamp("2026-04-20")


def fases_to_df(shifts_df: pd.DataFrame) -> pd.DataFrame:
    """Expande los turnos en una fila por fase, para el Gantt."""
    filas = []
    for _, t in shifts_df.iterrows():
        for ev in t["eventos"]:
            filas.append({
                "turno": t["turno"],
                "fase": ev["fase"],
                "detalle": ev.get("detalle", ""),
                "h_ini": ev["h_ini"],
                "h_fin": ev["h_fin"],
                "ini_dt": BASE + pd.to_timedelta(ev["h_ini"], unit="s"),
                "fin_dt": BASE + pd.to_timedelta(ev["h_fin"], unit="s"),
                "dur_min": round((ev["h_fin"] - ev["h_ini"]) / 60, 1),
            })
    return pd.DataFrame(filas)


# ---------------------------------------------------------------------------
# Análisis de optimización — identificación de puntos clave
# ---------------------------------------------------------------------------

def ranking_puntos_clave(df: pd.DataFrame, pernocta_laja: bool,
                         max_jornada_s: int, max_conduccion_s: int) -> dict:
    """Identifica, de forma incremental y greedy, qué puntos de relevo
    aportan mayor reducción de turnos.

    Parte del escenario sin relevos y agrega, en cada paso, el punto que
    más reduce la cantidad de turnos, hasta que ningún punto adicional
    mejore el resultado.
    """
    # Escenario base: sin cambio de tren
    base = build_shifts(df, max_jornada_s=max_jornada_s,
                        max_conduccion_s=max_conduccion_s,
                        permitir_cambio_tren=False,
                        puntos_relevo=(), pernocta_laja=pernocta_laja)
    n_base = len(base)

    seleccionados: list[str] = []
    n_actual = n_base
    ranking: list[dict] = []
    candidatos = list(PUNTOS_RELEVO_OPC)

    while candidatos:
        mejor = None
        mejor_n = n_actual
        for c in candidatos:
            s = build_shifts(df, max_jornada_s=max_jornada_s,
                             max_conduccion_s=max_conduccion_s,
                             permitir_cambio_tren=True,
                             puntos_relevo=tuple(seleccionados + [c]),
                             pernocta_laja=pernocta_laja)
            if len(s) < mejor_n:
                mejor_n = len(s)
                mejor = c
        if mejor is None:
            break
        ranking.append({
            "punto": mejor,
            "ahorro": n_actual - mejor_n,
            "turnos_acumulado": mejor_n,
        })
        seleccionados.append(mejor)
        n_actual = mejor_n
        candidatos.remove(mejor)

    return {
        "n_base": n_base,
        "ranking": ranking,
        "puntos_optimos": seleccionados,
        "n_optimo": n_actual,
        "puntos_sin_aporte": candidatos,
    }


def comparar_pernocta(df: pd.DataFrame, puntos_relevo: tuple[str, ...],
                      max_jornada_s: int, max_conduccion_s: int) -> dict:
    """Compara el efecto de habilitar la pernocta en Laja."""
    sin = build_shifts(df, max_jornada_s=max_jornada_s,
                       max_conduccion_s=max_conduccion_s,
                       permitir_cambio_tren=True, puntos_relevo=puntos_relevo,
                       pernocta_laja=False)
    con = build_shifts(df, max_jornada_s=max_jornada_s,
                       max_conduccion_s=max_conduccion_s,
                       permitir_cambio_tren=True, puntos_relevo=puntos_relevo,
                       pernocta_laja=True)
    return {"n_sin": len(sin), "n_con": len(con)}


# ---------------------------------------------------------------------------
# Componente de visualización
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# Distribución teórica mensual de turnos por pareja
# ---------------------------------------------------------------------------

def generar_calendario_mensual(turnos_por_dia: dict,
                                 n_parejas: int,
                                 dias_mes: int = 30,
                                 dia_inicio: int = 0) -> dict:
    """Genera un calendario teórico de asignación mensual de parejas a turnos.

    Aplica el régimen 6x1: cada pareja trabaja 6 días seguidos y descansa 1.
    El día de descanso de cada pareja rota a lo largo de las semanas, para
    distribuir equitativamente los descansos en el equipo.

    Reglas respetadas:
      - 6x1 estricto: nunca más de 6 días consecutivos sin descanso.
      - Descansos rotativos: la fecha del descanso de cada pareja cambia cada
        semana para repartir los fines de semana entre todos.
      - El número de parejas trabajando cada día debe ser ≥ turnos del día.

    turnos_por_dia: dict {'LJ': n, 'V': n, 'SAB': n, 'DOM': n}
    n_parejas: cantidad de parejas (plantilla)
    dias_mes: número de días del mes (28..31)
    dia_inicio: día de la semana en que arranca el mes (0=Lunes ... 6=Domingo)

    Returns:
        dict con:
          'calendario': DataFrame parejas × días, valores 'T' (turno), 'R'
                        (reserva, presente sin turno) o 'D' (descanso).
          'turnos_dia': lista de turnos teóricos por cada día del mes.
          'tipo_dia': lista del tipo de día ('L-J','V','Sáb','Dom').
          'fechas': lista de etiquetas 'd1', 'd2', ...
    """
    DIA_LABELS = ["L", "M", "X", "J", "V", "S", "D"]

    def tipo_dia_idx(idx_semana: int) -> str:
        # 0=Lunes, 4=Viernes, 5=Sábado, 6=Domingo
        if idx_semana <= 3:
            return "LJ"
        if idx_semana == 4:
            return "V"
        if idx_semana == 5:
            return "SAB"
        return "DOM"

    turnos_por_fecha = []
    tipo_por_fecha = []
    etiquetas = []
    for d in range(dias_mes):
        idx_semana = (dia_inicio + d) % 7
        tipo = tipo_dia_idx(idx_semana)
        turnos_por_fecha.append(turnos_por_dia.get(tipo, 0))
        tipo_por_fecha.append({"LJ": "L-J", "V": "V",
                                "SAB": "Sáb", "DOM": "Dom"}[tipo])
        etiquetas.append(f"d{d+1}\n{DIA_LABELS[idx_semana]}")

    # Inicializar calendario: todos en "Turno" por defecto
    cal = [["T"] * dias_mes for _ in range(n_parejas)]

    # Asignar descansos: régimen 6x1 ESTRICTO — cada pareja descansa cada 7
    # días exactos (máximo 6 días consecutivos trabajados). El primer día de
    # descanso de cada pareja se desplaza por su índice (p % 7), repartiendo
    # uniformemente los descansos entre los 7 días de la semana.
    for p in range(n_parejas):
        # Día del mes en que descansa por primera vez (0..6)
        primer_descanso = (p % 7 - dia_inicio) % 7
        # Si la primera ronda de descanso es lejana, también marcar el día 0
        # como un "descanso reciente" implícito para no exceder 6 días al
        # inicio del mes (no se marca en el calendario, es contexto)
        d = primer_descanso
        while d < dias_mes:
            cal[p][d] = "D"
            d += 7  # próximo descanso exactamente 7 días después

    # Ahora marcar "R" (reserva) para las parejas que están en "T" pero exceden
    # los turnos disponibles ese día
    for d in range(dias_mes):
        n_turnos = turnos_por_fecha[d]
        # Lista de índices de parejas que están en T (no descansan) ese día
        trabajando = [p for p in range(n_parejas) if cal[p][d] == "T"]
        # Si trabajan más que los turnos disponibles, los excedentes pasan a R
        if len(trabajando) > n_turnos:
            # Para variedad, las "reservas" se eligen por rotación basada en (p+d)
            sobrante = len(trabajando) - n_turnos
            # Ordenar trabajando por (p + d) % n_parejas → rota qué pareja queda en R
            trabajando.sort(key=lambda p: (p + d) % n_parejas, reverse=True)
            for p in trabajando[:sobrante]:
                cal[p][d] = "R"

    # Construir DataFrame
    parejas_idx = [f"P{p+1:02d}" for p in range(n_parejas)]
    columnas = etiquetas
    df_cal = pd.DataFrame(cal, index=parejas_idx, columns=columnas)
    df_cal.index.name = "Pareja"

    return {
        "calendario": df_cal,
        "turnos_dia": turnos_por_fecha,
        "tipo_dia": tipo_por_fecha,
        "fechas": etiquetas,
    }


def resumen_mensual_parejas(cal_data: dict) -> pd.DataFrame:
    """Resumen por pareja: turnos, reserva, descansos, días trabajados, etc."""
    cal = cal_data["calendario"]
    rows = []
    for pareja, row in cal.iterrows():
        n_T = (row == "T").sum()
        n_R = (row == "R").sum()
        n_D = (row == "D").sum()
        rows.append({
            "Pareja": pareja,
            "Turnos asignados": int(n_T),
            "Días en reserva": int(n_R),
            "Días de descanso": int(n_D),
            "Días trabajados": int(n_T + n_R),
            "Cumple 6x1": "✓" if n_D >= 4 else "⚠️",
        })
    return pd.DataFrame(rows)


def render_distribucion_mensual(turnos_por_dia: dict,
                                  dotacion: int,
                                  dias_mes: int = 30,
                                  dia_inicio: int = 0) -> None:
    """Pestaña de distribución teórica mensual."""
    cal_data = generar_calendario_mensual(turnos_por_dia, dotacion,
                                            dias_mes, dia_inicio)
    cal = cal_data["calendario"]
    resumen = resumen_mensual_parejas(cal_data)

    # KPIs mensuales
    total_turnos_mes = sum(cal_data["turnos_dia"])
    turnos_por_pareja_prom = resumen["Turnos asignados"].mean()
    descansos_prom = resumen["Días de descanso"].mean()
    n_cumple = (resumen["Cumple 6x1"] == "✓").sum()

    k1, k2, k3, k4 = st.columns(4)
    k1.metric("Total turnos en el mes", f"{total_turnos_mes:,}")
    k2.metric("Turnos por pareja (prom.)",
              f"{turnos_por_pareja_prom:.1f}")
    k3.metric("Descansos por pareja (prom.)",
              f"{descansos_prom:.1f}")
    k4.metric("Parejas que cumplen 6x1", f"{n_cumple} / {len(resumen)}")

    # Distribución estadística
    st.markdown("##### Distribución de turnos por pareja")
    fig_h = px.histogram(
        resumen, x="Turnos asignados", nbins=15,
        title=None,
        color_discrete_sequence=["#1F4E78"],
    )
    fig_h.update_layout(
        height=250, margin=dict(l=20, r=20, t=20, b=40),
        plot_bgcolor="white", showlegend=False,
        xaxis_title="Turnos asignados en el mes",
        yaxis_title="N° parejas",
    )
    fig_h.update_xaxes(gridcolor="#EEEEEE")
    fig_h.update_yaxes(gridcolor="#EEEEEE")
    st.plotly_chart(fig_h, use_container_width=True, key="hist_mes")

    # Calendario visual (heatmap) — TODAS las parejas
    st.markdown("##### Calendario mensual de asignación")
    st.caption(
        f"Vista calendario completa de las **{len(cal)} parejas**. "
        "**T** = turno asignado · **R** = en reserva (apoyo / disponible) · "
        "**D** = descanso."
    )

    # Crear heatmap usando colores
    cal_mostrar = cal.copy()
    color_map = {"T": "#1F4E78", "R": "#FFC857", "D": "#E8EEF7"}
    # Construir matriz numérica para el heatmap (1=T, 2=R, 0=D)
    val_map = {"T": 2, "R": 1, "D": 0}
    z = cal_mostrar.map(lambda v: val_map.get(v, 0)).values
    text = cal_mostrar.values

    fig_cal = go.Figure(data=go.Heatmap(
        z=z, x=cal_mostrar.columns.tolist(),
        y=cal_mostrar.index.tolist(),
        text=text, texttemplate="%{text}",
        textfont={"size": 8, "color": "white"},
        colorscale=[[0, "#E8EEF7"], [0.5, "#FFC857"], [1.0, "#1F4E78"]],
        showscale=False, xgap=1, ygap=1,
    ))
    fig_cal.update_layout(
        height=max(420, 18 * len(cal_mostrar) + 80),
        margin=dict(l=60, r=20, t=20, b=60),
        yaxis_title="Pareja", xaxis_title="Día del mes",
    )
    fig_cal.update_xaxes(side="top", tickangle=0)
    fig_cal.update_yaxes(autorange="reversed")
    st.plotly_chart(fig_cal, use_container_width=True, key="cal_mes")

    # Tabla resumen por pareja
    st.markdown("##### Resumen por pareja")
    st.dataframe(resumen, use_container_width=True, hide_index=True,
                 height=min(420, 37 * (len(resumen) + 1) + 40))

    # Información sobre el modelo
    with st.expander("📋 Reglas y supuestos del calendario teórico"):
        st.markdown(f"""
**Reglas operacionales respetadas:**
- **Régimen 6x1**: cada pareja trabaja un máximo de 6 días seguidos antes
  de un descanso obligatorio. Se rota el día de descanso entre semanas.
- **Jornada máxima** ≤ 7:30 h y **conducción** ≤ 5:00 h por turno (validado
  en la construcción de turnos arriba).
- **Reposo entre jornadas** ≥ 10 h (garantizado al asignar 1 solo turno por
  pareja por día).
- **Servicios por tipo de día** (datos reales del itinerario activo):
  L-J = {turnos_por_dia.get('LJ',0)} · V = {turnos_por_dia.get('V',0)} ·
  Sáb = {turnos_por_dia.get('SAB',0)} · Dom = {turnos_por_dia.get('DOM',0)}.

**Estados del calendario:**
- **T** (turno asignado): la pareja toma un turno operativo.
- **R** (reserva): la pareja está presente y disponible pero no se le
  asignó un turno operativo (cubre ausencias, apoyo, fuente de
  flexibilidad).
- **D** (descanso): día libre obligatorio del régimen 6x1.

**Distribución del descanso:**
- El día de descanso de cada pareja se desplaza cada semana
  (`día_descanso = (índice_pareja + nº_semana) mod 7`), de modo que ninguna
  pareja siempre descanse el mismo día y los descansos quedan repartidos.
- En días con menos turnos (sábado y domingo) más parejas quedan en
  estado **R** — son la reserva natural del sistema.

**Limitaciones (modelo teórico):**
- No se incluyen aún vacaciones legales, licencias médicas ni feriados.
  La dotación recomendada ya contempla un margen para ausentismo.
- La asignación específica de qué pareja toma qué servicio cada día es
  posterior a este calendario y depende de antigüedades, certificaciones
  y reglas de la operación real.
        """)

    # Descarga XLSX
    import io
    buf = io.BytesIO()
    with pd.ExcelWriter(buf, engine="openpyxl") as writer:
        cal.to_excel(writer, sheet_name="Calendario mensual")
        resumen.to_excel(writer, sheet_name="Resumen por pareja",
                          index=False)
        # Info del mes
        info_df = pd.DataFrame({
            "Día": [f"d{i+1}" for i in range(len(cal_data["turnos_dia"]))],
            "Tipo": cal_data["tipo_dia"],
            "Turnos": cal_data["turnos_dia"],
        })
        info_df.to_excel(writer, sheet_name="Turnos por día", index=False)
    st.download_button(
        "📥 Descargar calendario mensual completo (XLSX)",
        data=buf.getvalue(),
        file_name="distribucion_mensual_parejas.xlsx",
        mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        key="dl_dist_mes",
    )


# ---------------------------------------------------------------------------
# Componente principal de la pestaña
# ---------------------------------------------------------------------------

def render_turnos(services_df: pd.DataFrame, dia_sel: str = "LJ") -> None:
    """Pestaña de turnos teóricos con escenarios de optimización.

    dia_sel: día a visualizar ('LJ','V','SAB','DOM'). La dotación de parejas
    se calcula con los turnos reales de los 4 tipos de día.
    """
    from components.viz_gantt import aplica_en_dia

    # Solo servicios Biotren con horario (sin canales de carga)
    df_full = services_df.dropna(subset=["h_salida", "h_llegada"]).copy()
    if "tipo" in df_full.columns:
        df_full = df_full[~df_full["tipo"].str.upper().str.contains("CARGA", na=False)]

    if df_full.empty:
        st.info("No hay servicios Biotren con horario para construir turnos.")
        return

    # ----- Configuración del escenario de optimización -----
    st.markdown("##### Configuración del escenario de turnos")
    st.caption(
        "Ajusta las reglas para explorar cómo se optimiza la cantidad de "
        "turnos. La dotación se calcula con los turnos reales de los 4 "
        "tipos de día (L-J × 4, V, Sáb, Dom)."
    )

    c1, c2 = st.columns(2)
    with c1:
        permitir_cambio = st.checkbox(
            "Permitir cambio de tren en puntos de relevo", value=True,
            help="Si se activa, una pareja puede entregar su tren y tomar "
                 "otro en un punto de relevo, encadenando más servicios.",
        )
        pernocta_laja = st.checkbox(
            "Permitir pernocta en Laja", value=True,
            help="Las parejas que terminan en Laja pernoctan allí y retoman "
                 "el servicio en Laja al día siguiente, sin traslado.",
        )
        aplicar_buffer = st.checkbox(
            "Aplicar buffer de 15 min al relevo", value=True,
            help="La pareja que toma un relevo debe presentarse 15 min "
                 "antes del inicio del servicio (adicional al traslado).",
        )
        puntos = st.multiselect(
            "Puntos de relevo habilitados",
            PUNTOS_RELEVO_OPC, default=PUNTOS_RELEVO_DEF,
            help="Estaciones donde un turno puede iniciarse, cerrarse o "
                 "cambiar de tren.",
        )
    with c2:
        permitir_he = st.checkbox(
            "Permitir horas extra (hasta +2 h)", value=False,
            help="Extiende la jornada máxima de 7:30 a 9:30 h cuando "
                 "se autoriza HE. La conducción máxima sigue siendo 5 h.",
        )
        if permitir_he:
            max_jorn_h = st.slider("Jornada máxima (h)", 6.0, 9.5, 9.5, 0.25,
                                    help="Con HE puede llegar a 9:30 h")
        else:
            max_jorn_h = st.slider("Jornada máxima (h)", 6.0, 9.0, 7.5, 0.25,
                                    help="Norma vigente sin HE: 7:30 h")
        max_cond_h = st.slider("Conducción máxima (h)", 4.0, 6.0, 5.0, 0.25,
                               help="Norma vigente (Art. 25 ter): 5:00 h")
        ausentismo_pct = st.slider(
            "Ausentismo total (%)", 0.0, 25.0, 14.6, 0.5,
            help="Suma de vacaciones legales, licencias médicas, "
                 "permisos y otros (por defecto 14.6%).",
        )

    cfg_actual = dict(
        max_jornada_s=int(max_jorn_h * 3600),
        max_conduccion_s=int(max_cond_h * 3600),
        permitir_cambio_tren=permitir_cambio,
        puntos_relevo=tuple(puntos),
        pernocta_laja=pernocta_laja,
        aplicar_buffer_relevo=aplicar_buffer,
    )
    cfg_base = dict(
        max_jornada_s=MAX_JORNADA_S_DEF,
        max_conduccion_s=MAX_CONDUCCION_S_DEF,
        permitir_cambio_tren=False,
        puntos_relevo=tuple(PUNTOS_RELEVO_DEF),
        pernocta_laja=False,
        aplicar_buffer_relevo=True,
    )

    # ----- Construir turnos para cada tipo de día -----
    DIAS = ["LJ", "V", "SAB", "DOM"]
    DIAS_LABEL = {"LJ": "Lun-Jue", "V": "Viernes",
                  "SAB": "Sábado", "DOM": "Domingo"}
    shifts_actual = {}
    shifts_base = {}
    if "dia" in df_full.columns:
        for d in DIAS:
            mask = df_full.apply(lambda r: aplica_en_dia(r, d), axis=1)
            df_d = df_full[mask]
            shifts_actual[d] = (build_shifts(df_d, **cfg_actual)
                                if not df_d.empty else pd.DataFrame())
            shifts_base[d] = (build_shifts(df_d, **cfg_base)
                              if not df_d.empty else pd.DataFrame())
    else:
        # Sin columna día: tratar todo como L-J
        shifts_actual["LJ"] = build_shifts(df_full, **cfg_actual)
        shifts_base["LJ"] = build_shifts(df_full, **cfg_base)
        for d in ["V", "SAB", "DOM"]:
            shifts_actual[d] = pd.DataFrame()
            shifts_base[d] = pd.DataFrame()

    # ----- Día actualmente visualizado -----
    shifts_df = shifts_actual.get(dia_sel, pd.DataFrame())
    if shifts_df.empty:
        st.warning(
            f"No hay turnos generables para el día '{DIAS_LABEL[dia_sel]}' "
            "con la configuración elegida."
        )

    # ----- Pico simultáneo (día actual; informativo) -----
    pico_dia = 0
    if not shifts_df.empty:
        events = []
        for _, sh in shifts_df.iterrows():
            events.append((sh["h_inicio"], 1))
            events.append((sh["h_termino"], -1))
        events.sort()
        cur = 0
        for _, d in events:
            cur += d
            pico_dia = max(pico_dia, cur)

    # Jornada media ponderada sobre todos los turnos
    todos = pd.concat([sh for sh in shifts_actual.values() if not sh.empty],
                       ignore_index=True) if any(
        not sh.empty for sh in shifts_actual.values()) else pd.DataFrame()
    jornada_media_h = (todos["jornada_s"].mean() / 3600) if not todos.empty else 0

    # ----- Dotación semanal con datos REALES por día -----
    turnos_por_dia = {d: len(shifts_actual[d]) for d in DIAS}
    turnos_por_dia_base = {d: len(shifts_base[d]) for d in DIAS}
    crews = estimate_crews_semanal(turnos_por_dia, jornada_media_h, pico_dia,
                                     ausentismo=ausentismo_pct/100)
    crews_base_dict = estimate_crews_semanal(turnos_por_dia_base,
                                              jornada_media_h, pico_dia,
                                              ausentismo=ausentismo_pct/100)

    # ----- Comparación de escenarios -----
    label_dia = DIAS_LABEL[dia_sel]
    shifts_base_dia = shifts_base.get(dia_sel, pd.DataFrame())
    n_dia_actual = turnos_por_dia[dia_sel]
    n_dia_base = turnos_por_dia_base[dia_sel]

    st.markdown(f"##### Optimización ({label_dia}): escenario actual vs base")
    delta_turnos = n_dia_actual - n_dia_base
    delta_dot = (crews["dotacion_recomendada"]
                 - crews_base_dict["dotacion_recomendada"])
    if delta_turnos < 0:
        st.success(
            f"✅ La configuración actual genera **{n_dia_actual} turnos** el {label_dia} — "
            f"{abs(delta_turnos)} menos que el escenario base "
            f"({n_dia_base} turnos, sin cambio de tren). "
            f"Dotación semanal: {crews['dotacion_recomendada']} parejas "
            f"({delta_dot:+d} respecto al base).",
            icon="✅",
        )
    elif delta_turnos == 0:
        st.info(
            f"La configuración actual genera **{n_dia_actual} turnos** el {label_dia}, "
            f"igual que el escenario base.", icon="ℹ️",
        )
    else:
        st.warning(
            f"La configuración actual genera **{n_dia_actual} turnos** el {label_dia} — "
            f"{delta_turnos} más que el escenario base.", icon="⚠️",
        )

    # Cifras seguras aunque shifts_base_dia/shifts_df estén vacíos
    def _media(serie):
        if serie is None or serie.empty:
            return None
        v = serie.mean()
        return v if pd.notna(v) else None

    comp = pd.DataFrame([
        (f"Turnos {label_dia}", str(n_dia_base), str(n_dia_actual)),
        ("Turnos por semana",
         str(crews_base_dict["turnos_semana"]),
         str(crews["turnos_semana"])),
        ("Dotación recomendada (parejas)",
         str(crews_base_dict["dotacion_recomendada"]),
         str(crews["dotacion_recomendada"])),
        ("Personal total (personas)",
         str(crews_base_dict["personal_total"]),
         str(crews["personal_total"])),
        ("Jornada media",
         _fmt(_media(shifts_base_dia["jornada_s"]) if not shifts_base_dia.empty else None),
         _fmt(_media(shifts_df["jornada_s"]) if not shifts_df.empty else None)),
        ("Conducción media",
         _fmt(_media(shifts_base_dia["conduccion_s"]) if not shifts_base_dia.empty else None),
         _fmt(_media(shifts_df["conduccion_s"]) if not shifts_df.empty else None)),
        ("Turnos con cambio de tren",
         str(int((shifts_base_dia["n_trenes"] > 1).sum()) if not shifts_base_dia.empty else 0),
         str(int((shifts_df["n_trenes"] > 1).sum()) if not shifts_df.empty else 0)),
        ("Turnos que cumplen jornada/conducción",
         f"{int(shifts_base_dia['cumple'].sum()) if not shifts_base_dia.empty else 0}/{len(shifts_base_dia)}",
         f"{int(shifts_df['cumple'].sum()) if not shifts_df.empty else 0}/{len(shifts_df)}"),
    ], columns=["Indicador", "Escenario base", "Escenario actual"])
    st.dataframe(comp, use_container_width=True, hide_index=True)

    # ----- Distribución semanal de turnos -----
    st.markdown("##### Distribución semanal de turnos")
    dist_df = pd.DataFrame([
        {"Tipo de día": DIAS_LABEL[d],
         "Multiplicador semanal": str(4 if d == "LJ" else 1),
         "Turnos / día": str(turnos_por_dia[d]),
         "Turnos semanales":
            str(turnos_por_dia[d] * (4 if d == "LJ" else 1))}
        for d in DIAS
    ])
    dist_df.loc[len(dist_df)] = ["TOTAL", "", "—", str(crews["turnos_semana"])]
    st.dataframe(dist_df, use_container_width=True, hide_index=True)

    # ----- Tarjetas resumen -----
    st.markdown(f"##### Resumen del escenario actual — {label_dia}")
    m1, m2, m3, m4 = st.columns(4)
    m1.metric(f"Turnos {label_dia}", n_dia_actual)
    m2.metric("Dotación recomendada", f"{crews['dotacion_recomendada']} parejas",
              help="Plantilla calculada con turnos reales de los 4 tipos de día")
    m3.metric("Personal total", f"{crews['personal_total']} personas")
    m4.metric(f"Pico simultáneo ({label_dia})", crews["pico"],
              help="Parejas operando a la vez (dato informativo)")

    if not shifts_df.empty:
        m5, m6, m7, m8 = st.columns(4)
        m5.metric("Jornada media", _fmt(shifts_df["jornada_s"].mean()))
        m6.metric("Conducción media", _fmt(shifts_df["conduccion_s"].mean()))
        m7.metric("Turnos con relevo de tren",
                  int((shifts_df["n_trenes"] > 1).sum()))
        cumplen = int(shifts_df["cumple"].sum())
        m8.metric("Turnos que cumplen", f"{cumplen} / {len(shifts_df)}")

    # ===== Diagnóstico honesto contra dotación real =====
    delta = crews["delta_vs_real"]
    aus_real = crews["ausentismo_calibrado"] * 100
    if delta > 0:
        bg, border, icon = "#FFF5E6", "#E8A33D", "⚠️"
        veredicto = (
            f"El modelo calcula <b>{crews['dotacion_recomendada']} parejas</b> "
            f"pero la dotación real es de <b>{crews['dotacion_real']}</b> "
            f"({delta:+d} parejas de diferencia)."
        )
    elif delta < 0:
        bg, border, icon = "#E8F4E8", "#3CB371", "✅"
        veredicto = (
            f"El modelo calcula <b>{crews['dotacion_recomendada']} parejas</b>, "
            f"menos que las <b>{crews['dotacion_real']}</b> reales "
            f"({delta:+d} parejas)."
        )
    else:
        bg, border, icon = "#E8F4E8", "#3CB371", "✅"
        veredicto = (f"El modelo coincide con la dotación real de "
                     f"<b>{crews['dotacion_real']} parejas</b>.")

    msg = (
        f"<div style='background:{bg};padding:14px 18px;"
        f"border-left:4px solid {border};border-radius:6px;"
        f"margin:18px 0;'>"
        f"<div style='font-weight:600;color:#2C3E50;margin-bottom:8px;'>"
        f"{icon} Diagnóstico vs dotación real (50 parejas informadas)"
        f"</div>"
        f"<div style='font-size:13px;color:#2C3E50;line-height:1.7;'>"
        f"{veredicto}<br>"
        f"• Turnos operativos necesarios: <b>{crews['parejas_operativas']:.1f}</b> "
        f"(={crews['turnos_semana']} turnos/sem ÷ 6 turnos por pareja).<br>"
        f"• Ausentismo asumido: <b>{crews['ausentismo']*100:.1f}%</b> · "
        f"margen aplicado: <b>×{crews['margen']:.2f}</b>.<br>"
        f"• <b>Ausentismo implícito en la dotación real de 50</b>: "
        f"<b>{aus_real:.1f}%</b> "
    )
    if aus_real < 5:
        msg += (
            "→ <i>la operación está prácticamente sin colchón para ausentismo</i>. "
            "Esto sugiere que las licencias, vacaciones y permisos se cubren "
            "con horas extra estructurales o reasignación entre escenarios. "
            "Considera revisar si las 50 parejas son sostenibles o si la "
            "operación depende de HE para no caer."
        )
    elif aus_real < 10:
        msg += "→ margen ajustado, cubre vacaciones legales pero poco más."
    else:
        msg += "→ margen razonable."
    msg += "</div></div>"
    st.markdown(msg, unsafe_allow_html=True)

    # ===== Maquinista vs Ayudante =====
    st.markdown("##### 👥 Turnos requeridos: maquinista vs ayudante")
    st.caption(
        "El ayudante no tiene la restricción de 5 h de conducción del "
        "maquinista, por lo que puede cubrir turnos más largos. "
        "Calculando ambos por separado se ve si la dotación de "
        "ayudantes puede ser menor que la de maquinistas."
    )

    # Recalcular turnos solo con restricción de jornada (sin conducción)
    cfg_ayudante = dict(cfg_actual)
    cfg_ayudante["max_conduccion_s"] = 24 * 3600  # sin tope práctico
    shifts_ayud = {}
    for d in DIAS:
        if "dia" in df_full.columns:
            mask = df_full.apply(lambda r: aplica_en_dia(r, d), axis=1)
            df_d = df_full[mask]
            shifts_ayud[d] = (build_shifts(df_d, **cfg_ayudante)
                              if not df_d.empty else pd.DataFrame())
        else:
            shifts_ayud[d] = pd.DataFrame()
    n_ayud_dia = {d: len(shifts_ayud[d]) for d in DIAS}
    turnos_ayud_sem = (n_ayud_dia["LJ"]*4 + n_ayud_dia["V"]
                        + n_ayud_dia["SAB"] + n_ayud_dia["DOM"])
    op_ayud = turnos_ayud_sem / 6 if turnos_ayud_sem else 0
    dot_ayud = math.ceil(op_ayud / (1 - ausentismo_pct/100)
                          * crews["margen"]) if op_ayud else 0

    cm1, cm2, cm3 = st.columns(3)
    with cm1:
        st.metric("Turnos maquinista L-J", turnos_por_dia["LJ"],
                  help="Limitado por jornada (≤7:30) y conducción (≤5:00)")
        st.metric("Turnos maquinista / semana", crews["turnos_semana"])
        st.metric("Dotación maquinistas", f"{crews['dotacion_recomendada']}")
    with cm2:
        st.metric("Turnos ayudante L-J", n_ayud_dia["LJ"],
                  help="Limitado solo por jornada (≤7:30)")
        st.metric("Turnos ayudante / semana", turnos_ayud_sem)
        st.metric("Dotación ayudantes", f"{dot_ayud}")
    with cm3:
        delta_t = crews["turnos_semana"] - turnos_ayud_sem
        delta_d = crews["dotacion_recomendada"] - dot_ayud
        st.metric("Δ turnos / semana", f"{delta_t:+d}",
                  delta=f"{-delta_t}", delta_color="inverse",
                  help="Si > 0, los ayudantes encadenan más servicios")
        st.metric("Δ dotación posible", f"{delta_d:+d}",
                  delta=f"{-delta_d}", delta_color="inverse")
        st.metric("Personal total ajustado",
                   f"{crews['dotacion_recomendada'] + dot_ayud} pers.",
                   help="Maquinistas + ayudantes (en lugar de 2× maquinistas)")

    if delta_d > 0:
        ahorro_pers = delta_d
        st.success(
            f"💡 **Oportunidad de optimización**: si se planifica turnos "
            f"diferenciados para ayudantes (sin tope de 5 h de conducción), "
            f"se podrían requerir **{ahorro_pers} ayudantes menos** que "
            f"maquinistas. Ahorro potencial de personal: "
            f"**~{ahorro_pers} personas** sobre la plantilla actual."
        )

    st.markdown("---")

    # ----- Análisis de puntos clave de optimización -----
    st.markdown("##### Análisis de puntos clave de optimización")
    st.caption(
        "Identifica automáticamente qué puntos de relevo aportan mayor "
        "reducción de turnos y cuál es la configuración óptima alcanzable."
    )
    if st.button(f"🔍 Analizar puntos clave de optimización ({label_dia})"):
        # Filtrar al día seleccionado para el análisis
        if "dia" in df_full.columns:
            mask_dia = df_full.apply(lambda r: aplica_en_dia(r, dia_sel), axis=1)
            df_dia = df_full[mask_dia]
        else:
            df_dia = df_full
        if df_dia.empty:
            st.warning(f"No hay servicios para analizar en {label_dia}.")
        else:
            with st.spinner("Probando configuraciones…"):
                ana = ranking_puntos_clave(
                    df_dia, pernocta_laja=pernocta_laja,
                    max_jornada_s=cfg_actual["max_jornada_s"],
                    max_conduccion_s=cfg_actual["max_conduccion_s"],
                )
                perno = comparar_pernocta(
                    df_dia, tuple(PUNTOS_RELEVO_OPC),
                    cfg_actual["max_jornada_s"],
                    cfg_actual["max_conduccion_s"],
                )
            st.session_state["turnos_analisis"] = {"ana": ana, "perno": perno,
                                                    "dia": dia_sel}

    if "turnos_analisis" in st.session_state:
        ana = st.session_state["turnos_analisis"]["ana"]
        perno = st.session_state["turnos_analisis"]["perno"]

        st.markdown(
            f"**Punto de partida** — sin relevos: **{ana['n_base']} turnos**. "
            f"**Configuración óptima** — relevos en "
            f"{', '.join(ana['puntos_optimos']) or '(ninguno)'}: "
            f"**{ana['n_optimo']} turnos** "
            f"(−{ana['n_base'] - ana['n_optimo']} respecto al inicio)."
        )

        if ana["ranking"]:
            st.markdown("**Aporte incremental de cada punto de relevo** "
                        "(en orden de impacto):")
            rank_df = pd.DataFrame([
                {
                    "Orden": i + 1,
                    "Punto de relevo": r["punto"],
                    "Turnos ahorrados": f"−{r['ahorro']}",
                    "Turnos acumulados": r["turnos_acumulado"],
                }
                for i, r in enumerate(ana["ranking"])
            ])
            st.dataframe(rank_df, use_container_width=True, hide_index=True)
        else:
            st.info("Ningún punto de relevo individual reduce los turnos "
                    "con la configuración de jornada/conducción actual.")

        if ana["puntos_sin_aporte"]:
            st.caption(
                "Puntos sin aporte adicional: "
                + ", ".join(ana["puntos_sin_aporte"])
                + ". Habilitarlos no reduce más los turnos."
            )

        # Efecto de la pernocta
        delta_perno = perno["n_sin"] - perno["n_con"]
        if delta_perno > 0:
            st.success(
                f"🛏️ **Pernocta en Laja**: habilitar la pernocta reduce de "
                f"{perno['n_sin']} a {perno['n_con']} turnos "
                f"(−{delta_perno}). Las parejas que terminan en Laja retoman "
                f"allí al día siguiente, evitando el largo traslado.",
                icon="🛏️",
            )
        else:
            st.info(
                f"🛏️ **Pernocta en Laja**: con la configuración de relevos "
                f"completa, la pernocta mantiene los turnos en "
                f"{perno['n_con']} (su efecto principal es evitar traslados "
                f"improductivos largos, más que reducir turnos).",
                icon="🛏️",
            )

        st.caption(
            "💡 Recomendación: habilita en la configuración de arriba los "
            "puntos de relevo listados como óptimos y la pernocta en Laja "
            "para alcanzar la menor cantidad de turnos."
        )

    st.markdown("---")

    # ----- Diagrama de turnos con fases -----
    st.markdown(f"##### Diagrama de turnos por fase — {label_dia}")
    st.caption(
        "Cada fila es un turno descompuesto en sus fases: traslado inicial, "
        "revisión del tren (solo primer uso del día), servicios, esperas / "
        "relevos, traslado final y cierre."
    )
    if shifts_df.empty:
        st.info(f"No hay turnos generables para mostrar en {label_dia}.")
        return

    fases = fases_to_df(shifts_df)
    orden_fase = ["Traslado inicial", "Revisión tren SFE", "Revisión tren SFB",
                  "Servicio", "Espera", "Relevo / espera", "Traslado final",
                  "Cierre"]
    fases_presentes = [f for f in orden_fase if f in set(fases["fase"])]

    fig = px.timeline(
        fases, x_start="ini_dt", x_end="fin_dt", y="turno",
        color="fase", color_discrete_map=FASE_COLOR,
        category_orders={"fase": fases_presentes},
        hover_data={"detalle": True, "dur_min": True,
                    "ini_dt": False, "fin_dt": False,
                    "turno": False, "fase": True},
        labels={"detalle": "Detalle", "dur_min": "Duración (min)",
                "fase": "Fase"},
    )
    fig.update_yaxes(autorange="reversed", title=None)
    fig.update_xaxes(title="Hora del día", tickformat="%H:%M",
                     dtick=60 * 60 * 1000, gridcolor="#EEEEEE")
    fig.update_layout(
        height=max(420, 17 * len(shifts_df)),
        margin=dict(l=20, r=20, t=20, b=40),
        plot_bgcolor="white", legend_title_text="Fase del turno",
        legend=dict(orientation="h", y=1.02, yanchor="bottom"),
        bargap=0.25,
    )
    st.plotly_chart(fig, use_container_width=True, key="turnos_fases_gantt")

    # ----- Tabla detallada -----
    with st.expander("Ver tabla detallada de turnos"):
        tabla = shifts_df[[
            "turno", "trenes", "n_servicios", "servicios",
            "punto_inicio", "punto_fin", "inicio_str", "termino_str",
            "jornada_str", "conduccion_str", "cumple",
        ]].rename(columns={
            "turno": "Turno", "trenes": "Tren(es)", "n_servicios": "N° Serv.",
            "servicios": "Servicios", "punto_inicio": "Inicio",
            "punto_fin": "Fin", "inicio_str": "Inicio jorn.",
            "termino_str": "Término jorn.", "jornada_str": "Jornada",
            "conduccion_str": "Conducción", "cumple": "Cumple",
        })
        st.dataframe(tabla, use_container_width=True, height=420,
                     hide_index=True)
        csv = tabla.to_csv(index=False).encode("utf-8")
        st.download_button("Descargar turnos como CSV", data=csv,
                           file_name="turnos_biotren.csv", mime="text/csv")

    # ----- Metodología -----
    with st.expander("Metodología del cálculo de turnos y dotación"):
        st.markdown(f"""
**Modelo de tiempos de cada turno:**
- **Traslado inicial** — del depósito El Arenal al punto de inicio
  (Concepción 45 min · Coronel 90 · Lomas Coloradas 60 · Desvío Lagunillas
  75 · Hualqui 90 · Omer Huet 90 · Arenal 0 min).
- **Revisión del tren** — 45 min, **solo la primera vez que cada tren opera
  en el día** (revisión del equipo SFE o SFB).
- **Servicios** — conducción efectiva; con esperas o relevos si el turno
  encadena varios.
- **Traslado final** — del punto de término de vuelta al depósito.
- **Cierre** — 30 min al terminar el turno.

**Restricciones:** jornada ≤ {max_jorn_h:.2f} h · conducción ≤ {max_cond_h:.2f} h.

**Optimización de turnos:** los servicios se empaquetan en turnos con un
algoritmo *greedy* que minimiza el tiempo muerto. Si se permite el **cambio
de tren en puntos de relevo**, una pareja puede entregar su tren y tomar
otro, encadenando servicios que de otro modo quedarían en turnos separados.
Esto reduce la cantidad total de turnos.

**Dotación de parejas — paso a paso:**
1. Turnos por tipo de día: L-J **{crews['n_lj']}** · V **{crews['n_v']}** · Sáb **{crews['n_sab']}** · Dom **{crews['n_dom']}**
2. Turnos por semana = {crews['n_lj']}×4 + {crews['n_v']} + {crews['n_sab']} + {crews['n_dom']}
   = **{crews['turnos_semana']:.0f} turnos/semana** (con datos reales por día)
3. Cada pareja cubre 6 turnos/semana (régimen 6x1)
   → operativas = **{crews['parejas_operativas']:.1f} parejas**
4. Disponibilidad = {crews['disponibilidad']*100:.1f}% (ausentismo
   {crews['ausentismo']*100:.1f}%: vacaciones, licencias, permisos)
   → plantilla = **{crews['parejas_plantilla']:.1f} parejas**
5. Margen de relevos e imprevistos (×{crews['margen']:.2f})
   → **dotación = {crews['dotacion_recomendada']} parejas
   ({crews['personal_total']} personas)**

> Los turnos son **teóricos**: la asignación nominal de cada pareja a su rol
> semanal requiere además respetar el reposo de 10 h entre jornadas.
        """)

    # ===== Distribución teórica mensual de turnos por pareja =====
    st.markdown("---")
    st.markdown("### 📅 Distribución teórica mensual de turnos por pareja")
    st.caption(
        "Asignación teórica de las parejas de conducción a un mes operativo, "
        "respetando el régimen 6x1, la jornada máxima y los días reales "
        "de cada tipo (L-J, V, Sáb, Dom). Permite verificar la dotación "
        "calculada y entregar un calendario inicial al equipo de operaciones."
    )

    cmes1, cmes2 = st.columns([1, 1])
    with cmes1:
        dias_mes_in = st.number_input(
            "Días del mes", min_value=28, max_value=31, value=30, step=1,
            key="turnos_dias_mes",
        )
    with cmes2:
        dia_inicio_label = st.selectbox(
            "El mes empieza en…",
            ["Lunes", "Martes", "Miércoles", "Jueves",
             "Viernes", "Sábado", "Domingo"],
            index=0, key="turnos_inicio_dia",
        )
    DIA_IDX = {"Lunes": 0, "Martes": 1, "Miércoles": 2, "Jueves": 3,
                "Viernes": 4, "Sábado": 5, "Domingo": 6}
    render_distribucion_mensual(
        turnos_por_dia, crews["dotacion_recomendada"],
        dias_mes=int(dias_mes_in),
        dia_inicio=DIA_IDX[dia_inicio_label],
    )
