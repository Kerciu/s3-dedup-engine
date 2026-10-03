FROM python:3.12-slim

WORKDIR /app

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    HF_HOME=/opt/huggingface \
    HF_HUB_DISABLE_TELEMETRY=1 \
    HF_HUB_DISABLE_SYMLINKS_WARNING=1

RUN apt-get update \
    && apt-get install -y --no-install-recommends libglib2.0-0 \
    && rm -rf /var/lib/apt/lists/*

RUN pip install --extra-index-url https://download.pytorch.org/whl/cpu "torch==2.9.1+cpu"

RUN pip install \
    "grpcio>=1.78.0" \
    "grpcio-tools>=1.78.0" \
    "huggingface-hub>=0.35.0" \
    "numpy>=2.1.0" \
    "opencv-python-headless>=4.10.0" \
    "pillow>=11.0.0" \
    "psycopg[binary]>=3.2.0" \
    "pypika>=0.51.1" \
    "rich>=14.0.0"

COPY const ./const
COPY logger.py ./
COPY embeddings ./embeddings
RUN python -c "from embeddings.sscd import get_embedder; get_embedder().load()"

COPY . .

EXPOSE 50051
ENTRYPOINT ["python", "main.py"]
