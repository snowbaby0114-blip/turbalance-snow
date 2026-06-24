FROM python:3.11-slim

WORKDIR /app

RUN pip install --no-cache-dir kubernetes

COPY scheduler.py .

CMD ["python", "scheduler.py"]
