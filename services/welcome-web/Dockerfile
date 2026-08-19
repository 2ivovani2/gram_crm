FROM node:22-alpine AS build

WORKDIR /src
COPY package.json package-lock.json vite.welcome.config.js ./
RUN npm ci
COPY frontend/welcome ./frontend/welcome
RUN npm run build:welcome

FROM nginxinc/nginx-unprivileged:1.27-alpine

COPY services/welcome-web/nginx.conf /etc/nginx/conf.d/default.conf
COPY --from=build /src/services/welcome-web/dist /usr/share/nginx/html

EXPOSE 8080

HEALTHCHECK --interval=30s --timeout=3s --start-period=5s --retries=3 \
  CMD wget -q -O /dev/null http://127.0.0.1:8080/health || exit 1
