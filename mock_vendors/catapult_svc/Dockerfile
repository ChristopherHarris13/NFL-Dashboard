# Build context is mock_vendors/ so the shared common/ package (and the
# baked-in player seed parquet) can be copied into the image.
FROM python:3.11-slim
WORKDIR /app
COPY requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r /app/requirements.txt
COPY __init__.py dirt_config.yaml /app/mock_vendors/
COPY common /app/mock_vendors/common
COPY catapult_svc /app/mock_vendors/catapult_svc
ENV PORT=8001
CMD ["sh", "-c", "uvicorn mock_vendors.catapult_svc.app:app --host 0.0.0.0 --port ${PORT}"]
