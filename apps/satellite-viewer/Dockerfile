FROM localhost:5000/satellite-analyzer-worker:v1.5.0

WORKDIR /app
RUN pip3 install --no-cache-dir --break-system-packages sgp4
COPY app.py orbit_calculator.py ./
COPY static/ ./static/

ENV PYTHONUNBUFFERED=1
EXPOSE 8080

CMD ["python3", "app.py"]
