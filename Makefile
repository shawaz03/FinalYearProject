.DEFAULT_GOAL := help

PYTHON := $(if $(wildcard .venv/Scripts/python.exe),.venv/Scripts/python.exe,$(if $(wildcard .venv/bin/python),.venv/bin/python,python))

.PHONY: help setup test cluster-up deploy collect train serve bench analyse clean

help:
	@$(PYTHON) scripts/cpctl.py help

setup:
	@$(PYTHON) scripts/cpctl.py setup

test:
	@$(PYTHON) scripts/cpctl.py test

cluster-up:
	@$(PYTHON) scripts/cpctl.py cluster-up

deploy:
	@$(PYTHON) scripts/cpctl.py deploy

collect:
	@$(PYTHON) scripts/cpctl.py collect

train:
	@$(PYTHON) scripts/cpctl.py train

serve:
	@$(PYTHON) scripts/cpctl.py serve

bench:
	@$(PYTHON) scripts/cpctl.py bench

analyse:
	@$(PYTHON) scripts/cpctl.py analyse

clean:
	@$(PYTHON) scripts/cpctl.py clean
