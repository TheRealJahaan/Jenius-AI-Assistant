.PHONY: install detect build eval serve test lint fmt check docker

install:      ## install in editable mode with dev tools
	pip install -e ".[dev]"

detect:       ## write out/output_anomalies.csv
	python -m jenius detect

build:        ## regenerate web/data/kb.json for the dashboard
	python -m jenius build

eval:         ## reproducibility + labelled-case evaluation
	python -m jenius eval

serve:        ## API + dashboard on http://localhost:8000
	python -m jenius serve

test:
	pytest
	npm test

lint:
	ruff check .
	ruff format --check .
	mypy

fmt:
	ruff check --fix .
	ruff format .

check: lint test eval

docker:
	docker build -t jenius . && docker run --rm -p 8000:8000 jenius
