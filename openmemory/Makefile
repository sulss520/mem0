.PHONY: help up down logs shell migrate test test-clean env ui-install ui-start ui-dev ui-build ui-dev-start build build-api build-ui build-clean deploy start stop restart status health clean

NEXT_PUBLIC_USER_ID=$(USER)
NEXT_PUBLIC_API_URL=http://localhost:8765

# Default target
help:
	@echo "Available commands:"
	@echo "  make env         - Copy .env.example to .env"
	@echo "  make build       - Build all services (API + UI) with Tencent mirrors"
	@echo "  make build-api   - Build API service only"
	@echo "  make build-ui    - Build UI service only"
	@echo "  make build-clean - Clean and build all services"
	@echo "  make deploy      - Deploy services (build + start)"
	@echo "  make start       - Start services"
	@echo "  make stop        - Stop services"
	@echo "  make restart     - Restart services"
	@echo "  make status      - Show service status"
	@echo "  make health      - Health check"
	@echo "  make clean       - Clean all containers and volumes"
	@echo "  make up          - Start the containers"
	@echo "  make down        - Stop the containers"
	@echo "  make logs        - Show container logs"
	@echo "  make shell       - Open a shell in the api container"
	@echo "  make migrate     - Run database migrations"
	@echo "  make test        - Run tests in a new container"
	@echo "  make test-clean  - Run tests and clean up volumes"
	@echo "  make ui-install  - Install frontend dependencies"
	@echo "  make ui-start    - Start the frontend development server"
	@echo "  make ui-dev      - Install dependencies and start the frontend in dev mode"
	@echo "  make ui          - Install dependencies and start the frontend in production mode"

env:
	cd api && cp .env.example .env
	cd ui && cp .env.example .env

# Build commands using Tencent mirrors
build:
	@echo "Building all services with Tencent mirrors..."
	@./build.sh --all

build-api:
	@echo "Building API service with Tencent PyPI mirror..."
	@./build.sh --api

build-ui:
	@echo "Building UI service with Tencent npm mirror..."
	@./build.sh --ui

build-clean:
	@echo "Cleaning and building all services..."
	@./build.sh --all --clean

# Management commands using admin.sh
deploy:
	@./admin.sh deploy --build

start:
	@./admin.sh start

stop:
	@./admin.sh stop

restart:
	@./admin.sh restart

status:
	@./admin.sh status

health:
	@./admin.sh health

clean:
	@./admin.sh clean

up:
	NEXT_PUBLIC_USER_ID=$(USER) NEXT_PUBLIC_API_URL=$(NEXT_PUBLIC_API_URL) docker compose up

down:
	docker compose down -v
	rm -f api/openmemory.db

logs:
	docker compose logs -f

shell:
	docker compose exec api bash

upgrade:
	docker compose exec api alembic upgrade head

migrate:
	docker compose exec api alembic upgrade head

downgrade:
	docker compose exec api alembic downgrade -1

ui-dev:
	cd ui && NEXT_PUBLIC_USER_ID=$(USER) NEXT_PUBLIC_API_URL=$(NEXT_PUBLIC_API_URL) pnpm install && pnpm dev
