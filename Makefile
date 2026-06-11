.PHONY: setup install dev rebuild app asusroutercontrol run-menubar test lint clean unhide-site-packages build-test-app verify-test-app

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
	uv pip install -e ".[dev,menubar]"
	# uv sets UF_HIDDEN on the venv tree; Homebrew Python honours that flag
	# and skips all .pth files in hidden directories, breaking editable installs.
	$(MAKE) unhide-site-packages
	.venv/bin/python -m ruff check src/
	.venv/bin/python -m pytest
build-test-app:
	bash scripts/build_macos_app.sh --mode test

verify-test-app:
	bash scripts/verify_test_app.sh

app: run-menubar

asusroutercontrol: app

run-menubar: install
	.venv/bin/python -m asusroutercontrol.menubar

test:
	.venv/bin/python -m pytest

lint:
	.venv/bin/python -m ruff check src/

clean:
	rm -rf .venv
