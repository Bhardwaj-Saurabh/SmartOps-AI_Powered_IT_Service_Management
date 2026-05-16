FROM python:3.12-slim
RUN pip install --no-cache-dir uv==0.5.4
WORKDIR /app
COPY pyproject.toml /app/
COPY libs /app/libs
COPY services/knowledge-search-agent /app/services/knowledge-search-agent
COPY configs /app/configs
RUN uv sync --frozen --no-dev || uv sync --no-dev
RUN useradd -m -u 1000 appuser && chown -R appuser:appuser /app
USER appuser
ENV PYTHONUNBUFFERED=1 \
    PATH="/app/.venv/bin:${PATH}"
EXPOSE 8444 8443
HEALTHCHECK --interval=10s --timeout=3s --start-period=30s --retries=10 \
  CMD python -c "import urllib.request,sys;sys.exit(0 if urllib.request.urlopen('http://localhost:8444/health',timeout=2).status==200 else 1)"
CMD ["python", "-m", "uvicorn", "knowledge_search.main:app", "--host", "0.0.0.0", "--port", "8444"]
