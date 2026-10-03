PYTHON ?= .venv/bin/python
RUFF ?= .venv/bin/ruff

.PHONY: quality-setup quality quality-python quality-js quality-test

# This virtual environment contains repository quality tools, not app runtime deps.
.venv/bin/python:
	uv venv --python 3.12 .venv

quality-setup: .venv/bin/python
	uv pip install --python $(PYTHON) -r requirements-quality.txt
	npm ci

quality: quality-python quality-js

quality-python:
	$(PYTHON) -m scripts.quality --root .
	$(RUFF) check --config ruff.toml --no-cache .
	$(RUFF) format --config ruff.toml --check --no-cache .

quality-js:
	npm run quality:js

quality-test:
	$(PYTHON) -m unittest discover -s tests/quality -v
	npm run quality:test:js
