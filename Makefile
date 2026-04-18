.PHONY: setup install dev rebuild app asusroutercontrol run-menubar test lint clean unhide-site-packages bundle dmg venv-link

# Canonical venv location lives OUTSIDE iCloud Drive. iCloud sets UF_HIDDEN on
# files inside ~/Library/Mobile Documents, which causes Homebrew Python to
# silently skip .pth files and break editable installs. Keeping the venv in
# ~/.virtualenvs avoids that class of bug entirely.
VENV_DIR := $(HOME)/.virtualenvs/asusroutercontrol
VENV_LINK := .venv
VENV_PYTHON := $(VENV_DIR)/bin/python
VENV_PIP_ENV := VIRTUAL_ENV="$(VENV_DIR)"
SITE_PACKAGES_PY := import site; paths=[p for p in site.getsitepackages() if p.endswith("site-packages")]; print(paths[0] if paths else "")

# Ensure the in-repo .venv symlink points at the canonical external venv.
# Kept for compatibility with scripts/tools that reference .venv/bin/...
# If a stale .venv directory exists (e.g. copied from iCloud, or left behind
# by an aborted `make setup`), auto-remove it — but only if it actually looks
# like a venv (has pyvenv.cfg). Anything else is refused to protect real data.
venv-link:
	@if [ -L "$(VENV_LINK)" ]; then \
		target=$$(readlink "$(VENV_LINK)"); \
		if [ "$$target" != "$(VENV_DIR)" ]; then \
			rm -f "$(VENV_LINK)"; \
			ln -s "$(VENV_DIR)" "$(VENV_LINK)"; \
		fi; \
	elif [ -d "$(VENV_LINK)" ] && [ -f "$(VENV_LINK)/pyvenv.cfg" ]; then \
		echo "Removing stale $(VENV_LINK) directory (pyvenv.cfg present) and replacing with symlink"; \
		rm -rf "$(VENV_LINK)"; \
		ln -s "$(VENV_DIR)" "$(VENV_LINK)"; \
	elif [ -e "$(VENV_LINK)" ]; then \
		echo "Refusing to overwrite existing non-venv $(VENV_LINK) (no pyvenv.cfg)."; \
		echo "Move or delete it manually, then re-run."; \
		exit 1; \
	else \
		ln -s "$(VENV_DIR)" "$(VENV_LINK)"; \
	fi

unhide-site-packages:
	@if [ ! -x "$(VENV_PYTHON)" ]; then \
		echo "No $(VENV_PYTHON); skipping UF_HIDDEN fix"; \
		exit 0; \
	fi
	@SITE_PACKAGES=$$($(VENV_PYTHON) -c '$(SITE_PACKAGES_PY)'); \
	if [ -n "$$SITE_PACKAGES" ] && [ -d "$$SITE_PACKAGES" ]; then \
		chflags -R nohidden "$$SITE_PACKAGES" 2>/dev/null || true; \
	fi

# `setup` is idempotent: `--clear` makes `uv venv` recreate the external venv
# cleanly whether or not it already exists, and `venv-link` now handles any
# stale in-repo .venv directory. Safe to re-run after partial or aborted runs.
setup:
	mkdir -p "$(dir $(VENV_DIR))"
	uv venv --clear --python 3.11 "$(VENV_DIR)"
	$(MAKE) venv-link
	$(VENV_PIP_ENV) uv pip install -e ".[dev,menubar]"
	# Safety net — venv is outside iCloud so UF_HIDDEN shouldn't apply, but
	# clear it anyway in case a previous in-iCloud venv left artefacts.
	$(MAKE) unhide-site-packages

install: venv-link
	$(VENV_PIP_ENV) uv pip install -e ".[menubar]"
	$(MAKE) unhide-site-packages

dev: venv-link
	$(VENV_PIP_ENV) uv pip install -e ".[dev,menubar]"
	$(MAKE) unhide-site-packages

rebuild: venv-link
	$(VENV_PIP_ENV) uv pip install -e ".[dev,menubar]"
	$(MAKE) unhide-site-packages
	$(VENV_PYTHON) -m ruff check src/
	$(VENV_PYTHON) -m pytest

app: run-menubar

asusroutercontrol: app

run-menubar: install
	$(VENV_PYTHON) -m asusroutercontrol.menubar

test:
	$(VENV_PYTHON) -m pytest

lint:
	$(VENV_PYTHON) -m ruff check src/

bundle:
	rm -rf build dist
	$(VENV_DIR)/bin/pyinstaller AsusRouterMonitor.spec --noconfirm
	@echo "\n✅  dist/AsusRouterMonitor.app is ready"

dmg: bundle
	rm -rf /tmp/dmg-stage dist/AsusRouterMonitor.dmg
	mkdir -p /tmp/dmg-stage
	cp -R dist/AsusRouterMonitor.app /tmp/dmg-stage/
	ln -s /Applications /tmp/dmg-stage/Applications
	hdiutil create -volname "AsusRouterMonitor" \
		-srcfolder /tmp/dmg-stage -ov -format UDZO \
		dist/AsusRouterMonitor.dmg
	rm -rf /tmp/dmg-stage
	@echo "\n✅  dist/AsusRouterMonitor.dmg is ready"

# Remove the external venv, the in-repo symlink, and build artefacts.
# `rm -rf` on the symlink only removes the link itself, so wipe the canonical
# directory first.
clean:
	rm -rf "$(VENV_DIR)"
	rm -f "$(VENV_LINK)"
	rm -rf build dist
