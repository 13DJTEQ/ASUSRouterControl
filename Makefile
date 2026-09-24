.PHONY: setup install dev rebuild app asusroutercontrol run-menubar test lint clean unhide-site-packages build-dev-app verify-dev-app launch-dev-app mac-pull mac-test ci-local

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
build-dev-app:
	bash scripts/build_macos_app.sh --mode dev
verify-dev-app:
	bash scripts/verify_dev_app.sh
launch-dev-app: verify-dev-app

# Mac Connect helpers (Darwin). BRANCH=... SKIP_UNIT=1 LOGS=1 DRY_RUN=1 supported.
mac-pull:
	BRANCH="$(BRANCH)" bash scripts/pull_test_branch.sh --no-next $(BRANCH)

mac-test:
	BRANCH="$(BRANCH)" bash scripts/mac_test_connect.sh \
		$(if $(filter 1,$(SKIP_UNIT)),--skip-unit) \
		$(if $(filter 1,$(SKIP_PULL)),--skip-pull) \
		$(if $(filter 1,$(LOGS)),--logs) \
		$(if $(filter 1,$(DRY_RUN)),--dry-run)

# Linux-safe local CI mirror (ruff + pytest + compileall). No Mac app steps.
ci-local:
	bash scripts/validate.sh

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
