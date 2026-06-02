@echo off
cd /d "%~dp0"
if not exist ".venv" (
  echo Creando entorno virtual...
  python -m venv .venv
)
call .venv\Scripts\activate
echo Instalando dependencias...
pip install -q -r requirements.txt
echo Iniciando aplicacion...
streamlit run app.py
