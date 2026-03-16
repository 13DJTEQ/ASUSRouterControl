.PHONY: setup install dev rebuild app asusroutercontrol run-menubar test lint clean

setup:
	uv venv --python 3.11
	uv pip install -e ".[dev,menubar]"
	# uv sets UF_HIDDEN on the venv tree; Homebrew Python 3.11 honours that flag
	# and skips all .pth files in hidden directories, breaking editable installs.
	chflags -R nohidden .venv/lib/python3.11/site-packages

install:
	uv pip install -e ".[menubar]"

dev:
	uv pip install -e ".[dev,menubar]"

rebuild:
	uv pip install -e ".[dev,menubar]"
	.venv/bin/python -m ruff check src/
	.venv/bin/python -m pytest

app: install

asusroutercontrol: app

run-menubar:
	.venv/bin/python -m asusroutercontrol.menubar

test:
	.venv/bin/python -m pytest

lint:
	.venv/bin/python -m ruff check src/

clean:
	safe_rm -r .venv
