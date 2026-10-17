FROM python:3.12-slim

WORKDIR /opt/remymy-api

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONPATH=/opt/remymy-api/src \
    NLTK_DATA=/usr/share/nltk_data \
    HOST=0.0.0.0 \
    PORT=8002 \
    DATABASE_PATH=/data/remymy-food.db \
    USER_DATABASE_PATH=/state/remymy-users.db \
    INDEX_DIR=/var/cache/remymy/retrieval-index

COPY requirements.txt .
# build-essential only compiles hnswlib; the NLTK data is what the reranker's tokenizer reads
RUN apt-get update \
 && apt-get install -y --no-install-recommends build-essential \
 && pip install --no-cache-dir -r requirements.txt \
 && apt-get purge -y build-essential \
 && apt-get autoremove -y \
 && rm -rf /var/lib/apt/lists/* \
 && python -m nltk.downloader -d /usr/share/nltk_data wordnet omw-1.4 stopwords

COPY src ./src
COPY migrations ./migrations
COPY weights/gru_xattn_reranker/model.pt weights/gru_xattn_reranker/config.json ./weights/gru_xattn_reranker/
COPY README.md .

EXPOSE 8002

# Startup loads the model and builds the HNSW index over the catalog before the API answers.
HEALTHCHECK --interval=10s --timeout=3s --start-period=120s --retries=5 \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:' + __import__('os').environ.get('PORT', '8002') + '/ready', timeout=2)" || exit 1

CMD ["sh", "-c", "exec uvicorn remymy_api.main:app --host ${HOST} --port ${PORT}"]
