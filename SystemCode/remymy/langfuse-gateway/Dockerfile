FROM node:22-alpine

WORKDIR /opt/langfuse-gateway
COPY server.mjs .

USER node
EXPOSE 3030
CMD ["node", "server.mjs"]
