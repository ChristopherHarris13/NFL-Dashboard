import sys
from pathlib import Path

# Make the plugins folder importable exactly as Airflow does (it puts
# AIRFLOW__CORE__PLUGINS_FOLDER on sys.path), so tests import `identity`
# the same way the DAG does.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
