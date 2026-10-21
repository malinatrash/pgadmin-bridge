FROM python:3.12-slim
WORKDIR /app
COPY *.py ui.html ./
ENV PGPROXY_DATA=/data
VOLUME /data
EXPOSE 5432 8080
ENTRYPOINT ["python3", "main.py", "--host", "0.0.0.0"]
