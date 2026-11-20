.PHONY: test up down logs once show

test:
	.venv/bin/python -m pytest -q

# The Expected Points scheduler in Docker (README, "Expected Points push").
up:
	docker compose up -d --build

down:
	docker compose down

logs:
	docker compose logs -f --tail 100

once:
	docker compose run --rm ffp ffp schedule --once --output-dir /data/out

show:
	docker compose run --rm ffp ffp schedule --show 30
