#!/usr/bin/env sh
set -eu

SCRIPT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
PROJECT_ROOT=$(CDPATH= cd -- "$SCRIPT_DIR/.." && pwd)
RUNTIME_ROOT=${TMALL_RUNTIME_ROOT:-${XDG_STATE_HOME:-$HOME/.local/state}/tmall-search-materials/runtime}
ENVIRONMENT_DIR="$RUNTIME_ROOT/.venv"
CACHE_DIR="$RUNTIME_ROOT/uv-cache"
MIRROR=${TMALL_UV_MIRROR:-official}
CONFIG_FILE="$PROJECT_ROOT/config/uv-$MIRROR.toml"

if ! command -v uv >/dev/null 2>&1; then
  echo "UV_NOT_FOUND: install uv first, then rerun this script." >&2
  exit 2
fi

mkdir -p "$RUNTIME_ROOT" "$CACHE_DIR"
PYTHON=${TMALL_PYTHON:-}
if [ -z "$PYTHON" ]; then
  PYTHON=$(uv --config-file "$CONFIG_FILE" --cache-dir "$CACHE_DIR" python find --no-managed-python '>=3.11' 2>/dev/null || true)
fi
if [ -z "$PYTHON" ]; then
  uv --config-file "$CONFIG_FILE" --cache-dir "$CACHE_DIR" python install 3.11 --default --system-certs
  PYTHON=$(uv --config-file "$CONFIG_FILE" --cache-dir "$CACHE_DIR" python find '3.11')
fi
if [ ! -x "$PYTHON" ]; then
  echo "PYTHON_NOT_AVAILABLE: set TMALL_PYTHON to Python >=3.11." >&2
  exit 2
fi

UV_PROJECT_ENVIRONMENT="$ENVIRONMENT_DIR" uv \
  --config-file "$CONFIG_FILE" \
  --cache-dir "$CACHE_DIR" \
  sync --locked --no-editable --python "$PYTHON" --no-managed-python --system-certs

CLI="$ENVIRONMENT_DIR/bin/tmall-materials"
if [ ! -x "$CLI" ]; then
  echo "CLI_SMOKE_TEST_FAILED: tmall-materials entry point was not installed." >&2
  exit 2
fi
"$CLI" --help
