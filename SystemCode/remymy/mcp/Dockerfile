FROM python:3.12-slim

WORKDIR /opt/remymy-mcp

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PORT=8000 \
    HOST=0.0.0.0

COPY . .

RUN if [ -f requirements.txt ]; then \
      pip install --no-cache-dir -r requirements.txt; \
    elif [ -f pyproject.toml ]; then \
      pip install --no-cache-dir .; \
    fi

EXPOSE 8000

HEALTHCHECK --interval=10s --timeout=3s --start-period=20s --retries=5 \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:' + __import__('os').environ.get('PORT', '8000') + '/health', timeout=2)" || exit 1

# A future MCP implementation can use main.py, app.py, or an installed
# package entrypoint. The fallback is a simple HTTP server for local wiring.
CMD ["sh", "-c", "if [ -f main.py ]; then exec python main.py; elif [ -f app.py ]; then exec python app.py; else exec python -m http.server \"$PORT\" --bind \"$HOST\"; fi"]
