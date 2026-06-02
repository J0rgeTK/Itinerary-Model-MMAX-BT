"""
Asignación de turnos a personal simulado (maquinistas y ayudantes).

Esta pieza es de PROGRAMACIÓN (no dimensionamiento). A diferencia de la
pestaña "Turnos teóricos", aquí:
  - Se genera un roster de personal simulado (~50 maq + 50 ayu).
  - Se asigna cada turno abstracto a personas concretas.
  - Se valida el REPOSO DE 10H entre fin de un turno e inicio del siguiente.
  - Se valida el RÉGIMEN 6x1 (1 día libre por semana, sin 7 días corridos).
  - Roles DESACOPLADOS (Opción B): maquinistas y ayudantes se asignan
    independientemente, manteniendo el resto de las restricciones
    operacionales (jornada ≤7:30 h, traslados, revisión) salvo la conducción
    (5 h aplica solo al maquinista).
  - SIN restricción de certificación: cualquier persona opera cualquier
    equipo (SFE, SFB, UT).
  - Si no hay personal disponible para un turno, se deja DESCUBIERTO y se
    reporta explícitamente (no se cubre violando reglas).
  - SIMULACIÓN DE AUSENCIAS: marcar personas como ausentes en fechas
    específicas y re-asignar.

El motor es GREEDY con fairness: para cada turno se elige, entre los
candidatos válidos, a la persona con mayor tiempo desde su última asignación
(distribuye la carga uniformemente).

Diseñado para aceptar en el futuro una planilla real de personas
(disponibilidad, rol, ausencias programadas) sin reescribir el motor:
basta reemplazar la lista de Persona.
"""

from __future__ import annotations

import datetime as dt
import random
from dataclasses import dataclass, field
from typing import Optional

import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import streamlit as st

from components.viz_turnos import build_shifts
from components.viz_gantt import aplica_en_dia


# =============================================================================
# Constantes
# =============================================================================

REPOSO_MIN_S = 10 * 3600  # 10 horas de reposo legal entre jornadas
REPOSO_MIN_H = REPOSO_MIN_S / 3600

N_MAQUINISTAS_DEF = 50
N_AYUDANTES_DEF = 50

# Fecha de referencia usada por el resto del dashboard (para dt)
FECHA_REF = dt.date(2026, 4, 20)


# =============================================================================
# Modelo de persona
# =============================================================================

@dataclass
class Persona:
    """Una persona del roster (maquinista o ayudante)."""
    id: str
    rol: str  # "Maquinista" | "Ayudante"
    nombre: str
    disponible: bool = True
    # Fechas bloqueadas explícitamente (ausencias programadas).
    # dict: fecha (date) -> motivo (str)
    ausencias: dict = field(default_factory=dict)

    # Estado interno del motor (se resetea al inicio de cada corrida).
    last_end_abs_s: Optional[int] = None
    last_working_date: Optional[dt.date] = None
    consecutive_working_days: int = 0
    turnos_asignados: list = field(default_factory=list)

    def reset_estado(self) -> None:
        self.last_end_abs_s = None
        self.last_working_date = None
        self.consecutive_working_days = 0
        self.turnos_asignados = []

    def esta_ausente(self, fecha: dt.date) -> tuple[bool, str]:
        """True si la persona está ausente en `fecha` y el motivo."""
        if not self.disponible:
            return True, "Bloqueado global"
        if fecha in self.ausencias:
            return True, self.ausencias[fecha]
        return False, ""

    def validar(self, h_inicio_s: int, fecha: dt.date,
                base_abs_s: int) -> tuple[bool, str]:
        """Chequea si la persona puede tomar un turno que inicia a h_inicio_s.

        Valida:
          - Disponibilidad
          - Reposo 10h entre fin del último turno e inicio del nuevo
          - Régimen 6x1: si ya trabajó 6 días consecutivos, el día 7
            es descanso obligatorio
        """
        ausente, motivo = self.esta_ausente(fecha)
        if ausente:
            return False, f"Ausente ({motivo})"

        abs_start = base_abs_s + h_inicio_s
        if self.last_end_abs_s is not None:
            rest_s = abs_start - self.last_end_abs_s
            if rest_s < REPOSO_MIN_S:
                return False, (f"Reposo {rest_s/3600:.1f}h < {REPOSO_MIN_H:.0f}h "
                               f"(fin anterior: {self._fmt_h(self.last_end_abs_s - base_abs_s)})")

        if (self.last_working_date is not None
                and (fecha - self.last_working_date).days == 1
                and self.consecutive_working_days >= 6):
            return False, "Régimen 6x1: 6 días corridos, descanso obligatorio"

        return True, "OK"

    def registrar(self, h_inicio_s: int, h_termino_s: int, fecha: dt.date,
                  base_abs_s: int, turno_info: dict) -> None:
        """Registra un turno asignado a esta persona."""
        self.last_end_abs_s = base_abs_s + h_termino_s
        if (self.last_working_date is None
                or (fecha - self.last_working_date).days > 1):
            self.consecutive_working_days = 1
        else:
            self.consecutive_working_days += 1
        self.last_working_date = fecha
        self.turnos_asignados.append(turno_info)

    @staticmethod
    def _fmt_h(seg: int) -> str:
        seg = int(seg) % 86400
        return f"{seg // 3600:02d}:{(seg % 3600) // 60:02d}"


def generar_roster(n_maq: int, n_ayu: int, seed: int = 42) -> list[Persona]:
    """Genera un roster simulado de n_maq maquinistas + n_ayu ayudantes."""
    rng = random.Random(seed)
    personas = []
    nombres_maq = [
        "Juan Pérez", "María González", "Carlos Muñoz", "Ana Rodríguez",
        "Luis Soto", "Patricia Vega", "Andrés Castillo", "Camila Fuentes",
        "Diego Silva", "Valentina Rojas", "Felipe Morales", "Isidora Lara",
        "Sebastián Torres", "Francisca Vidal", "Matías Reyes", "Javiera Bravo",
        "Ignacio Henríquez", "Constanza Sáez", "Tomás Jara", "Antonia Pardo",
        "Ricardo Cárcamo", "Macarena Lobos", "Hernán Sandoval", "Lorena Pizarro",
        "Álvaro Bustamante", "Daniela Carrasco", "Mauricio Alarcón", "Carolina Riquelme",
        "Esteban Figueroa", "Paula Navarrete", "Rodrigo Oyarzún", "Catalina Mancilla",
        "Marcelo Quintana", "Verónica Baeza", "Eduardo Araneda", "Pamela Ulloa",
        "Héctor Gallegos", "Soledad Acuña", "Claudio Cabezas", "Marta Yáñez",
        "Pablo Ferrada", "Claudia Tapia", "Sergio Mansilla", "Yasna Ávila",
        "Cristián Leiva", "Andrea Orellana", "Patricio Sanhueza", "Rosa Antinao",
        "Iván Escobar", "Lorena Pereira",
    ]
    for i in range(1, n_maq + 1):
        personas.append(Persona(
            id=f"MQ-{i:03d}",
            rol="Maquinista",
            nombre=nombres_maq[(i - 1) % len(nombres_maq)],
        ))
    nombres_ayu = [n.replace("Maquinista", "Ayudante") for n in nombres_maq]
    for i in range(1, n_ayu + 1):
        personas.append(Persona(
            id=f"AY-{i:03d}",
            rol="Ayudante",
            nombre=f"Ayudante {i:02d}",
        ))
    return personas


# =============================================================================
# Motor de asignación
# =============================================================================

def reset_roster_state(personas: list[Persona]) -> None:
    """Limpia el estado de asignación de todas las personas."""
    for p in personas:
        p.reset_estado()


def _seleccionar_candidato(candidatos: list[Persona], h_inicio_s: int,
                          fecha: dt.date, base_abs_s: int) -> tuple[Optional[Persona], str]:
    """Elige, entre los candidatos válidos, al de mayor 'descanso' (fairness).

    Criterio: mayor tiempo (en horas) desde el fin de su último turno
    (o +infinito si nunca ha trabajado). Esto distribuye la carga entre el
    personal disponible y minimiza violaciones de 10h.
    """
    validos = []
    invalidos_motivos = []
    for p in candidatos:
        ok, motivo = p.validar(h_inicio_s, fecha, base_abs_s)
        if ok:
            validos.append(p)
        else:
            invalidos_motivos.append((p.id, motivo))
    if not validos:
        return None, "; ".join(f"{pid}:{m}" for pid, m in invalidos_motivos[:3])

    # Fairness: prioriza al que más tiempo lleva sin trabajar.
    def score(p: Persona) -> float:
        if p.last_end_abs_s is None:
            return float("inf")
        return base_abs_s + h_inicio_s - p.last_end_abs_s

    validos.sort(key=score, reverse=True)
    return validos[0], ""


def asignar_turnos_a_personas(turnos_df: pd.DataFrame,
                              personas: list[Persona],
                              fecha: dt.date,
                              base_abs_s: Optional[int] = None,
                              reset_state: bool = False) -> dict:
    """Asigna cada turno abstracto a un maquinista y un ayudante concretos.

    Args:
        turnos_df: DataFrame devuelto por build_shifts. Debe tener al menos
            las columnas 'turno', 'h_inicio', 'h_termino', 'jornada_s'.
        personas: lista completa de personas del roster. Se filtrará por rol.
        fecha: fecha del día a planificar.
        base_abs_s: timestamp absoluto de inicio del día (segundos desde
            alguna referencia). Si es None, se calcula como
            (fecha - FECHA_REF).days * 86400.
        reset_state: si True, limpia el estado de todas las personas antes
            de asignar (útil para empezar de cero). Por defecto False: el
            estado persiste entre llamadas, lo que permite planificar
            día tras día y validar 10h de reposo entre jornadas
            consecutivas.

    Returns:
        dict con:
          - 'asignaciones': lista de dicts con turno, maquinista, ayudante
          - 'sin_maquinista': turnos sin maquinista disponible
          - 'sin_ayudante': turnos sin ayudante disponible
          - 'stats': contadores
    """
    if turnos_df is None or turnos_df.empty:
        return {
            "asignaciones": [],
            "sin_maquinista": [],
            "sin_ayudante": [],
            "stats": {"n_turnos": 0},
        }

    if base_abs_s is None:
        base_abs_s = (fecha - FECHA_REF).days * 86400

    if reset_state:
        reset_roster_state(personas)

    # Filtrar por rol
    pool_maq = [p for p in personas if p.rol == "Maquinista"]
    pool_ayu = [p for p in personas if p.rol == "Ayudante"]

    # Ordenar turnos por inicio
    turnos_ordenados = turnos_df.sort_values("h_inicio").to_dict("records")

    asignaciones = []
    sin_maq = []
    sin_ayu = []

    for turno in turnos_ordenados:
        h_ini = int(turno["h_inicio"])
        h_ter = int(turno["h_termino"])
        id_turno = turno["turno"]

        maq, motivo_maq = _seleccionar_candidato(pool_maq, h_ini, fecha, base_abs_s)
        ayu, motivo_ayu = _seleccionar_candidato(pool_ayu, h_ini, fecha, base_abs_s)

        info_turno = {
            "turno": id_turno,
            "h_inicio_s": h_ini,
            "h_termino_s": h_ter,
            "jornada_s": h_ter - h_ini,
            "trenes": turno.get("trenes", ""),
            "servicios": turno.get("servicios", ""),
            "punto_inicio": turno.get("punto_inicio", ""),
            "punto_fin": turno.get("punto_fin", ""),
        }

        if maq is None:
            sin_maq.append({
                "turno": id_turno, "h_inicio_s": h_ini, "h_termino_s": h_ter,
                "motivo": motivo_maq,
            })
        else:
            maq.registrar(h_ini, h_ter, fecha, base_abs_s, info_turno)
        if ayu is None:
            sin_ayu.append({
                "turno": id_turno, "h_inicio_s": h_ini, "h_termino_s": h_ter,
                "motivo": motivo_ayu,
            })
        else:
            ayu.registrar(h_ini, h_ter, fecha, base_abs_s, info_turno)

        asignaciones.append({
            **info_turno,
            "maquinista_id": maq.id if maq else None,
            "maquinista_nombre": maq.nombre if maq else None,
            "ayudante_id": ayu.id if ayu else None,
            "ayudante_nombre": ayu.nombre if ayu else None,
        })

    stats = {
        "n_turnos": len(turnos_ordenados),
        "n_asignados_completos": sum(
            1 for a in asignaciones
            if a["maquinista_id"] and a["ayudante_id"]
        ),
        "n_sin_maquinista": len(sin_maq),
        "n_sin_ayudante": len(sin_ayu),
        "n_maq_disponibles": sum(1 for p in pool_maq if not p.esta_ausente(fecha)[0]),
        "n_ayu_disponibles": sum(1 for p in pool_ayu if not p.esta_ausente(fecha)[0]),
    }

    return {
        "asignaciones": asignaciones,
        "sin_maquinista": sin_maq,
        "sin_ayudante": sin_ayu,
        "stats": stats,
    }


# =============================================================================
# Componente de UI
# =============================================================================

def _init_state():
    """Inicializa el estado de la sesión para esta pestaña."""
    if "asig_roster" not in st.session_state:
        st.session_state["asig_roster"] = generar_roster(
            N_MAQUINISTAS_DEF, N_AYUDANTES_DEF
        )
    if "asig_resultado" not in st.session_state:
        st.session_state["asig_resultado"] = None
    if "asig_turnos" not in st.session_state:
        st.session_state["asig_turnos"] = None
    if "asig_fecha" not in st.session_state:
        st.session_state["asig_fecha"] = FECHA_REF
    if "asig_dia_sel" not in st.session_state:
        st.session_state["asig_dia_sel"] = "LJ"


def _editable_roster() -> list[Persona]:
    """Editor del roster: tabla editable de personas y sus ausencias."""
    st.markdown("##### 1) Roster de personal")
    c1, c2, c3 = st.columns([1, 1, 1.5])
    with c1:
        n_maq = st.number_input(
            "N° maquinistas", 1, 200, N_MAQUINISTAS_DEF, 1,
            key="asig_n_maq",
        )
    with c2:
        n_ayu = st.number_input(
            "N° ayudantes", 1, 200, N_AYUDANTES_DEF, 1,
            key="asig_n_ayu",
        )
    with c3:
        st.markdown("<br>", unsafe_allow_html=True)
        if st.button("🔄 Regenerar roster", use_container_width=True):
            st.session_state["asig_roster"] = generar_roster(int(n_maq), int(n_ayu))
            st.session_state["asig_resultado"] = None
            st.rerun()

    roster: list[Persona] = st.session_state["asig_roster"]

    # Métricas rápidas del roster
    m1, m2, m3, m4 = st.columns(4)
    m1.metric("Total personas", len(roster))
    m2.metric("Maquinistas", sum(1 for p in roster if p.rol == "Maquinista"))
    m3.metric("Ayudantes", sum(1 for p in roster if p.rol == "Ayudante"))
    m4.metric("Bloqueados global", sum(1 for p in roster if not p.disponible))

    # Editor de disponibilidad global (toggle por persona)
    with st.expander("🔧 Editar disponibilidad global (marcado rápido)", expanded=False):
        st.caption("Marca personas como NO disponibles (licencia, renuncia, "
                   "vacaciones largas). Más abajo puedes marcar ausencias "
                   "puntuales por fecha.")
        df = pd.DataFrame([
            {"ID": p.id, "Rol": p.rol, "Nombre": p.nombre,
             "Disponible": p.disponible}
            for p in roster
        ])
        edited = st.data_editor(
            df, use_container_width=True, hide_index=True,
            column_config={
                "Disponible": st.column_config.CheckboxColumn(
                    "Disponible", help="Desmarca para bloquear a esta persona "
                                       "en TODAS las planificaciones."
                ),
            },
            disabled=["ID", "Rol", "Nombre"],
            height=min(420, 35 * (len(df) + 1) + 40),
            key="asig_editor_global",
        )
        # Sincronizar cambios al roster
        cambios = False
        for _, row in edited.iterrows():
            p = next((p for p in roster if p.id == row["ID"]), None)
            if p and p.disponible != bool(row["Disponible"]):
                p.disponible = bool(row["Disponible"])
                cambios = True
        if cambios:
            st.session_state["asig_resultado"] = None

    return roster


def _seccion_ausencias(roster: list[Persona]):
    """Editor de ausencias puntuales por fecha."""
    st.markdown("##### 2) Ausencias puntuales (simulación)")
    st.caption(
        "Marca una o más personas como ausentes en la fecha objetivo. "
        "Simula licencias médicas, permisos, capacitación, etc. "
        "Tras editar, vuelve a pulsar **Asignar** para re-ejecutar el motor."
    )
    col1, col2 = st.columns([1, 2])
    with col1:
        fecha = st.date_input(
            "Fecha objetivo de la ausencia",
            value=st.session_state["asig_fecha"],
            min_value=FECHA_REF,
            max_value=FECHA_REF + dt.timedelta(days=365),
            key="asig_fecha_ausencia",
        )
        st.session_state["asig_fecha"] = fecha
    with col2:
        personas_opts = [f"{p.id} · {p.nombre}" for p in roster if p.disponible]
        marcadas = st.multiselect(
            "Personas ausentes en esa fecha",
            options=personas_opts,
            help="Simula que estas personas no están disponibles ese día.",
            key="asig_personas_ausentes",
        )
    motivo_default = st.selectbox(
        "Motivo de la ausencia (aplica a todas las marcadas)",
        ["Licencia médica", "Vacaciones", "Capacitación",
         "Permiso administrativo", "Día libre compensatorio"],
        key="asig_motivo_ausencia",
    )

    # Aplicar / limpiar
    b1, b2 = st.columns(2)
    with b1:
        if st.button("💾 Registrar ausencias", use_container_width=True):
            for m in marcadas:
                pid = m.split(" · ")[0]
                p = next((p for p in roster if p.id == pid), None)
                if p:
                    p.ausencias[fecha] = motivo_default
            st.session_state["asig_resultado"] = None
            st.success(f"Ausencias registradas para {fecha.isoformat()}: "
                       f"{len(marcadas)} personas.")
    with b2:
        if st.button("🧹 Limpiar ausencias de la fecha", use_container_width=True):
            for p in roster:
                p.ausencias.pop(fecha, None)
            st.session_state["asig_resultado"] = None
            st.info(f"Ausencias del {fecha.isoformat()} eliminadas.")

    # Mostrar ausencias ya registradas
    ausencias_activas = []
    for p in roster:
        for f, mot in p.ausencias.items():
            ausencias_activas.append({
                "ID": p.id, "Rol": p.rol, "Nombre": p.nombre,
                "Fecha": f.isoformat(), "Motivo": mot,
            })
    if ausencias_activas:
        st.markdown(f"**Ausencias registradas:** {len(ausencias_activas)}")
        st.dataframe(
            pd.DataFrame(ausencias_activas).sort_values(["Fecha", "ID"]),
            use_container_width=True, hide_index=True, height=200,
        )


def _seccion_parametros_turnos() -> dict:
    """Parámetros de construcción de turnos (reutiliza los de viz_turnos)."""
    st.markdown("##### 3) Parámetros de construcción de turnos")
    st.caption(
        "Estos parámetros reproducen la configuración de la pestaña "
        "*Turnos teóricos*. El motor de asignación los aplica al construir "
        "los turnos abstractos del día elegido."
    )
    c1, c2, c3 = st.columns(3)
    with c1:
        permitir_cambio = st.checkbox(
            "Permitir cambio de tren en relevo", value=True, key="asig_cambio",
        )
        pernocta_laja = st.checkbox(
            "Pernocta en Laja", value=True, key="asig_pernocta",
        )
    with c2:
        max_jorn_h = st.slider("Jornada máxima (h)", 6.0, 9.5, 7.5, 0.25,
                               key="asig_jorn_h")
        max_cond_h = st.slider("Conducción máxima (h)", 4.0, 6.0, 5.0, 0.25,
                               key="asig_cond_h")
    with c3:
        aplicar_buffer = st.checkbox(
            "Buffer 15 min en relevo", value=True, key="asig_buffer",
        )
        st.caption(
            "El buffer de relevo **sí** afecta el cálculo de 10h: "
            "se descuenta del inicio del nuevo turno."
        )

    return dict(
        max_jornada_s=int(max_jorn_h * 3600),
        max_conduccion_s=int(max_cond_h * 3600),
        permitir_cambio_tren=permitir_cambio,
        pernocta_laja=pernocta_laja,
        aplicar_buffer_relevo=aplicar_buffer,
    )


def _seccion_dia_y_asignacion(roster: list[Persona], cfg_turnos: dict,
                               services_df: pd.DataFrame):
    """Selector de día, ejecución del motor, resultados."""
    st.markdown("##### 4) Día a planificar y ejecución del motor")

    # Selector de día
    col1, col2 = st.columns([1, 2])
    with col1:
        dia_label = st.radio(
            "Tipo de día del itinerario",
            ["Lun-Jue", "Viernes", "Sábado", "Domingo"],
            horizontal=False, key="asig_dia_tipo",
        )
    DIA_MAP = {"Lun-Jue": "LJ", "Viernes": "V",
               "Sábado": "SAB", "Domingo": "DOM"}
    dia_sel = DIA_MAP[dia_label]
    st.session_state["asig_dia_sel"] = dia_sel

    with col2:
        fecha = st.date_input(
            "Fecha específica",
            value=st.session_state["asig_fecha"],
            min_value=FECHA_REF,
            max_value=FECHA_REF + dt.timedelta(days=365),
            key="asig_fecha_ejec",
        )
        st.session_state["asig_fecha"] = fecha
        st.caption(
            f"Día seleccionado: **{dia_label}** ({dia_sel}) · "
            f"Fecha: **{fecha.isoformat()}**"
        )

    # Toggle para limpiar el estado de planificación (última fecha)
    usar_estado_limpio = st.checkbox(
        "🔄 Limpiar estado de planificación previo (empezar de cero)",
        value=False, key="asig_reset_state",
        help="Si lo marcas, el motor olvidará las asignaciones de fechas "
             "anteriores. Útil para simular desde un lunes cualquiera sin "
             "que el estado de la semana anterior influya.",
    )

    # Filtrar servicios al día elegido
    df_full = services_df.dropna(subset=["h_salida", "h_llegada"]).copy()
    if "tipo" in df_full.columns:
        df_full = df_full[~df_full["tipo"].str.upper().str.contains("CARGA", na=False)]
    if "dia" in df_full.columns:
        mask = df_full.apply(lambda r: aplica_en_dia(r, dia_sel), axis=1)
        df_dia = df_full[mask]
    else:
        df_dia = df_full

    n_svc = len(df_dia)
    st.caption(f"Servicios a planificar en {dia_label}: **{n_svc}**")

    # Botón de ejecución
    ejecutar = st.button("⚙️ Asignar turnos al personal", type="primary",
                         use_container_width=True)

    if ejecutar:
        if df_dia.empty:
            st.warning(f"No hay servicios Biotren para el día {dia_label}.")
            return
        with st.spinner("Construyendo turnos abstractos y asignando al personal…"):
            turnos = build_shifts(df_dia, **cfg_turnos)
        if turnos.empty:
            st.warning(
                f"No se pudieron construir turnos con la configuración "
                f"actual para {dia_label}."
            )
            return
        st.session_state["asig_turnos"] = turnos
        resultado = asignar_turnos_a_personas(
            turnos, roster, fecha=fecha,
            reset_state=usar_estado_limpio,
        )
        resultado["cfg_turnos"] = cfg_turnos
        resultado["dia_sel"] = dia_sel
        resultado["fecha"] = fecha
        st.session_state["asig_resultado"] = resultado

    resultado = st.session_state["asig_resultado"]
    if resultado is None:
        st.info("Ajusta los parámetros y pulsa **Asignar turnos al personal** "
                "para ejecutar el motor.")
        return

    _render_resultados(resultado, roster, fecha, dia_sel)


def _render_resultados(resultado: dict, roster: list[Persona],
                       fecha: dt.date, dia_sel: str):
    """Renderiza el resultado de la asignación: KPIs, Gantt, descubiertos."""
    st.markdown("---")
    st.markdown(f"##### 📊 Resultado de la asignación — {fecha.isoformat()} ({dia_sel})")

    stats = resultado["stats"]
    n_total = stats["n_turnos"]
    n_ok = stats["n_asignados_completos"]
    n_sin_maq = stats["n_sin_maquinista"]
    n_sin_ayu = stats["n_sin_ayudante"]
    n_maq_disp = stats["n_maq_disponibles"]
    n_ayu_disp = stats["n_ayu_disponibles"]

    k1, k2, k3, k4, k5 = st.columns(5)
    k1.metric("Turnos totales", n_total)
    k2.metric("Cobertura completa", f"{n_ok}/{n_total}",
              f"{(n_ok/n_total*100) if n_total else 0:.0f}%")
    k3.metric("Sin maquinista", n_sin_maq,
              delta_color="inverse" if n_sin_maq else "off")
    k4.metric("Sin ayudante", n_sin_ayu,
              delta_color="inverse" if n_sin_ayu else "off")
    k5.metric("Maquinistas disp.", f"{n_maq_disp} / "
              f"{sum(1 for p in roster if p.rol == 'Maquinista')}")

    if n_sin_maq == 0 and n_sin_ayu == 0:
        st.success(
            f"✅ **Cobertura completa:** los {n_ok} turnos del día "
            f"{fecha.isoformat()} se asignaron respetando el reposo de 10h "
            f"y el régimen 6x1, con {n_maq_disp} maquinistas y "
            f"{n_ayu_disp} ayudantes disponibles.",
            icon="✅",
        )
    else:
        st.error(
            f"⚠️ **Cobertura incompleta:** {n_sin_maq} turno(s) sin "
            f"maquinista y {n_sin_ayu} sin ayudante. El motor no cubre "
            f"turnos violando las 10h de reposo ni el régimen 6x1; "
            f"revisa ausencias, aumenta la dotación o ajusta el itinerario.",
            icon="⚠️",
        )

    # ---- Gantt por persona ----
    st.markdown("##### Diagrama de Gantt por persona")
    asignaciones_ok = [a for a in resultado["asignaciones"]
                       if a["maquinista_id"] or a["ayudante_id"]]
    if not asignaciones_ok:
        st.info("No hay turnos asignados para graficar.")
    else:
        filas = []
        for a in asignaciones_ok:
            for rol, pid_key, nombre_key in [
                ("Maquinista", "maquinista_id", "maquinista_nombre"),
                ("Ayudante", "ayudante_id", "ayudante_nombre"),
            ]:
                if a[pid_key]:
                    filas.append({
                        "Persona": f"{a[pid_key]} ({a[nombre_key]})",
                        "Rol": rol,
                        "Turno": a["turno"],
                        "Inicio": FECHA_REF + dt.timedelta(seconds=a["h_inicio_s"]),
                        "Fin": FECHA_REF + dt.timedelta(seconds=a["h_termino_s"]),
                        "Jornada (h)": round(a["jornada_s"] / 3600, 2),
                    })
        df_g = pd.DataFrame(filas)
        fig = px.timeline(
            df_g, x_start="Inicio", x_end="Fin", y="Persona",
            color="Rol", color_discrete_map={
                "Maquinista": "#1F4E78", "Ayudante": "#E89B3F"},
            hover_data=["Turno", "Jornada (h)"],
        )
        fig.update_yaxes(autorange="reversed", title=None)
        fig.update_xaxes(title=None, tickformat="%H:%M",
                         dtick=60 * 60 * 1000, gridcolor="#EEEEEE")
        fig.update_layout(
            height=max(420, 22 * df_g["Persona"].nunique() + 80),
            margin=dict(l=20, r=20, t=20, b=30),
            plot_bgcolor="white", legend_title_text="",
        )
        st.plotly_chart(fig, use_container_width=True,
                        key="asig_gantt_persona")

    # ---- Tabla de asignaciones ----
    with st.expander("📋 Ver tabla detallada de asignaciones", expanded=False):
        df_asg = pd.DataFrame([{
            "Turno": a["turno"],
            "Inicio": _fmt_h(a["h_inicio_s"]),
            "Término": _fmt_h(a["h_termino_s"]),
            "Jornada": _fmt_dur(a["jornada_s"]),
            "Maquinista": a["maquinista_nombre"] or "—",
            "Ayudante": a["ayudante_nombre"] or "—",
            "Trenes": a["trenes"],
            "Servicios": a["servicios"],
        } for a in resultado["asignaciones"]])
        st.dataframe(df_asg, use_container_width=True, hide_index=True,
                     height=min(450, 35 * (len(df_asg) + 1) + 40))

    # ---- Turnos descubiertos ----
    if resultado["sin_maquinista"] or resultado["sin_ayudante"]:
        st.markdown("##### ⚠️ Turnos descubiertos (no cubiertos)")
        st.caption(
            "El motor dejó estos turnos sin asignar porque, al momento de "
            "evaluarlos, ningún candidato del rol cumplía las 10h de reposo "
            "o el régimen 6x1, o estaba marcado como ausente. La política es "
            "**dejar el turno descubierto y reportarlo**, no violar reglas."
        )
        c1, c2 = st.columns(2)
        with c1:
            st.markdown("**Sin maquinista:**")
            if resultado["sin_maquinista"]:
                st.dataframe(
                    pd.DataFrame([{
                        "Turno": x["turno"],
                        "Inicio": _fmt_h(x["h_inicio_s"]),
                        "Término": _fmt_h(x["h_termino_s"]),
                        "Motivo": x["motivo"][:120] + ("…" if len(x["motivo"]) > 120 else ""),
                    } for x in resultado["sin_maquinista"]]),
                    use_container_width=True, hide_index=True, height=250,
                )
            else:
                st.success("Todos los turnos tienen maquinista.")
        with c2:
            st.markdown("**Sin ayudante:**")
            if resultado["sin_ayudante"]:
                st.dataframe(
                    pd.DataFrame([{
                        "Turno": x["turno"],
                        "Inicio": _fmt_h(x["h_inicio_s"]),
                        "Término": _fmt_h(x["h_termino_s"]),
                        "Motivo": x["motivo"][:120] + ("…" if len(x["motivo"]) > 120 else ""),
                    } for x in resultado["sin_ayudante"]]),
                    use_container_width=True, hide_index=True, height=250,
                )
            else:
                st.success("Todos los turnos tienen ayudante.")

    # ---- Distribución de carga ----
    st.markdown("##### Distribución de carga entre el personal")
    df_carga = []
    for p in roster:
        if p.turnos_asignados:
            jornada = sum(t["jornada_s"] for t in p.turnos_asignados) / 3600
            df_carga.append({
                "ID": p.id, "Rol": p.rol, "Nombre": p.nombre,
                "N° turnos": len(p.turnos_asignados),
                "Jornada total (h)": round(jornada, 2),
                "Disponible": p.disponible,
            })
    if df_carga:
        df_c = pd.DataFrame(df_carga)
        c1, c2 = st.columns([2, 1])
        with c1:
            st.caption(
                f"**{len(df_c)} personas fueron asignadas** en este día. "
                "La distribución es uniforme: el motor elige al candidato "
                "con mayor tiempo desde su última asignación (fairness)."
            )
            st.dataframe(
                df_c.sort_values(["Rol", "Jornada total (h)"],
                                  ascending=[True, False]),
                use_container_width=True, hide_index=True, height=350,
            )
        with c2:
            # Histograma de horas trabajadas
            fig = px.histogram(
                df_c, x="Jornada total (h)", color="Rol",
                color_discrete_map={"Maquinista": "#1F4E78",
                                     "Ayudante": "#E89B3F"},
                nbins=15, barmode="overlay", opacity=0.7,
            )
            fig.update_layout(
                height=300, margin=dict(l=10, r=10, t=20, b=30),
                plot_bgcolor="white", legend_title_text="",
            )
            fig.update_xaxes(gridcolor="#EEEEEE", title="Horas trabajadas")
            fig.update_yaxes(gridcolor="#EEEEEE", title="Personas")
            st.plotly_chart(fig, use_container_width=True,
                            key="asig_hist_carga")

    # ---- Simulación rápida de ausencia ----
    st.markdown("---")
    st.markdown("##### 🦠 Simulación rápida: ¿qué pasa si alguien se enferma?")
    st.caption(
        "Selecciona personas adicionales como ausentes **en este mismo día** "
        "y vuelve a ejecutar. Útil para evaluar la resiliencia del roster."
    )
    disponibles = [p for p in roster
                   if p.disponible and not p.esta_ausente(fecha)[0]]
    nuevas_ausencias = st.multiselect(
        "Marcar ausentes adicionales (en este día)",
        options=[f"{p.id} · {p.nombre}" for p in disponibles],
        key="asig_sim_aus",
    )
    if nuevas_ausencias and st.button("🦠 Aplicar y reasignar",
                                       use_container_width=True):
        for m in nuevas_ausencias:
            pid = m.split(" · ")[0]
            p = next((p for p in roster if p.id == pid), None)
            if p:
                p.ausencias[fecha] = "Licencia médica (simulada)"
        st.session_state["asig_resultado"] = None
        st.rerun()


# =============================================================================
# Utilidades de formato
# =============================================================================

def _fmt_h(seg) -> str:
    if seg is None or pd.isna(seg):
        return ""
    seg = int(seg) % 86400
    return f"{seg // 3600:02d}:{(seg % 3600) // 60:02d}"


def _fmt_dur(seg) -> str:
    if seg is None or pd.isna(seg):
        return ""
    seg = int(seg)
    return f"{seg // 3600}:{(seg % 3600) // 60:02d}"


# =============================================================================
# Entry point
# =============================================================================

def render_asignacion(services_df: pd.DataFrame) -> None:
    """Pestaña de asignación de turnos a personal simulado."""
    _init_state()
    roster = _editable_roster()
    st.markdown("---")
    _seccion_ausencias(roster)
    st.markdown("---")
    cfg_turnos = _seccion_parametros_turnos()
    st.markdown("---")
    _seccion_dia_y_asignacion(roster, cfg_turnos, services_df)

    # Documentación
    st.markdown("---")
    with st.expander("📚 Reglas y notas del motor de asignación"):
        st.markdown(f"""
**Reglas validadas por el motor:**

1. **Reposo entre jornadas ≥ 10 h** (configurable en el código,
   constante `REPOSO_MIN_S = {REPOSO_MIN_S}` s = {REPOSO_MIN_H} h).
   Se mide entre el **fin** del último turno de la persona
   (última llegada + traslado final + cierre) y el **inicio de trabajo**
   del nuevo turno (traslado inicial + revisión + buffer de relevo).
2. **Régimen 6x1 estricto**: una persona que ya acumuló 6 días
   consecutivos de trabajo *debe* descansar el 7°. No se asigna otro
   turno ese día.
3. **Disponibilidad**: respeta la marca global por persona y las
   ausencias puntuales por fecha. Cualquier ausencia es bloqueante.
4. **Sin restricción de certificación**: cualquier maquinista opera
   cualquier equipo (SFE, SFB, UT). Cualquier ayudante va con cualquier
   maquinista. Esto es una hipótesis explícita de la simulación.
5. **Roles desacoplados (Opción B)**: el motor asigna maquinistas y
   ayudantes de forma **independiente**; un turno puede tener
   maquinista MQ-007 y ayudante AY-031, y al día siguiente las
   combinaciones cambian. No hay "pareja fija".
6. **Conducta ante falta de personal**: si para un turno no hay
   candidato válido del rol, el turno queda **DESCUBIERTO** y se
   reporta en la sección "Turnos descubiertos". El motor *no* viola
   reglas para cubrir turnos.

**Algoritmo:**

Es un **greedy con fairness**:
  - Los turnos se procesan en orden cronológico de inicio.
  - Para cada turno y cada rol, el motor filtra candidatos que pasan
    la validación (10h + 6x1 + disponibilidad).
  - Entre los válidos, elige al de **mayor tiempo desde su última
    asignación** (o +∞ si nunca ha trabajado). Esto reparte la carga
    uniformemente y reduce futuras violaciones de reposo.

**Limitaciones (y cómo prepararse para un solver formal):**

- El greedy es razonable para el orden de magnitud actual (≈20-30
  turnos/día, 50+50 personas), pero empieza a quedar corto si
  aparecen más restricciones (antigüedad, equidad de horas noche,
  preferencias individuales, etc.). En ese caso, conviene migrar a
  un solver de programación entera (PuLP / OR-Tools).
- El motor no considera aún preferencias por equipo, equilibrio de
  horas anuales ni feriados. Son extensiones naturales.
- Cuando exista la planilla real de personal, basta con reemplazar
  `generar_roster()` por una carga de CSV/Excel; el resto del motor
  no cambia.
""")
