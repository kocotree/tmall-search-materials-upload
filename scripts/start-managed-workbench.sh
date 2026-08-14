#!/usr/bin/env sh
set -eu

SCRIPT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
PROJECT_ROOT=$(CDPATH= cd -- "$SCRIPT_DIR/.." && pwd)
RUNTIME_ROOT=${TMALL_RUNTIME_ROOT:-${XDG_STATE_HOME:-$HOME/.local/state}/tmall-search-materials/runtime}
PYTHON="$RUNTIME_ROOT/.venv/bin/python"

if [ ! -x "$PYTHON" ]; then
  echo "PREPARED_RUNTIME_MISSING: run scripts/bootstrap.sh first." >&2
  exit 2
fi

export TMALL_PLUGIN_ROOT="$PROJECT_ROOT"
export TMALL_WORKSPACE_ROOT="$PROJECT_ROOT"

exec "$PYTHON" "$SCRIPT_DIR/run-plugin.py" desktop-workbench --project-root "$PROJECT_ROOT" "$@"
