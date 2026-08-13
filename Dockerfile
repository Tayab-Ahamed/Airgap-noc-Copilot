# Backend image. Build offline once wheels are vendored (see scripts/predownload.sh).
FROM python:3.12-slim

WORKDIR /app

# Install deps. At an air-gapped venue, copy a wheelhouse/ and use --no-index.
COPY requirements.txt .
RUN if [ -d wheelhouse ]; then \
      pip install --no-index --find-links wheelhouse -r requirements.txt; \
    else \
      pip install --no-cache-dir -r requirements.txt; \
    fi

COPY . .

ENV NOC_HOST=0.0.0.0 NOC_PORT=8000 PYTHONPATH=/app
EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --retries=3 \
  CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://localhost:8000/api/health').status==200 else 1)"

CMD ["uvicorn", "backend.main:app", "--host", "0.0.0.0", "--port", "8000"]
