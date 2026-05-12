# ─────────────────────────────────────────────────────────────────────────────
# Stage 1 — deps: install node_modules (layer-cached until package-lock changes)
# ─────────────────────────────────────────────────────────────────────────────
FROM node:20-alpine AS deps

WORKDIR /app

COPY frontend/package.json frontend/package-lock.json ./

RUN npm ci


# ─────────────────────────────────────────────────────────────────────────────
# Stage 2 — dev: node + deps; source bind-mounted at runtime
# ─────────────────────────────────────────────────────────────────────────────
FROM node:20-alpine AS dev

WORKDIR /app

COPY --from=deps /app/node_modules ./node_modules

ENV NODE_ENV=development \
    NEXT_TELEMETRY_DISABLED=1

EXPOSE 3000

# Source is bind-mounted via docker-compose.dev.yml; Next.js HMR runs live
CMD ["npx", "next", "dev", "-p", "3000"]


# ─────────────────────────────────────────────────────────────────────────────
# Stage 3 — builder: compile the optimised Next.js production build
# ─────────────────────────────────────────────────────────────────────────────
FROM node:20-alpine AS builder

WORKDIR /app

COPY --from=deps /app/node_modules ./node_modules
COPY frontend/ .

ARG NEXT_PUBLIC_API_URL=http://localhost:8000
ARG NEXT_PUBLIC_BOT_USERNAME=gramlycrmtestbot
ARG NEXT_PUBLIC_GRAMLY_URL=""
ENV NEXT_PUBLIC_API_URL=$NEXT_PUBLIC_API_URL \
    NEXT_PUBLIC_BOT_USERNAME=$NEXT_PUBLIC_BOT_USERNAME \
    NEXT_PUBLIC_GRAMLY_URL=$NEXT_PUBLIC_GRAMLY_URL \
    NEXT_TELEMETRY_DISABLED=1 \
    NODE_ENV=production

RUN npm run build && mkdir -p public


# ─────────────────────────────────────────────────────────────────────────────
# Stage 4 — runner: minimal image using Next.js standalone output
#   Contains only server.js + static assets — no source, no node_modules
# ─────────────────────────────────────────────────────────────────────────────
FROM node:20-alpine AS runner

WORKDIR /app

RUN addgroup --system --gid 1001 nodejs && \
    adduser  --system --uid 1001 nextjs

ENV NODE_ENV=production \
    NEXT_TELEMETRY_DISABLED=1

COPY --from=builder --chown=nextjs:nodejs /app/.next/standalone ./
COPY --from=builder --chown=nextjs:nodejs /app/.next/static    ./.next/static
COPY --from=builder --chown=nextjs:nodejs /app/public          ./public

USER nextjs

EXPOSE 3000

HEALTHCHECK --interval=15s --timeout=5s --start-period=30s --retries=3 \
    CMD node -e "require('http').get('http://127.0.0.1:3000/', r => process.exit(r.statusCode >= 500 ? 1 : 0)).on('error', () => process.exit(1))"

CMD ["node", "server.js"]
