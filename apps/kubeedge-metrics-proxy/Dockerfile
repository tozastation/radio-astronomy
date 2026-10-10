FROM localhost:5000/satellite-analyzer-worker:dev

WORKDIR /app
COPY proxy.py .

ENV PYTHONUNBUFFERED=1
EXPOSE 10351

CMD ["python", "proxy.py"]
