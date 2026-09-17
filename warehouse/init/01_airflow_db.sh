#!/usr/bin/env bash
# Runs once on first boot of the warehouse container (empty data volume).
# Creates a separate database + role for Airflow's own metadata so the
# scheduler's bookkeeping never lives next to the medallion schemas.
set -euo pipefail

psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname "$POSTGRES_DB" <<-SQL
    CREATE ROLE ${AIRFLOW_DB_USER} WITH LOGIN PASSWORD '${AIRFLOW_DB_PASSWORD}';
    CREATE DATABASE ${AIRFLOW_DB_NAME} OWNER ${AIRFLOW_DB_USER};
SQL
