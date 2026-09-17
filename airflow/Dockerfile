FROM apache/airflow:3.3.1
# Extra Python deps for DAGs go in requirements.txt; the image already ships
# the postgres provider (psycopg2) and requests.
COPY requirements.txt /requirements.txt
RUN pip install --no-cache-dir -r /requirements.txt
