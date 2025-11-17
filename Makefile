.PHONY: lint lint-fix

deploy:
	kamal deploy

lint:
	pipenv run ruff check .

lint-fix:
	pipenv run ruff check . --fix
