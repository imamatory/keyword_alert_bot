.PHONY: lint lint-fix

lint:
	pipenv run ruff check .

lint-fix:
	pipenv run ruff check . --fix
