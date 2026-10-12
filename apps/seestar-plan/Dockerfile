FROM python:3.12-slim

RUN apt-get update \
    && apt-get install -y --no-install-recommends fonts-noto-cjk \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY seestar_plan ./seestar_plan

ENV PORT=30089
EXPOSE 30089
CMD ["python", "-m", "seestar_plan.server"]
