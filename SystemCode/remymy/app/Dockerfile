FROM node:22-alpine AS build
WORKDIR /usr/src/app
ENV NEXT_TELEMETRY_DISABLED=1
COPY package.json package-lock.json ./
RUN npm ci
COPY . .
RUN npm run build

FROM node:22-alpine AS runner
WORKDIR /usr/src/app
ENV NODE_ENV=production NEXT_TELEMETRY_DISABLED=1 PORT=3000 HOSTNAME=0.0.0.0
COPY --from=build --chown=node:node /usr/src/app/.next/standalone ./
COPY --from=build --chown=node:node /usr/src/app/.next/static ./.next/static
COPY --from=build --chown=node:node /usr/src/app/public ./public
# Writable state for saved AI connections; a new named volume copies this owner.
RUN mkdir -p /state && chown node:node /state
USER node
EXPOSE 3000
HEALTHCHECK --interval=10s --timeout=3s --start-period=20s --retries=5 CMD wget --spider --quiet "http://127.0.0.1:${PORT}/" || exit 1
CMD ["node", "server.js"]
