.PHONY: install dev migrate test lint format api-types app release

install:
	cd backend && uv sync
	cd frontend && bun install

migrate:
	cd backend && uv run alembic upgrade head

# Runs backend (:8100) and frontend (:5273) together; Ctrl-C stops both. Ports 8000 is the
# desktop app's (the one agents and other devices use), so both can run at once.
dev: migrate
	@trap 'kill $$(jobs -p) 2>/dev/null' EXIT INT TERM; \
	(cd backend && ARGOS_PORT=8100 exec uv run uvicorn argos.main:app --reload --reload-dir src --host 127.0.0.1 --port 8100) & \
	(cd frontend && exec bun run dev) & \
	wait

test:
	cd backend && uv run pytest -q
	cd frontend && bun run test

lint:
	cd backend && uv run ruff check . && uv run ruff format --check . && uv run pyright
	cd frontend && bun run lint && bun run typecheck

format:
	cd backend && uv run ruff check --fix . && uv run ruff format .
	cd frontend && bun run format

# Regenerates frontend/src/api-types.ts from the FastAPI OpenAPI schema.
api-types:
	cd backend && uv run python -c "import json; from argos.main import app; print(json.dumps(app.openapi()))" > ../frontend/openapi.json
	cd frontend && bunx openapi-typescript openapi.json -o src/api-types.ts && rm openapi.json

# The desktop app (PLAN Phase 12): frontend build + PyInstaller server sidecar + Tauri
# shell. Output: desktop/build/Argos.app and desktop/build/Argos.dmg (drag to Applications).
# The dmg is made with hdiutil: Tauri's styled dmg drives Finder over AppleScript, which
# needs an automation permission a terminal often lacks.
TRIPLE := $(shell rustc -vV | sed -n 's/^host: //p')
app:
	cd frontend && bun run build
	cd backend && uv run --group desktop pyinstaller --noconfirm --clean \
		--distpath ../desktop/build/dist --workpath ../desktop/build/work ../desktop/argos-server.spec
	mkdir -p desktop/src-tauri/binaries
	cp desktop/build/dist/argos-server desktop/src-tauri/binaries/argos-server-$(TRIPLE)
	# Update artifacts need the updater key: CI gets it from secrets; locally it is read from
	# ~/.tauri/argos-updater.key (password in the Keychain). Without it: no update files.
	cd desktop && bun install && \
	if [ -n "$$TAURI_SIGNING_PRIVATE_KEY" ]; then bunx tauri build; \
	elif [ -f ~/.tauri/argos-updater.key ]; then \
		TAURI_SIGNING_PRIVATE_KEY="$$(cat ~/.tauri/argos-updater.key)" \
		TAURI_SIGNING_PRIVATE_KEY_PASSWORD="$$(security find-generic-password -s argos-updater-key-password -w)" \
		bunx tauri build; \
	else bunx tauri build --config '{"bundle":{"createUpdaterArtifacts":false}}'; fi
	rm -rf desktop/build/Argos.app desktop/build/dmg desktop/build/Argos.dmg
	mkdir -p desktop/build/dmg
	cp -R desktop/src-tauri/target/release/bundle/macos/Argos.app desktop/build/
	cp -R desktop/build/Argos.app desktop/build/dmg/ && ln -s /Applications desktop/build/dmg/Applications
	hdiutil create -volname Argos -srcfolder desktop/build/dmg -ov -format UDZO desktop/build/Argos.dmg
	rm -rf desktop/build/dmg

# Publishes a new app version: bumps the version, commits, tags and pushes; the Release
# workflow builds, signs and uploads it, and installed apps update themselves.
# Usage: make release VERSION=0.2.0
release:
	@test -n "$(VERSION)" || { echo "usage: make release VERSION=x.y.z"; exit 1; }
	@test -z "$$(git status --porcelain)" || { echo "commit or stash your changes first"; exit 1; }
	python3 -c "import json,sys; p='desktop/src-tauri/tauri.conf.json'; d=json.load(open(p)); d['version']='$(VERSION)'; open(p,'w').write(json.dumps(d, indent=2, ensure_ascii=False)+'\n')"
	sed -i '' 's/^version = ".*"/version = "$(VERSION)"/' desktop/src-tauri/Cargo.toml
	cd desktop/src-tauri && cargo update -p argos-desktop --offline -q
	git commit -am "chore: release v$(VERSION)"
	git tag "v$(VERSION)"
	git push && git push origin "v$(VERSION)"
