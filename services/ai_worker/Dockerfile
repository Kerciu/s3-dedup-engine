FROM python:3.12-slim
WORKDIR /app

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

COPY . .

RUN pip install --no-cache-dir "grpcio>=1.84.0" "grpcio-tools>=1.84.0"

EXPOSE 50051
ENTRYPOINT ["python", "main.py"]
