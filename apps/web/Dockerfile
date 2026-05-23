FROM node:22-alpine

WORKDIR /app

COPY package.json package-lock.json ./
COPY apps/web/package.json ./apps/web/package.json
RUN npm install

COPY apps/web /app/apps/web

ENV NODE_ENV=production
EXPOSE 3000

CMD ["sh", "-c", "cd /app/apps/web && rm -rf .next && npm run build && npm run start -- --hostname 0.0.0.0 --port 3000"]
