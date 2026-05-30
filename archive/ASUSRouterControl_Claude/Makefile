.PHONY: setup install dev rebuild app asusroutercontrol run-menubar test lint clean unhide-site-packages

VENV_PYTHON := .venv/bin/python
SITE_PACKAGES_PY := import site; paths=[p for p in site.getsitepackages() if p.endswith("site-packages")]; print(paths[0] if paths else "")

unhide-site-packages:
	@if [ ! -x "$(VENV_PYTHON)" ]; then \
		echo "No $(VENV_PYTHON); skipping UF_HIDDEN fix"; \
		exit 0; \
	fi
	@SITE_PACKAGES=$$($(VENV_PYTHON) -c '$(SITE_PACKAGES_PY)'); \
	if [ -n "$$SITE_PACKAGES" ] && [ -d "$$SITE_PACKAGES" ]; then \
		chflags -R nohidden "$$SITE_PACKAGES"; \
	fi

setup:
	uv venv --python 3.11
	uv pip install -e ".[dev,menubar]"
	# uv sets UF_HIDDEN on the venv tree; Homebrew Python honours that flag
	# and skips all .pth files in hidden directories, breaking editable installs.
	$(MAKE) unhide-site-packages

install:
	uv pip install -e ".[menubar]"
	# uv sets UF_HIDDEN on the venv tree; Homebrew Python honours that flag
	# and skips all .pth files in hidden directories, breaking editable installs.
	$(MAKE) unhide-site-packages

dev:
	uv pip install -e ".[dev,menubar]"
	# uv sets UF_HIDDEN on the venv tree; Homebrew Python honours that flag
	# and skips all .pth files in hidden directories, breaking editable installs.
	$(MAKE) unhide-site-packages

rebuild:
	.venv/bin/pip install -e ".[dev,menubar]"
	$(MAKE) unhide-site-packages
	PYTHONPATH=src .venv/bin/python -m ruff check src/
	PYTHONPATH=src .venv/bin/python -m pytest

app: run-menubar

asusroutercontrol: app

run-menubar:
	# PYTHONPATH=src is required: iCloud Drive re-applies the UF_HIDDEN flag on
	# the editable-install .pth file after each sync, making the package invisible
	# to Python. Explicit PYTHONPATH bypasses .pth entirely and is always reliable.
	PYTHONPATH=src .venv/bin/python -m asusroutercontrol.menubar

test:
	.venv/bin/python -m pytest

lint:
	.venv/bin/python -m ruff check src/

clean:
	rm -rf .venv
