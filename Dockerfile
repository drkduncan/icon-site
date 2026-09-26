# Tiny image: Python's standard library is the whole server.
FROM python:3.13-alpine

WORKDIR /app
COPY server/app.py /app/app.py
COPY index.html presets.js /app/static/

ENV STATIC_DIR=/app/static \
    DATA_DIR=/data \
    PORT=8080 \
    PYTHONUNBUFFERED=1

RUN mkdir -p /data/photos && chown -R 1000:1000 /data
USER 1000:1000
VOLUME /data
EXPOSE 8080

HEALTHCHECK --interval=30s --timeout=5s \
  CMD python -c "import urllib.request,sys; urllib.request.urlopen('http://127.0.0.1:8080/api/health', timeout=3)" || exit 1

CMD ["python", "/app/app.py"]
