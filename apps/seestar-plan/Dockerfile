FROM python:3.12-slim

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY seestar_plan ./seestar_plan

ENV PORT=9101
EXPOSE 9101
CMD ["python", "-m", "seestar_plan.server"]
