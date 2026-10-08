FROM python:3.11-slim

# RTL-SDR ランタイムライブラリおよび必須ツールのインストール
RUN apt-get update && apt-get install -y --no-install-recommends \
    librtlsdr0 \
    librtlsdr-dev \
    usbutils \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# 依存パッケージのインストール
COPY apps/satellite-tracker/requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

# ソースコードの配置
COPY apps/satellite-tracker/src/ ./src/

ENV PYTHONPATH=/app/src
ENV PYTHONUNBUFFERED=1

EXPOSE 9100

CMD ["python", "src/main.py"]
