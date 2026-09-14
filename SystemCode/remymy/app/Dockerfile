FROM node:22-alpine

WORKDIR /usr/src/app

ENV NODE_ENV=development \
    NEXT_TELEMETRY_DISABLED=1 \
    PORT=3000

# The application source and its package manifest will live in this module
# directory. Keeping the conditional install makes the compose stack useful
# while the application is still being scaffolded.
COPY . .

RUN if [ -f package-lock.json ]; then \
      npm ci; \
    elif [ -f package.json ]; then \
      npm install; \
    fi

EXPOSE 3000

HEALTHCHECK --interval=10s --timeout=3s --start-period=20s --retries=5 \
  CMD wget --spider --quiet "http://127.0.0.1:${PORT}/" || exit 1

# Prefer the real Next.js scripts when the app is added. The fallback keeps
# `docker compose up` useful for validating service wiring before then.
CMD ["sh", "-c", "if [ -f package.json ]; then if npm run | grep -qE '^  start'; then exec npm run start -- -H 0.0.0.0 -p \"$PORT\"; else exec npm run dev -- --hostname 0.0.0.0 --port \"$PORT\"; fi; else exec node -e 'const http=require(\"http\"); http.createServer((req,res)=>{res.writeHead(200,{\"content-type\":\"application/json\"}); res.end(JSON.stringify({service:\"app\",status:\"scaffold\"}));}).listen(process.env.PORT,\"0.0.0.0\");' ; fi"]
