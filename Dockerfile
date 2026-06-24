FROM python:3.11-slim

WORKDIR /app

RUN pip install --no-cache-dir kubernetes

COPY scheduler.py .

RUN useradd --no-create-home --uid 1000 scheduler
USER scheduler

CMD ["python", "scheduler.py"]
