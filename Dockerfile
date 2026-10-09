FROM python:3.12-slim
WORKDIR /app
COPY backend /app/backend
COPY public /app/public
ENV GREENAPI_DATA_DIR=/data PYTHONUNBUFFERED=1
RUN useradd -u 10001 -m worker && mkdir /data && chown worker:worker /data
USER worker
EXPOSE 8080
CMD ["python3", "backend/server.py"]
