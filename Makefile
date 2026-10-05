.PHONY: up down seed test lint api-test worker-test
up:            ## start the full local stack (no GPU needed)
	cp -n .env.example .env || true
	docker compose up -d --build
seed:
	docker compose run --rm api python -m app.seed
down:
	docker compose down
lint:
	cd services/api && ruff check .
	cd services/worker && ruff check .
test: api-test worker-test
api-test:
	cd services/api && python -m pytest -q
worker-test:
	cd services/worker && python -m pytest -q
