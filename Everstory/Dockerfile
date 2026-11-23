# Everstory container image.
#
#   docker build -t everstory .
#   docker run -p 8000:8000 --env-file .env -v everstory-data:/data everstory
#
# Match the Python version you develop with if you like (see requirements.txt).
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    EVERSTORY_DATA_DIR=/data \
    PORT=8000

WORKDIR /app

COPY requirements.txt requirements-prod.txt ./
RUN pip install --no-cache-dir -r requirements-prod.txt

COPY . .

# Run as an ordinary user, never as root.
RUN useradd --create-home --uid 10001 everstory \
    && mkdir -p /data \
    && chown -R everstory:everstory /data /app

USER everstory

# The database and uploads live here. Always mount a volume on it,
# otherwise your diary disappears when the container is replaced.
VOLUME ["/data"]

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD python -c "import os,urllib.request as u; u.urlopen('http://127.0.0.1:%s/healthz' % os.environ.get('PORT','8000'), timeout=4)"

CMD ["gunicorn", "wsgi:app", "-c", "gunicorn.conf.py"]
