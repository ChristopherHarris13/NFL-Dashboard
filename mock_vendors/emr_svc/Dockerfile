FROM python:3.11-slim
WORKDIR /app
COPY requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r /app/requirements.txt
COPY __init__.py dirt_config.yaml /app/mock_vendors/
COPY common /app/mock_vendors/common
COPY emr_svc /app/mock_vendors/emr_svc
ENV PORT=8005
CMD ["sh", "-c", "uvicorn mock_vendors.emr_svc.app:app --host 0.0.0.0 --port ${PORT}"]
