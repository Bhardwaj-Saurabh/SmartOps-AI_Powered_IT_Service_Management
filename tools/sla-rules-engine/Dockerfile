FROM python:3.12-slim
RUN pip install --no-cache-dir uv==0.5.4
WORKDIR /app
COPY tools/sla-rules-engine /app/
RUN uv pip install --system --no-cache .
RUN useradd -m -u 1000 appuser && chown -R appuser:appuser /app
USER appuser
ENV PYTHONUNBUFFERED=1
EXPOSE 9002
HEALTHCHECK --interval=10s --timeout=3s --start-period=10s --retries=5 \
  CMD python -c "import urllib.request,sys;sys.exit(0 if urllib.request.urlopen('http://localhost:9002/health',timeout=2).status==200 else 1)"
CMD ["python", "-m", "uvicorn", "sla_rules_engine.main:app", "--host", "0.0.0.0", "--port", "9002"]
