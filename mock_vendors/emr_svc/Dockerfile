# Build from the mock_vendors/ directory so common/ and dirt_config.yaml are in context:
#   docker build -f emr_svc/Dockerfile -t emr_svc mock_vendors/
FROM python:3.11-slim

WORKDIR /app
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PORT=8005

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY dirt_config.yaml .
COPY common ./common
COPY emr_svc ./emr_svc

EXPOSE 8005
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s \
  CMD python -c "import urllib.request,os,sys; sys.exit(0 if urllib.request.urlopen(f'http://127.0.0.1:{os.environ[\"PORT\"]}/health', timeout=3).status == 200 else 1)"

CMD ["sh", "-c", "uvicorn emr_svc.main:app --host 0.0.0.0 --port ${PORT}"]
