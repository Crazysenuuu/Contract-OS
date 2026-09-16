#!/bin/bash

# ContractOS Deployment Script
# Usage: ./scripts/deploy.sh [environment]

set -e

ENVIRONMENT=${1:-staging}
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(dirname "$SCRIPT_DIR")"

echo "🚀 Deploying ContractOS to $ENVIRONMENT..."

# Check if environment is valid
if [[ ! "$ENVIRONMENT" =~ ^(staging|production)$ ]]; then
    echo "❌ Invalid environment: $ENVIRONMENT"
    echo "Usage: ./scripts/deploy.sh [staging|production]"
    exit 1
fi

# Build frontend
echo "📦 Building frontend..."
cd "$ROOT_DIR/frontend"
npm ci
npm run build

# Run tests
echo "🧪 Running tests..."
cd "$ROOT_DIR/backend"
python -m pytest tests/ -v --tb=short

# Build Docker images
echo "🐳 Building Docker images..."
docker build -t contractos-backend:$ENVIRONMENT -f Dockerfile.backend .
docker build -t contractos-frontend:$ENVIRONMENT -f Dockerfile.frontend .

# Deploy based on environment
if [ "$ENVIRONMENT" = "staging" ]; then
    echo "📦 Deploying to staging..."
    # Add your staging deployment commands here
    # Example: kubectl apply -f k8s/staging/
    echo "✅ Staging deployment complete!"
elif [ "$ENVIRONMENT" = "production" ]; then
    echo "🔒 Deploying to production..."
    # Add your production deployment commands here
    # Example: kubectl apply -f k8s/production/
    echo "✅ Production deployment complete!"
fi

echo "🎉 Deployment finished!"
