FROM apache/airflow:2.11.2-python3.12
# Everything the pipeline will need across all stages is baked in now so the
# image is built once: dbt + GX for Silver/Gold later, nfl_data_py for the
# nflverse feed. nfl_data_py pins pandas<2 / numpy<2, so it goes in with
# --no-deps after modern pandas (same trick as mock_vendors/common/build_seed.py).
COPY requirements.txt /requirements.txt
RUN pip install --no-cache-dir -r /requirements.txt \
 && pip install --no-cache-dir --no-deps nfl_data_py==0.3.3
