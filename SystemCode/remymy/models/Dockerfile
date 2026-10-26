FROM python:3.12-alpine

WORKDIR /opt/remymy-models

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PORT=8001 \
    HOST=0.0.0.0 \
    MODEL_ARTIFACT_PATH=/models

COPY . .

RUN if [ -f requirements.txt ]; then \
      pip install --no-cache-dir -r requirements.txt; \
    elif [ -f pyproject.toml ]; then \
      pip install --no-cache-dir .; \
    fi

EXPOSE 8001

HEALTHCHECK --interval=10s --timeout=3s --start-period=20s --retries=5 \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:' + __import__('os').environ.get('PORT', '8001') + '/', timeout=2)" || exit 1

# The model service can later be replaced with the actual inference command.
# This fallback exposes a simple HTTP endpoint so the complete local topology
# can be started before model-serving code and artifacts are added.
CMD ["sh", "-c", "if [ -f serve.py ]; then exec python serve.py; elif [ -f main.py ]; then exec python main.py; elif [ -f app.py ]; then exec python app.py; else exec python -m http.server \"$PORT\" --bind \"$HOST\"; fi"]
