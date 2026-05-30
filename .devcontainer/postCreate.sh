#!/usr/bin/env bash
set -euo pipefail

cd "$(git rev-parse --show-toplevel)"

export PATH="$HOME/.local/bin:$PATH"
export UV_NO_CONFIG=1
export HERMES_HOME="${HERMES_HOME:-/workspaces/.hermes-personal-stable}"

python -m pip install --user "uv==0.11.17"
if [ -x "$HOME/.local/bin/uv" ] && command -v sudo >/dev/null 2>&1; then
  sudo ln -sf "$HOME/.local/bin/uv" /usr/local/bin/uv
fi
uv python install 3.11
uv venv .venv --python 3.11 --allow-existing

if UV_PROJECT_ENVIRONMENT="$PWD/.venv" uv sync --extra all --locked; then
  echo "Installed Python dependencies from uv.lock."
else
  echo "uv sync failed; falling back to editable dev install without lockfile hash verification." >&2
  uv pip install -e ".[all,dev]"
fi

if [ -f package-lock.json ]; then
  npm ci
fi

if [ -f ui-tui/package-lock.json ]; then
  npm ci --prefix ui-tui
fi

mkdir -p "$HERMES_HOME"/{cron,sessions,logs,memories,skills}
if [ ! -f "$HERMES_HOME/config.yaml" ]; then
  cp cli-config.yaml.example "$HERMES_HOME/config.yaml"
fi
touch "$HERMES_HOME/.env"
chmod 600 "$HERMES_HOME/.env" || true

.venv/bin/python -m hermes_cli.main --version
