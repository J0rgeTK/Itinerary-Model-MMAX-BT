#!/usr/bin/env bash
# Lanzador rapido para Biotren Itinerario Studio
set -e
cd "$(dirname "$0")"
if [ ! -d ".venv" ]; then
  echo "Creando entorno virtual..."
  python3 -m venv .venv
fi
source .venv/bin/activate
echo "Instalando dependencias..."
pip install -q -r requirements.txt
echo "Iniciando aplicacion..."
streamlit run app.py
