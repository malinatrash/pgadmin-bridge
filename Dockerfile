FROM python:3.12-slim
WORKDIR /app
COPY pgadmin_bridge ./pgadmin_bridge
ENV PGADMIN_BRIDGE_DATA=/data
VOLUME /data
EXPOSE 5432 8080
ENTRYPOINT ["python3", "-m", "pgadmin_bridge", "--host", "0.0.0.0"]
