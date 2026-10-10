FROM localhost:5000/satellite-analyzer-worker:v1.3.0

WORKDIR /app
COPY app.py .

ENV PYTHONUNBUFFERED=1
EXPOSE 8080

CMD ["python", "app.py"]
