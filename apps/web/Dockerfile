FROM node:22-alpine

WORKDIR /app

COPY package.json package-lock.json ./
COPY apps/web/package.json ./apps/web/package.json
RUN npm install

COPY apps/web /app/apps/web

ARG API_BASE_URL=http://api:8000
ARG PIPECAT_SERVICE_URL=http://pipecat:8110
ARG NEXT_PUBLIC_API_BASE_URL=http://localhost:8025
ARG NEXT_PUBLIC_PIPECAT_SERVICE_URL=http://localhost:8110
ARG APP_ENV=development
ARG PRODUCTION=false

ENV API_BASE_URL=$API_BASE_URL
ENV PIPECAT_SERVICE_URL=$PIPECAT_SERVICE_URL
ENV NEXT_PUBLIC_API_BASE_URL=$NEXT_PUBLIC_API_BASE_URL
ENV NEXT_PUBLIC_PIPECAT_SERVICE_URL=$NEXT_PUBLIC_PIPECAT_SERVICE_URL
ENV APP_ENV=$APP_ENV
ENV PRODUCTION=$PRODUCTION

RUN cd /app/apps/web && npm run build

ENV NODE_ENV=production
EXPOSE 3000

CMD ["sh", "-c", "cd /app/apps/web && npm run start -- --hostname 0.0.0.0 --port 3000"]
