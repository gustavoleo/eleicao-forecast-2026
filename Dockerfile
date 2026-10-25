FROM python:3.12-slim
ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1 DATA_DIR=/data DATAZOO_DISABLE_TELEMETRY=1
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt && useradd --uid 10001 --create-home app && mkdir /data && chown app:app /data
COPY src ./src
USER app
RUN python -c "import duckdb; c=duckdb.connect(); c.execute('INSTALL anofox_forecast FROM community'); c.execute('LOAD anofox_forecast'); c.execute('SET anofox_telemetry_enabled=false'); print(c.execute(\"SELECT extension_version FROM duckdb_extensions() WHERE extension_name='anofox_forecast'\").fetchone())"
EXPOSE 8501 8080
HEALTHCHECK --interval=30s --timeout=5s --start-period=90s CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8080/health',timeout=4)"
CMD ["python", "-m", "src.service"]
