FROM python:3.12-slim

RUN pip install --quiet flask beautifulsoup4 requests cloudscraper gunicorn

WORKDIR /app
COPY app.py                /app/app.py
COPY parkrun_summary.py /app/parkrun_summary.py

VOLUME ["/data/summaries"]

EXPOSE 8767

CMD ["gunicorn", "--bind", "0.0.0.0:8767", "--workers", "4", "--timeout", "300", "app:app"]
