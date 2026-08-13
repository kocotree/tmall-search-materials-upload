#!/usr/bin/env sh
set -eu

SCRIPT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
PROJECT_ROOT=$(CDPATH= cd -- "$SCRIPT_DIR/.." && pwd)
RUNTIME_ROOT=${TMALL_RUNTIME_ROOT:-${XDG_STATE_HOME:-$HOME/.local/state}/tmall-search-materials/runtime}
CLI="$RUNTIME_ROOT/.venv/bin/tmall-materials"

if [ ! -x "$CLI" ]; then
  echo "PREPARED_RUNTIME_MISSING: run scripts/bootstrap.sh first." >&2
  exit 2
fi

export TMALL_PLUGIN_ROOT="$PROJECT_ROOT"
export TMALL_WORKSPACE_ROOT="$PROJECT_ROOT"

exec "$CLI" desktop-workbench --project-root "$PROJECT_ROOT" "$@"
