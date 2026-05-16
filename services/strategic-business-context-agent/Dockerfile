FROM python:3.12-slim

# We install uv to leverage the workspace layout and resolve the local libs/.
RUN pip install --no-cache-dir uv==0.5.4

WORKDIR /app

# Copy workspace metadata and source. Build context is repo root.
COPY pyproject.toml /app/
COPY libs /app/libs
COPY services/strategic-business-context-agent /app/services/strategic-business-context-agent

# uv sync builds & installs all workspace members. SBCA is what we run.
RUN uv sync --frozen --no-dev || uv sync --no-dev

# Drop privileges.
RUN useradd -m -u 1000 appuser && chown -R appuser:appuser /app
USER appuser

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PATH="/app/.venv/bin:${PATH}"

EXPOSE 8444

HEALTHCHECK --interval=10s --timeout=3s --start-period=15s --retries=5 \
  CMD python -c "import urllib.request,sys;sys.exit(0 if urllib.request.urlopen('http://localhost:8444/health',timeout=2).status==200 else 1)"

CMD ["python", "-m", "uvicorn", "sbca.main:app", "--host", "0.0.0.0", "--port", "8444"]
