# 🚆 Biotren Itinerario Studio

Dashboard interactivo de visualización y análisis del itinerario operacional
de **Biotren** (Servicio L1 Laja–Talcahuano y L2 Concepción–Coronel).

Construido con **Streamlit + Plotly + Folium**.

---

## 🎯 Qué hace

| Pestaña | Para qué sirve |
|---|---|
| 📈 **Diagrama de Marey** | Vista distancia–tiempo. La pieza central. Identifica conflictos de cruzamiento, frecuencias y holguras visualmente. |
| 🗺️ **Mapa de la red** | Mapa geográfico (Folium) o esquemático (Plotly, sin internet) con estaciones y tramos. |
| 📊 **Indicadores** | Tarjetas KPI: servicios, trenes, pico simultáneo, headway, distribución por franja. |
| 🚂 **Rotación de trenes** | Gantt con la secuencia de servicios que opera cada SFE. |
| 👷 **Turnos teóricos** | Dimensionamiento: cuántos turnos abstractos se necesitan respetando jornada y conducción. |
| 🧑‍✈️ **Asignación de personal** | **Programación**: asigna cada turno a maquinistas y ayudantes concretos, validando **reposo 10h**, **6x1**, ausencias y sin certificación por equipo. |
| 🏢 **Ocupación de estaciones** | Gantt de ocupación de cada estación crítica. |
| 📋 **Catálogo de servicios** | Tabla filtrable con export a CSV. |

---

## ⚙️ Instalación local

### Requisitos
- Python 3.10 o superior

### Opción A — Lanzador automático
- **Linux / macOS**: ejecuta `./run.sh`
- **Windows**: doble clic en `run.bat`

### Opción B — Manual
```bash
cd biotren_app
python -m venv .venv
source .venv/bin/activate         # Linux / macOS
.venv\Scripts\activate            # Windows
pip install -r requirements.txt
streamlit run app.py
```
La app abre en `http://localhost:8501`.

---

## ☁️ Despliegue en GitHub + Streamlit Community Cloud

La aplicación está lista para desplegarse como app web pública o privada.

### Pasos
1. Crea un repositorio en GitHub y sube **el contenido de la carpeta
   `biotren_app/`** como raíz del repo (de modo que `app.py` quede en la raíz).
2. Entra a [share.streamlit.io](https://share.streamlit.io) e inicia sesión con GitHub.
3. Pulsa **New app**, elige el repositorio y la rama.
4. En *Main file path* indica `app.py`.
5. **Deploy**. Streamlit instala `requirements.txt` automáticamente y publica la app.

### Archivos de configuración incluidos
- `.python-version` / `runtime.txt` — fija Python 3.12.
- `.streamlit/config.toml` — tema visual y ajustes de servidor.
- `requirements.txt` — dependencias.

### ⚠️ Consideración de confidencialidad
Un repositorio **público** expone los datos del itinerario y la infraestructura
a cualquier persona. Antes de hacerlo público, **confirma con EFE Sur** si esa
información es liberable. Alternativas si es sensible:
- **Repositorio privado** + Streamlit Cloud (plan gratuito permite 1 app privada).
- **Despliegue en servidor propio** de la organización (Docker).
- **Repo público con datos de demostración**; los datos reales se cargan aparte.

La app **no contiene credenciales ni secretos**, por lo que no hay riesgo de
filtración de claves: el único punto a evaluar es la sensibilidad de los datos
operacionales.

---

## 🧑‍✈️ Asignación de personal (programación de turnos)

La pestaña **🧑‍✈️ Asignación de personal** es la pieza de **programación**
a gran escala, complementaria a **👷 Turnos teóricos** (que es solo
**dimensionamiento**). Permite:

1. **Generar un roster simulado** de N maquinistas + M ayudantes (por
   defecto 50+50, regenerable).
2. **Marcar ausencias puntuales** por fecha y motivo (licencia médica,
   vacaciones, capacitación, etc.) — y simular licencias repentinas con
   un botón rápido *"¿qué pasa si alguien se enferma?"*.
3. **Ejecutar el motor de asignación** que, para cada día seleccionado:
   - Construye los turnos abstractos del itinerario del día.
   - Asigna cada turno a un maquinista y un ayudante **desacoplados**
     (Opción B): un turno puede tener MQ-007 + AY-031, y al día
     siguiente combinaciones distintas.
   - Valida **reposo 10h** entre fin del último turno e inicio del nuevo.
   - Valida el **régimen 6x1** (1 día libre por semana, sin 7 días
     corridos).
   - **Si un turno no puede cubrirse respetando las reglas, queda
     descubierto y se reporta** (no se viola la normativa para cubrirlo).
4. **Visualizar el resultado**:
   - Gantt por persona con la jornada del día.
   - Tabla detallada de asignaciones.
   - Turnos descubiertos con el motivo exacto del rechazo.
   - Distribución de carga y horas trabajadas (verifica que la
     asignación sea uniforme).
   - KPIs de cobertura.

### Decisiones de diseño

- **Roles desacoplados (Opción B)**: maquinistas y ayudantes se asignan
  por separado. Esto modela la realidad donde el ayudante no tiene
  límite de conducción, solo de jornada.
- **Sin restricción de certificación**: cualquier persona opera cualquier
  equipo (SFE / SFB / UT). Esta hipótesis se ajustará cuando exista la
  planilla real con habilitaciones.
- **Algoritmo greedy con fairness**: para cada turno, el motor elige
  al candidato con **mayor tiempo desde su última asignación** dentro
  de los que pasan la validación. Esto distribuye la carga uniformemente
  y minimiza futuras violaciones de reposo.
- **Estado persistente**: el historial de cada persona (último turno,
  racha de días trabajados) se mantiene entre llamadas, lo que permite
  planificar día tras día y validar 10h entre jornadas consecutivas.

### Hallazgos típicos

Al ejecutar el motor con el itinerario vigente, es habitual detectar
que **algunos turnos vespertinos no pueden cubrirse** porque las
parejas que podrían tomarlos terminaron su turno matinal a las
10:30-11:30, dejando apenas 8-9h hasta un nuevo turno a las 19:00.
Esta es la clase de violación que la normativa exige prevenir y que el
modelo anterior no detectaba.

### Migración futura a un solver formal

El motor greedy es eficiente y extensible, pero al agregar más
restricciones (antigüedad, equilibrio anual de horas, preferencias,
HE estructural, etc.) conviene migrar a un solver de programación
entera (PuLP / OR-Tools). El modelo `Persona` y las funciones de
validación están diseñadas para ser reemplazables sin reescribir la
UI.

### Tests

`test_asignacion.py` valida el motor contra 7 escenarios clave:
reposo 10h, régimen 6x1, ausencias, descubiertos, fairness y
cruce de medianoche. Ejecutar con `python test_asignacion.py`.

---



**Toda la base de datos está en un único archivo Excel editable:**
`data/biotren_datos.xlsx`

Esto permite que un planificador edite el itinerario sin tocar código ni JSON.
El libro tiene 5 hojas:

| Hoja | Contenido | Filas |
|---|---|---|
| **LÉEME** | Instrucciones de edición | — |
| **Servicios** | Un servicio (tren-viaje) por fila: id, línea, sentido, tren, tipo, origen, destino, horas | 195 |
| **Horarios** | Un paso por fila: service_id, orden, estación, hora de llegada/salida, distancia | 2.347 |
| **Estaciones** | Infraestructura: rol, vías, andenes, cruzamiento, cochera, coordenadas | 35 |
| **Tramos** | Segmentos de vía: tipo (simple/doble), distancia, electrificación | 40 |

### Cómo editar
1. Abre `data/biotren_datos.xlsx` en Excel o LibreOffice.
2. Modifica las celdas (respeta los nombres de columna de la fila 1).
3. Guarda el archivo.
4. En la app, pulsa **🔄 Recargar datos del Excel** en la barra lateral.

### Reglas de integridad
- `Horarios.service_id` debe coincidir con un `Servicios.id`.
- `Horarios.estacion` debe coincidir con un nombre de la hoja `Estaciones`.
- `Horarios.orden` define la secuencia de paso (1 = estación de origen).
- Las celdas de hora usan formato `HH:MM`.

### Conversión a/desde JSON
Para respaldos o control de versiones legible:
```bash
python convertir_datos.py xlsx2json   # Excel  -> JSON
python convertir_datos.py json2xlsx   # JSON   -> Excel
```

---

## 📁 Estructura del proyecto

```
biotren_app/
├── app.py                      # Punto de entrada Streamlit
├── data_loader.py              # Carga de datos desde Excel (cacheada)
├── convertir_datos.py          # Conversor Excel <-> JSON
├── requirements.txt            # Dependencias
├── run.sh / run.bat            # Lanzadores rápidos
├── .python-version             # Versión de Python (deploy)
├── .streamlit/config.toml      # Tema y configuración
│
├── components/                 # Módulos de visualización
│   ├── viz_marey.py           # Diagrama de Marey
│   ├── viz_map.py             # Mapa (Folium + Plotly)
│   ├── viz_kpis.py            # Dashboard de KPIs
│   ├── viz_gantt.py           # Gantts (trenes + estaciones)
│   ├── viz_services.py        # Tabla de servicios
│   ├── viz_turnos.py          # Turnos teóricos (dimensionamiento)
│   ├── viz_asignacion.py      # Asignación de personal (programación)
│   └── viz_escenarios.py      # Generador de escenarios
│
└── data/
    ├── biotren_datos.xlsx     # ★ BASE DE DATOS PRINCIPAL (editable)
    ├── services_full.json     # Respaldo JSON (generado por el conversor)
    ├── infraestructura.json   # Respaldo JSON
    └── estaciones.csv         # Coordenadas originales (referencia)
```

---

## 🧩 Cómo extender la app

### Agregar una pestaña
1. Crea un módulo en `components/`, ej. `viz_parejas.py`, con una función
   `render_parejas(services_df, ...)`.
2. Importa la función en `app.py`.
3. Agrega un tab nuevo en `st.tabs(...)` y llama tu función dentro.

### Cargar otro itinerario (escenario alternativo)
Crea una copia de `data/biotren_datos.xlsx` con los servicios modificados,
o agrega un `st.file_uploader` en la barra lateral para cargar y comparar
escenarios.

### Simular cambios de infraestructura
Edita la hoja `Estaciones` o `Tramos` del Excel:
- Cambia `andenes_hab` para simular ampliación de andenes.
- Marca `cruzamiento = 1` en una estación intermedia para evaluar un nuevo apartadero.
- Cambia `n_vias_fisicas` de un tramo de 1 a 2 para simular desdoblamiento a doble vía.

---

## 🛣️ Roadmap

| Sprint | Funcionalidad |
|---|---|
| **2** | Detección automática de conflictos de cruzamiento + alertas en Marey |
| **3** | Carga de itinerarios alternativos para comparación de escenarios |
| **4** | Módulo de parejas integrado (turnos, jornadas, dotación) |
| **5** | Editor de parámetros operacionales en la propia interfaz |
| **6** | Optimizador MILP con Pyomo (resolución automática) |

---

## 🆘 Solución de problemas

**`ModuleNotFoundError: streamlit_folium`** → `pip install streamlit-folium`

**El mapa Folium aparece en blanco** → falta conexión a internet (descarga los
tiles). Usa el modo "Esquemático (Plotly)" que funciona sin internet.

**Error al leer `biotren_datos.xlsx`** → verifica que no se hayan cambiado los
nombres de las columnas ni de las hojas. Usa `convertir_datos.py json2xlsx`
para regenerar el archivo desde el respaldo JSON.

**Los cambios en el Excel no se reflejan** → pulsa "🔄 Recargar datos del Excel"
en la barra lateral, o recarga la página.

---

## 📝 Atribución

Datos del itinerario: Circular GOF-S N.º 2/419 vigente 20-abr-2026 · EFE Sur.
Estructura, código y diagramas: documento de trabajo interno.
