FROM python:3.11-slim

# direwolf (APRSデコーダ) およびシステムライブラリのインストール
RUN apt-get update && apt-get install -y --no-install-recommends \
    direwolf \
    libsndfile1 \
    ca-certificates \
    curl \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY src/ ./src/

ENV PYTHONPATH=/app/src
ENV PYTHONUNBUFFERED=1

CMD ["python", "src/worker.py"]
