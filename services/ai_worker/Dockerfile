FROM python:3.12-slim
WORKDIR /app

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

COPY . .

RUN pip install --no-cache-dir grpcio grpcio-tools

EXPOSE 50051
ENTRYPOINT ["python", "main.py"]
