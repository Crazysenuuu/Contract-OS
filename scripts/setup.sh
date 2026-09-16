#!/bin/bash

# ContractOS Development Setup Script
# Usage: ./scripts/setup.sh

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(dirname "$SCRIPT_DIR")"

echo "🔧 Setting up ContractOS development environment..."

# Check prerequisites
check_command() {
    if ! command -v $1 &> /dev/null; then
        echo "❌ $1 is not installed. Please install it first."
        exit 1
    fi
}

check_command python3
check_command node
check_command npm
check_command docker

# Setup backend
echo "📦 Setting up backend..."
cd "$ROOT_DIR/backend"
python3 -m venv venv
source venv/bin/activate
pip install --upgrade pip
pip install -r requirements.txt
pip install pytest pytest-cov pytest-asyncio

# Setup frontend
echo "🎨 Setting up frontend..."
cd "$ROOT_DIR/frontend"
npm ci

# Setup E2E tests
echo "🔍 Setting up E2E tests..."
cd "$ROOT_DIR/e2e"
npm ci
npx playwright install chromium

# Setup database
echo "🗄️ Setting up database..."
cd "$ROOT_DIR/backend"
if command -v docker &> /dev/null; then
    docker-compose up -d postgres
    sleep 5
    alembic upgrade head
fi

# Seed data
echo "🌱 Seeding data..."
python3 -c "
from app.models.i18n import Language, GlossaryTerm
from app.services.translation_service import TranslationService
# Add seed commands here
print('Seeding complete!')
"

echo ""
echo "✅ Setup complete!"
echo ""
echo "To start development:"
echo "  Backend:  cd backend && source venv/bin/activate && uvicorn app.main:app --reload"
echo "  Frontend: cd frontend && npm run dev"
echo ""
echo "To run tests:"
echo "  ./scripts/test.sh all"
