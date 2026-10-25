FROM localhost:5000/satellite-analyzer-worker:v1.5.0

WORKDIR /app
COPY app.py orbit_calculator.py .

ENV PYTHONUNBUFFERED=1
EXPOSE 8080

CMD ["python3", "app.py"]
