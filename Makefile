.PHONY: help setup test test-backend test-frontend test-e2e lint build deploy clean

# Default target
help:
	@echo "ContractOS Development Commands:"
	@echo ""
	@echo "Setup:"
	@echo "  make setup          - Setup development environment"
	@echo "  make docker-up      - Start all services with Docker"
	@echo "  make docker-down    - Stop all services"
	@echo ""
	@echo "Development:"
	@echo "  make dev-backend    - Start backend server"
	@echo "  make dev-frontend   - Start frontend server"
	@echo ""
	@echo "Testing:"
	@echo "  make test           - Run all tests"
	@echo "  make test-backend   - Run backend tests"
	@echo "  make test-frontend  - Run frontend tests"
	@echo "  make test-e2e       - Run E2E tests"
	@echo "  make lint           - Run linting"
	@echo ""
	@echo "Build:"
	@echo "  make build          - Build all services"
	@echo "  make build-backend  - Build backend Docker image"
	@echo "  make build-frontend - Build frontend Docker image"
	@echo ""
	@echo "Deploy:"
	@echo "  make deploy-staging - Deploy to staging"
	@echo "  make deploy-prod    - Deploy to production"
	@echo ""
	@echo "Utilities:"
	@echo "  make migrate        - Run database migrations"
	@echo "  make seed           - Seed database"
	@echo "  make clean          - Clean build artifacts"

# Setup
setup:
	./scripts/setup.sh

# Docker
docker-up:
	docker-compose up -d

docker-down:
	docker-compose down

# Development
dev-backend:
	cd backend && ./venv/bin/python -m uvicorn app.main:app --reload --port 8000

dev-frontend:
	cd frontend && npm run dev

# Testing
test:
	./scripts/test.sh all

test-backend:
	./scripts/test.sh backend

test-frontend:
	./scripts/test.sh frontend

test-e2e:
	./scripts/test.sh e2e

lint:
	cd frontend && npm run lint
	cd backend && python -m flake8 app/ --max-line-length=120

# Build
build: build-backend build-frontend

build-backend:
	docker build -t contractos-backend:latest -f backend/Dockerfile backend

build-frontend:
	docker build -t contractos-frontend:latest -f frontend/Dockerfile frontend

# Deploy
deploy-staging:
	./scripts/deploy.sh staging

deploy-prod:
	./scripts/deploy.sh production

# Utilities
migrate:
	cd backend && ./venv/bin/python -m alembic upgrade head

seed:
	cd backend && ./venv/bin/python seed_data.py

# Create or promote a platform admin account
admin:
	cd backend && ./venv/bin/python scripts/create_admin.py $(EMAIL) "$(NAME)"

clean:
	rm -rf frontend/.next
	rm -rf frontend/node_modules/.cache
	rm -rf backend/__pycache__
	rm -rf backend/app/__pycache__
	rm -rf .pytest_cache
	find . -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null || true
