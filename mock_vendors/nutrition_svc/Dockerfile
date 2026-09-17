FROM python:3.11-slim
WORKDIR /app
COPY requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r /app/requirements.txt
COPY __init__.py dirt_config.yaml /app/mock_vendors/
COPY common /app/mock_vendors/common
COPY nutrition_svc /app/mock_vendors/nutrition_svc
ENV PORT=8004
CMD ["sh", "-c", "uvicorn mock_vendors.nutrition_svc.app:app --host 0.0.0.0 --port ${PORT}"]
