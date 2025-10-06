# syntax=docker/dockerfile:1
# Next.js App Router frontend. Build stage compiles the production bundle,
# runtime stage serves it via `next start`.
ARG APP_DIR=apps/frontend

FROM node:20-alpine AS deps
ARG APP_DIR
WORKDIR /app

COPY ${APP_DIR}/package.json ${APP_DIR}/package-lock.json* ${APP_DIR}/pnpm-lock.yaml* ${APP_DIR}/yarn.lock* ./
RUN npm install --frozen-lockfile || npm install

FROM node:20-alpine AS build
ARG APP_DIR
WORKDIR /app
COPY --from=deps /app/node_modules ./node_modules
COPY ${APP_DIR}/ ./
RUN npm run build

FROM node:20-alpine AS runner
WORKDIR /app
ENV NODE_ENV=production

COPY --from=build /app/package.json ./
COPY --from=build /app/node_modules ./node_modules
COPY --from=build /app/.next ./.next
COPY --from=build /app/public ./public

EXPOSE 3000
CMD ["npm", "run", "start"]
