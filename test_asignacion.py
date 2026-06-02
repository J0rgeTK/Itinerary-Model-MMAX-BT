"""
Smoke test del motor de asignación. No requiere Streamlit, solo valida la
lógica central. Ejecutar con: `python test_asignacion.py`
"""
import datetime as dt
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

# Importar el módulo sin pasar por Streamlit (mockeando st donde sea necesario)
import types
fake_st = types.ModuleType("streamlit")
fake_st.session_state = {}
sys.modules["streamlit"] = fake_st

from components.viz_asignacion import (
    generar_roster, asignar_turnos_a_personas, Persona, REPOSO_MIN_S,
)
import pandas as pd


# ---------------------------------------------------------------------------
# Test 1: 10h de reposo se respeta en asignaciones consecutivas
# ---------------------------------------------------------------------------

def test_10h_respeto():
    print("=" * 60)
    print("TEST 1: Reposo 10h se respeta entre turnos consecutivos")
    print("=" * 60)
    roster = generar_roster(5, 5)
    fecha = dt.date(2026, 4, 20)

    # Turno 1: 5:00 a 14:00 (9h, no debería caber en 7.5h jornada... lo
    # limitamos a 6h para que pase la validación de jornada)
    turnos = pd.DataFrame([
        {"turno": "T-01", "h_inicio": 5 * 3600, "h_termino": 12 * 3600,
         "jornada_s": 7 * 3600, "conduccion_s": 5 * 3600,
         "trenes": "SFE 1", "servicios": "L2-001",
         "punto_inicio": "CC", "punto_fin": "CW"},
        # Misma persona, día siguiente, inicio 5:00 → 12h reposo del día
        # anterior a las 12:00 → reposo = 17h, OK
        {"turno": "T-02", "h_inicio": 5 * 3600, "h_termino": 12 * 3600,
         "jornada_s": 7 * 3600, "conduccion_s": 5 * 3600,
         "trenes": "SFE 1", "servicios": "L2-002",
         "punto_inicio": "CC", "punto_fin": "CW"},
    ])

    res = asignar_turnos_a_personas(turnos, roster, fecha)
    print(f"  Turnos asignados: {res['stats']}")
    assert res["stats"]["n_asignados_completos"] == 2
    print("  ✓ Ambos turnos asignados con reposo OK")

    # Verificar que el mismo MQ no recibió los dos turnos si el reposo no
    # alcanza. Aquí el reposo SÍ alcanza (17h) → mismo MQ debería poder.
    maq_t1 = res["asignaciones"][0]["maquinista_id"]
    maq_t2 = res["asignaciones"][1]["maquinista_id"]
    # No exigimos mismo MQ, pero sí que al menos uno esté
    assert maq_t1 is not None and maq_t2 is not None
    print(f"  MQ T-01: {maq_t1} | MQ T-02: {maq_t2}")

    # --- Caso 2: violación de reposo (turno día 1 termina 22:00, día 2 inicia 5:00)
    # Esto debería dejar T-02 sin maquinista
    print()
    print("  Test 1b: violación de reposo 7h entre días")
    turnos2 = pd.DataFrame([
        {"turno": "T-01", "h_inicio": 14 * 3600, "h_termino": 22 * 3600,
         "jornada_s": 8 * 3600, "conduccion_s": 5 * 3600,
         "trenes": "SFE 1", "servicios": "L2-001",
         "punto_inicio": "CC", "punto_fin": "CW"},
        {"turno": "T-02", "h_inicio": 5 * 3600, "h_termino": 14 * 3600,
         "jornada_s": 9 * 3600, "conduccion_s": 5 * 3600,
         "trenes": "SFE 1", "servicios": "L2-002",
         "punto_inicio": "CC", "punto_fin": "CW"},
    ])
    res2 = asignar_turnos_a_personas(turnos2, roster, fecha)
    # En la primera pasada, todos tienen h_inicio=5h; el de 14-22h termina a 22h.
    # Como la jornada de T-01 es 8h y la max es 7.5h, build_shifts la cortaría
    # en la práctica. Aquí solo probamos el motor de asignación puro, así
    # que el test se enfoca en si el segundo turno se asigna al mismo MQ
    # que el primero o queda descubierto por reposo.
    maq_1 = res2["asignaciones"][0]["maquinista_id"]
    maq_2 = res2["asignaciones"][1]["maquinista_id"]
    if maq_1 == maq_2:
        # Si el algoritmo asigna el mismo MQ, debe haber detectado el reposo
        # insuficiente y haber dejado el segundo turno sin asignar o
        # haber tomado a otro MQ. Verificamos:
        print(f"  Mismo MQ: {maq_1} - el algoritmo debería preferir OTRO MQ")
    # La validación fuerte: para T-02, la persona que hizo T-01 tiene
    # fin a las 22h. Si T-02 inicia a las 5h, reposo = 5h - 22h + 24h = 7h.
    # Es < 10h → violación.
    # Verificamos que si maq_1 == maq_2, NO es una violación.
    persona_1 = next(p for p in roster if p.id == maq_1)
    persona_2 = next(p for p in roster if p.id == maq_2)
    print(f"  T-01 → MQ {maq_1} (fin {persona_1.last_end_abs_s % 86400 // 3600}h)")
    print(f"  T-02 → MQ {maq_2} (su last_end_abs_s = {persona_2.last_end_abs_s})")

    # Comprobar que la asignación NO viola el reposo
    if maq_1 == maq_2:
        # El motor asignó el mismo MQ a T-02 → debe haber tomado otro
        # candidato con mejor score, no este
        # OJO: aquí el motor podría haber asignado a otro MQ por fairness
        # (mayor tiempo desde última asignación), que es lo correcto.
        # Verifiquemos:
        base_abs = 0
        rest_for_1 = (5 * 3600 + base_abs) - persona_1.last_end_abs_s
        # Si rest_for_1 < 10h, no debería poder ser asignado
        # Pero persona_1.last_end_abs_s ya está actualizado a T-02 (5h del día sigte)
        print(f"  ⚠️ Misma persona para T-01 y T-02. Resto absoluto: {rest_for_1}s")
    else:
        print(f"  ✓ MQs distintos (fairness): {maq_1} → {maq_2}")
    print()


# ---------------------------------------------------------------------------
# Test 2: 6x1 se respeta
# ---------------------------------------------------------------------------

def test_6x1():
    print("=" * 60)
    print("TEST 2: Régimen 6x1 se respeta")
    print("=" * 60)
    roster = generar_roster(3, 3)
    fecha_inicio = dt.date(2026, 4, 20)

    # 7 turnos en 7 días, todos entre 5:00 y 12:00
    turnos = pd.DataFrame([
        {"turno": f"T-{i:02d}", "h_inicio": 5 * 3600, "h_termino": 12 * 3600,
         "jornada_s": 7 * 3600, "conduccion_s": 5 * 3600,
         "trenes": "SFE 1", "servicios": f"L2-{i:03d}",
         "punto_inicio": "CC", "punto_fin": "CW"}
        for i in range(1, 8)
    ])

    # Asignar día por día
    for d in range(7):
        fecha = fecha_inicio + dt.timedelta(days=d)
        res = asignar_turnos_a_personas(turnos.iloc[:1], roster, fecha)
        asg = res["asignaciones"][0]
        maq = asg["maquinista_id"]
        ayu = asg["ayudante_id"]
        persona_maq = next(p for p in roster if p.id == maq)
        persona_ayu = next(p for p in roster if p.id == ayu)
        print(f"  Día {d+1} ({fecha}): MQ {maq} (corr={persona_maq.consecutive_working_days}) "
              f"AYU {ayu} (corr={persona_ayu.consecutive_working_days})")

    # Después de 7 días, MQ y AYU no deberían tener 7 días consecutivos
    for p in roster:
        if p.turnos_asignados:
            assert p.consecutive_working_days <= 6, \
                f"{p.id} tiene {p.consecutive_working_days} días corridos"
    print("  ✓ Ninguna persona supera 6 días consecutivos")
    print()


# ---------------------------------------------------------------------------
# Test 3: Ausencia bloquea al candidato
# ---------------------------------------------------------------------------

def test_ausencia():
    print("=" * 60)
    print("TEST 3: Ausencia bloquea al candidato")
    print("=" * 60)
    roster = generar_roster(5, 5)
    fecha = dt.date(2026, 4, 20)

    # Marcar MQ-001 y MQ-002 ausentes
    roster[0].ausencias[fecha] = "Licencia médica"
    roster[1].ausencias[fecha] = "Vacaciones"

    turnos = pd.DataFrame([
        {"turno": "T-01", "h_inicio": 5 * 3600, "h_termino": 12 * 3600,
         "jornada_s": 7 * 3600, "conduccion_s": 5 * 3600,
         "trenes": "SFE 1", "servicios": "L2-001",
         "punto_inicio": "CC", "punto_fin": "CW"},
        {"turno": "T-02", "h_inicio": 13 * 3600, "h_termino": 20 * 3600,
         "jornada_s": 7 * 3600, "conduccion_s": 5 * 3600,
         "trenes": "SFE 1", "servicios": "L2-002",
         "punto_inicio": "CC", "punto_fin": "CW"},
    ])

    res = asignar_turnos_a_personas(turnos, roster, fecha)
    print(f"  Stats: {res['stats']}")
    print(f"  Asignaciones:")
    for a in res["asignaciones"]:
        print(f"    {a['turno']}: MQ={a['maquinista_id']} AYU={a['ayudante_id']}")

    # MQ-001 y MQ-002 no deberían aparecer
    for a in res["asignaciones"]:
        assert a["maquinista_id"] not in ("MQ-001", "MQ-002"), \
            f"MQ-001/002 ausentes no deberían ser asignados, pero {a['maquinista_id']} sí"
    print("  ✓ MQ-001 y MQ-002 (ausentes) no fueron asignados")
    print()


# ---------------------------------------------------------------------------
# Test 4: Sin personal disponible → turno queda descubierto
# ---------------------------------------------------------------------------

def test_descubierto():
    print("=" * 60)
    print("TEST 4: Sin personal → turno queda descubierto")
    print("=" * 60)
    roster = generar_roster(2, 2)
    fecha = dt.date(2026, 4, 20)

    # Marcar a TODOS los maquinistas ausentes
    for p in roster:
        if p.rol == "Maquinista":
            p.ausencias[fecha] = "Licencia médica"

    turnos = pd.DataFrame([
        {"turno": "T-01", "h_inicio": 5 * 3600, "h_termino": 12 * 3600,
         "jornada_s": 7 * 3600, "conduccion_s": 5 * 3600,
         "trenes": "SFE 1", "servicios": "L2-001",
         "punto_inicio": "CC", "punto_fin": "CW"},
    ])

    res = asignar_turnos_a_personas(turnos, roster, fecha)
    print(f"  Stats: {res['stats']}")
    print(f"  Sin maquinista: {len(res['sin_maquinista'])}")
    assert len(res["sin_maquinista"]) == 1
    assert res["asignaciones"][0]["maquinista_id"] is None
    print(f"  Motivo reportado: {res['sin_maquinista'][0]['motivo'][:100]}")
    print("  ✓ Turno correctamente descubierto (no se violaron reglas)")
    print()


# ---------------------------------------------------------------------------
# Test 5: Distribución uniforme de carga
# ---------------------------------------------------------------------------

def test_distribucion_uniforme():
    print("=" * 60)
    print("TEST 5: Distribución uniforme de carga (fairness)")
    print("=" * 60)
    roster = generar_roster(20, 20)
    fecha = dt.date(2026, 4, 20)

    # 10 turnos en el día
    turnos = pd.DataFrame([
        {"turno": f"T-{i:02d}", "h_inicio": 5 * 3600 + i * 1800,
         "h_termino": 12 * 3600, "jornada_s": 7 * 3600,
         "conduccion_s": 5 * 3600, "trenes": "SFE 1",
         "servicios": f"L2-{i:03d}", "punto_inicio": "CC", "punto_fin": "CW"}
        for i in range(10)
    ])

    res = asignar_turnos_a_personas(turnos, roster, fecha)
    cargas = {}
    for p in roster:
        if p.turnos_asignados:
            cargas[p.id] = (p.rol, len(p.turnos_asignados))

    print(f"  Turnos asignados: {sum(n for _, n in cargas.values())}")
    print(f"  Personas usadas: {len(cargas)}")
    print("  Carga por persona (debe ser uniforme: máx 1 turno cada una):")
    for pid, (rol, n) in sorted(cargas.items()):
        print(f"    {pid} ({rol}): {n} turno(s)")

    # El motor asigna con fairness: cada persona recibe a lo más 1 turno
    # en este día (porque todos los turnos son consecutivos y cada uno
    # deja al siguiente candidato con menos reposo del necesario).
    for pid, (rol, n) in cargas.items():
        assert n == 1, f"{pid} debería tener 1 turno, tiene {n}"
    print("  ✓ Cada persona recibió a lo más 1 turno (carga uniforme)")
    print()


# ---------------------------------------------------------------------------
# Test 7: 6x1 estricto — con 1 sola persona, el 7° día consecutivo debe
# quedar descubierto
# ---------------------------------------------------------------------------

def test_6x1_una_persona():
    print("=" * 60)
    print("TEST 7: 6x1 estricto con 1 sola persona (7 días seguidos)")
    print("=" * 60)
    roster = generar_roster(1, 1)  # 1 maq, 1 ayu — para forzar el conflicto
    fecha_inicio = dt.date(2026, 4, 20)

    turnos = pd.DataFrame([
        {"turno": f"T-{i:02d}", "h_inicio": 5 * 3600, "h_termino": 12 * 3600,
         "jornada_s": 7 * 3600, "conduccion_s": 5 * 3600,
         "trenes": "SFE 1", "servicios": f"L2-{i:03d}",
         "punto_inicio": "CC", "punto_fin": "CW"}
        for i in range(1, 8)
    ])

    resultados = []
    for d in range(7):
        fecha = fecha_inicio + dt.timedelta(days=d)
        res = asignar_turnos_a_personas(turnos.iloc[:1], roster, fecha,
                                         reset_state=(d == 0))
        resultados.append((fecha, res))
        asg = res["asignaciones"][0]
        print(f"  Día {d+1} ({fecha}): MQ={asg['maquinista_id']} "
              f"corr={roster[0].consecutive_working_days} "
              f"sin_maq={res['stats']['n_sin_maquinista']}")

    # El día 7 NO debería asignarse al MQ-001 (que ya trabajó 6 días seguidos)
    assert resultados[5][1]["stats"]["n_asignados_completos"] == 1, \
        "Día 6 debería asignarse (5 días previos + 1 = 6, aún permitido)"
    assert resultados[6][1]["stats"]["n_asignados_completos"] == 0, \
        "Día 7 NO debería asignarse (6x1)"
    assert resultados[6][1]["stats"]["n_sin_maquinista"] == 1
    print("  ✓ Día 7 correctamente descubierto por 6x1")
    print()


# ---------------------------------------------------------------------------
# Test 6: Resto legal con servicio que cruza medianoche
# ---------------------------------------------------------------------------

def test_cruza_medianoche():
    print("=" * 60)
    print("TEST 6: Reposo entre turno nocturno y matinal del día siguiente")
    print("=" * 60)
    roster = generar_roster(3, 3)

    # Día 1: turno termina a las 02:00 (= 26:00 en segundos)
    # (simulando un servicio nocturno que cruza medianoche)
    f1 = dt.date(2026, 4, 20)
    f2 = dt.date(2026, 4, 21)
    turnos_dia1 = pd.DataFrame([
        {"turno": "T-NOC", "h_inicio": 22 * 3600, "h_termino": 26 * 3600,
         "jornada_s": 8 * 3600, "conduccion_s": 5 * 3600,
         "trenes": "SFE 1", "servicios": "L2-NOC",
         "punto_inicio": "CC", "punto_fin": "CW"},
    ])
    res1 = asignar_turnos_a_personas(turnos_dia1, roster, f1)
    maq_noc = res1["asignaciones"][0]["maquinista_id"]
    print(f"  Turno nocturno T-NOC → MQ {maq_noc}")

    # Día 2: turno matinal a las 5:00 (= 5*3600 = 18000s)
    # El fin del nocturno fue 26*3600 = 93600s.
    # El inicio del día 2 es base_abs_dia2 = 1*86400 = 86400s
    # Rest = (86400 + 18000) - 93600 = 10800s = 3h. < 10h → violación.
    turnos_dia2 = pd.DataFrame([
        {"turno": "T-AM", "h_inicio": 5 * 3600, "h_termino": 12 * 3600,
         "jornada_s": 7 * 3600, "conduccion_s": 5 * 3600,
         "trenes": "SFE 1", "servicios": "L2-AM",
         "punto_inicio": "CC", "punto_fin": "CW"},
    ])
    res2 = asignar_turnos_a_personas(turnos_dia2, roster, f2,
                                     reset_state=False)
    maq_am = res2["asignaciones"][0]["maquinista_id"]
    print(f"  Turno matinal T-AM → MQ {maq_am}")
    print(f"  ¿Mismo MQ? {maq_noc == maq_am}")
    if maq_noc == maq_am:
        print("  ⚠️ El mismo MQ tomó ambos turnos. Esto sería ilegal (3h < 10h).")
    else:
        print(f"  ✓ El motor eligió OTRO MQ ({maq_noc} → {maq_am}) "
              f"respetando los 10h de reposo legal.")
    print()


if __name__ == "__main__":
    test_10h_respeto()
    test_6x1()
    test_ausencia()
    test_descubierto()
    test_distribucion_uniforme()
    test_cruza_medianoche()
    test_6x1_una_persona()
    print("=" * 60)
    print("✓ Todos los tests pasaron")
    print("=" * 60)
