#!/usr/bin/env bash

# ContractOS Deployment Script
# Usage: ./scripts/deploy.sh [staging|production]
#
# Deploys the hardened production stack (docker-compose.prod.yml), running
# database migrations first and waiting for every service's healthcheck before
# declaring success — a zero exit always means the release is up and healthy.
#
# Requires a populated env file (default: docker-compose.prod.env, override
# with CONTRACTOS_ENV_FILE). Copy docker-compose.prod.env.example and fill it:
#   cp docker-compose.prod.env.example docker-compose.prod.env
#
# Env knobs:
#   CONTRACTOS_ENV_FILE  path to the secrets env file (default shown above)
#   SKIP_TESTS=1         skip the pre-deploy backend test run
#
# NOTE: a TLS-terminating reverse proxy must front the frontend service and
# set X-Forwarded-Proto=https (see docker-compose.prod.yml).

set -euo pipefail

ENVIRONMENT=${1:-staging}
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(dirname "$SCRIPT_DIR")"

COMPOSE_FILE="$ROOT_DIR/docker-compose.prod.yml"
ENV_FILE=${CONTRACTOS_ENV_FILE:-"$ROOT_DIR/docker-compose.prod.env"}

echo "🚀 Deploying ContractOS to $ENVIRONMENT..."

# Check if environment is valid
if [[ ! "$ENVIRONMENT" =~ ^(staging|production)$ ]]; then
    echo "❌ Invalid environment: $ENVIRONMENT"
    echo "Usage: ./scripts/deploy.sh [staging|production]"
    exit 1
fi

if ! command -v docker >/dev/null 2>&1; then
    echo "❌ docker is required to deploy"
    exit 1
fi
if ! docker compose version >/dev/null 2>&1; then
    echo "❌ docker compose (v2) is required to deploy"
    exit 1
fi

if [[ ! -f "$ENV_FILE" ]]; then
    echo "❌ Missing env file: $ENV_FILE"
    echo "   cp docker-compose.prod.env.example docker-compose.prod.env and fill it in,"
    echo "   or point CONTRACTOS_ENV_FILE at your secrets file."
    exit 1
fi

# The stack name lives in the compose file; export the target environment so
# interpolation and strict/relaxed startup validation match the intent.
export ENVIRONMENT
export CONTRACTOS_ENV_FILE="$ENV_FILE"

compose() {
    docker compose --env-file "$ENV_FILE" -f "$COMPOSE_FILE" "$@"
}

# Fail before touching Docker if a required secret is missing or malformed.
echo "🔎 Validating compose configuration and required secrets..."
compose config --quiet

# Run tests unless explicitly skipped. Prefer the project virtualenv — a bare
# `python` here picks up whatever interpreter is on PATH, which is how a deploy
# ends up testing a different environment than the one that ships.
if [[ "${SKIP_TESTS:-0}" != "1" ]]; then
    echo "🧪 Running tests..."
    cd "$ROOT_DIR/backend"
    if [[ -x "$ROOT_DIR/backend/venv/bin/python" ]]; then
        PYTHON="$ROOT_DIR/backend/venv/bin/python"
    else
        PYTHON="python"
    fi
    "$PYTHON" -m pytest tests/ --tb=short
else
    echo "⏭️  SKIP_TESTS=1 set — skipping backend tests"
fi

# Build images from the immutable Dockerfiles (no source bind mounts).
echo "🐳 Building images..."
compose build

# Apply schema migrations as a one-off before new code serves traffic. The app
# does not migrate on startup, so this is the only place migrations run.
echo "🗄️  Applying database migrations..."
compose run --rm backend alembic upgrade head

# Roll out and wait for healthchecks. --wait makes the command fail (non-zero)
# if any service is unhealthy or exits, so a green deploy is a real one.
echo "⬆️  Starting services (waiting for healthchecks)..."
compose up -d --remove-orphans --wait

echo "✅ $ENVIRONMENT is up and healthy."
compose ps
