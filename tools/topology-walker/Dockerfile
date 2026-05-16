FROM python:3.12-slim
RUN pip install --no-cache-dir uv==0.5.4
WORKDIR /app
COPY tools/topology-walker /app/
COPY tools/service-dependency-mapper/data/topology.yaml /app/data/topology.yaml
RUN uv pip install --system --no-cache .
RUN useradd -m -u 1000 appuser && chown -R appuser:appuser /app
USER appuser
ENV PYTHONUNBUFFERED=1 \
    TOPOLOGY_PATH=/app/data/topology.yaml
EXPOSE 9003
HEALTHCHECK --interval=10s --timeout=3s --start-period=10s --retries=5 \
  CMD python -c "import urllib.request,sys;sys.exit(0 if urllib.request.urlopen('http://localhost:9003/health',timeout=2).status==200 else 1)"
CMD ["python", "-m", "uvicorn", "topology_walker.main:app", "--host", "0.0.0.0", "--port", "9003"]
