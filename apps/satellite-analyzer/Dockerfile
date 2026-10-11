FROM ubuntu:resolute

ENV DEBIAN_FRONTEND=noninteractive

# SatDump (気象衛星QPSKデコーダ), gr-satellites (CubeSat BPSKデコーダ), direwolf (APRS AFSKデコーダ)
RUN apt-get update && apt-get install -y --no-install-recommends \
    satdump \
    satdump-data \
    gr-satellites \
    direwolf \
    python3 \
    python3-pip \
    python3-numpy \
    python3-scipy \
    python3-matplotlib \
    libsndfile1 \
    ca-certificates \
    curl \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
RUN pip3 install --no-cache-dir --break-system-packages -r requirements.txt

COPY src/ ./src/
COPY assets/satdump_tles.txt /root/.config/satdump/satdump_tles.txt

ENV PYTHONPATH=/app/src
ENV PYTHONUNBUFFERED=1

CMD ["python3", "src/worker.py"]
