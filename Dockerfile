FROM python:3.12-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY server.py protocol.py documents.py cloud.py ./
COPY mobile ./mobile
ENV FOLIO_DATA=/data PYTHONUNBUFFERED=1
EXPOSE 8080
CMD ["sh", "-c", "exec gunicorn --bind 0.0.0.0:${PORT:-8080} --workers 1 --threads 8 --timeout 90 'cloud:create_app()'"]
