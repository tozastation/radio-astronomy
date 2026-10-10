# ==============================================================================
# Stage 1: Build (Rust Bookworm)
# ==============================================================================
FROM rust:bookworm AS builder

WORKDIR /workspace

# ワークスペース定義とクレートのコピー
COPY Cargo.toml Cargo.lock ./
COPY apps/satellite-tracker-rs ./apps/satellite-tracker-rs
COPY apps/ground-station/Cargo.toml ./apps/ground-station/Cargo.toml
RUN mkdir -p apps/ground-station/src && touch apps/ground-station/src/lib.rs

# satellite-tracker-rs のリリースビルド
RUN cargo build --release --package satellite-tracker-rs --bin satellite-tracker-rs

# ==============================================================================
# Stage 2: Runtime (Minimal Debian Bookworm Slim, ~40MB)
# ==============================================================================
FROM debian:bookworm-slim

# RTL-SDR v4 実機アクセス用ライブラリおよび CA 証明書
RUN apt-get update && apt-get install -y --no-install-recommends \
    ca-certificates \
    librtlsdr0 \
    usbutils \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# ビルド済みバイナリの配置
COPY --from=builder /workspace/target/release/satellite-tracker-rs /usr/local/bin/satellite-tracker-rs

ENV METRICS_PORT=9100
ENV UPDATE_INTERVAL_SEC=2
ENV SPOOL_DIR=/tmp/spool
ENV BALCONY_FACING=NORTH

EXPOSE 9100

CMD ["satellite-tracker-rs"]
