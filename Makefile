.PHONY: setup build flash run check

setup:
	bash tools/setup.sh

build:
	.venv/bin/python tools/build.py

flash:
	.venv/bin/python tools/flash.py

run:
	.venv/bin/python scanner.py --record captures/session.jsonl

check:
	.venv/bin/clang-format --dry-run --Werror firmware/main.c firmware/sdk_config.h
	.venv/bin/ruff format --check scanner.py tools tests
	.venv/bin/ruff check scanner.py tools tests
	.venv/bin/python -m unittest discover -s tests -v
	bash -n tools/setup.sh
	node --check web/app.js
