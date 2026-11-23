FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt \
    && groupadd --gid 10001 app \
    && useradd --uid 10001 --gid app --no-create-home app \
    && install -d -o app -g app /data
COPY batchbook ./batchbook
USER app

ENV BATCHBOOK_HOST=0.0.0.0 BATCHBOOK_DB=/data/batchbook.sqlite3
EXPOSE 8000
CMD ["python", "-m", "batchbook"]
