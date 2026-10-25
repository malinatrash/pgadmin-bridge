FROM python:3.12-slim

LABEL org.opencontainers.image.title="pgadmin-bridge" \
      org.opencontainers.image.description="PostgreSQL wire-protocol proxy over pgAdmin's Query Tool API" \
      org.opencontainers.image.source="https://github.com/malinatrash/pgadmin-bridge" \
      org.opencontainers.image.licenses="MIT"

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PGADMIN_BRIDGE_DATA=/data
RUN useradd --system --uid 10001 --home-dir /app bridge && install -d -o bridge -m 0700 /data
WORKDIR /app
COPY pgadmin_bridge ./pgadmin_bridge

USER bridge
VOLUME /data
EXPOSE 5432 8080
HEALTHCHECK --interval=30s --timeout=3s --start-period=5s \
  CMD ["python3", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8080/api/state', timeout=2)"]
ENTRYPOINT ["python3", "-m", "pgadmin_bridge", "--host", "0.0.0.0"]
