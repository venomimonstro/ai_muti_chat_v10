.PHONY: up down test lint migrate

up:
	docker compose up --build

down:
	docker compose down

test:
	docker compose run --rm backend pytest

lint:
	docker compose run --rm backend ruff check .
	docker compose run --rm frontend npm run lint

migrate:
	docker compose run --rm backend python manage.py migrate

