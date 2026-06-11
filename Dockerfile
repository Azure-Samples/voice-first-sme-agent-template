# Stage 1: Build frontend
# Vite 7 requires Node >=20.19 or >=22.12; the floating ":20" tag on this
# registry resolves to 20.14, so use the :24 stream (the only Vite-compatible
# tag currently published on mcr.microsoft.com/azurelinux/base/nodejs).
FROM mcr.microsoft.com/azurelinux/base/nodejs:24 AS frontend
WORKDIR /app
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci
COPY frontend/ .
# Copy agent-specific env overrides (Vite auto-loads .env.local over .env)
ARG AGENT_VARIANT=
COPY agents/ /tmp/agents/
RUN if [ -n "$AGENT_VARIANT" ] && [ -f "/tmp/agents/$AGENT_VARIANT/.env" ]; then \
      cp "/tmp/agents/$AGENT_VARIANT/.env" .env.local; \
    fi
# MSAL config:
# - Setup-wizard flow (deploy.js) writes frontend/.env.production into the
#   build context BEFORE invoking docker build, so these ARGs are unset and
#   the conditional below is a no-op. Vite's loadEnv() reads .env.production.
# - ADO pipeline flow (__PROJECT_NAME__/pipeline.yml) passes these as --build-arg
#   values. Vite's loadEnv() reads .env* files but NOT process.env, so we
#   need to materialize a .env.production from the ARGs for Vite to pick
#   them up at build time.
#
# IMPORTANT: do NOT add `=` defaults to these ARGs (e.g. `ARG VITE_MSAL_CLIENT_ID=`).
# Docker exports ARG defaults into process.env for subsequent RUN commands,
# and Vite's loadEnv gives process.env priority over .env files. An empty
# default would override the .env.production values written by deploy.js,
# leaving the bundle with empty strings and breaking MSAL sign-in.
ARG VITE_MSAL_CLIENT_ID
ARG VITE_MSAL_TENANT_ID
ARG VITE_MSAL_REDIRECT_URI
ARG VITE_MSAL_TRIBE_CLIENT_ID
ARG VITE_MSAL_TRIBE_TENANT_ID
RUN if [ -n "$VITE_MSAL_CLIENT_ID" ] && [ ! -f .env.production ]; then \
      printf "VITE_MSAL_CLIENT_ID=%s\nVITE_MSAL_TENANT_ID=%s\nVITE_MSAL_REDIRECT_URI=%s\nVITE_MSAL_TRIBE_CLIENT_ID=%s\nVITE_MSAL_TRIBE_TENANT_ID=%s\n" \
        "$VITE_MSAL_CLIENT_ID" "$VITE_MSAL_TENANT_ID" "${VITE_MSAL_REDIRECT_URI:-/}" "$VITE_MSAL_TRIBE_CLIENT_ID" "$VITE_MSAL_TRIBE_TENANT_ID" \
        > .env.production; \
    fi
RUN npm run build

# Stage 2: Backend + static files
FROM mcr.microsoft.com/azurelinux/base/python:3
# Template version + git SHA + local-changes flag — surfaced by
# GET /api/health and emitted in startup logs. Passed via --build-arg from
# deploy.js (which reads VERSION at repo root, `git rev-parse --short HEAD`,
# and `git status --porcelain` for the dirty check). Defaults preserve
# "unknown" for local docker builds that bypass deploy.js.
ARG TEMPLATE_VERSION=unknown
ARG GIT_SHA=unknown
ARG LOCAL_CHANGES=unknown
ENV TEMPLATE_VERSION=${TEMPLATE_VERSION}
ENV GIT_SHA=${GIT_SHA}
ENV LOCAL_CHANGES=${LOCAL_CHANGES}
WORKDIR /app
COPY backend/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY backend/ .
COPY --from=frontend /app/dist ./static
COPY agents/ ./agents/
EXPOSE 8000
# --no-access-log: WebSocket upgrade URLs include the bearer token as
# a query parameter (browser WebSocket API can't set Authorization
# headers). Uvicorn's default access log emits the full request line
# (path + query) at INFO, which would persist tokens into
# ContainerAppConsoleLogs_CL. Disable access logging in the container;
# application-level logging (logger.info / .warning in app.*) is
# unaffected and remains our source of operational visibility.
# Local dev scripts (scripts/dev.{ps1,sh}) keep access logging on.
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--no-access-log"]
