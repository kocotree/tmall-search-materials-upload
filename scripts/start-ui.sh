#!/usr/bin/env sh
set -eu

SCRIPT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
PROJECT_ROOT=$(CDPATH= cd -- "$SCRIPT_DIR/.." && pwd)
export TMALL_PLUGIN_ROOT="$PROJECT_ROOT"
export TMALL_WORKSPACE_ROOT="$PROJECT_ROOT"
RUNTIME_ROOT=${TMALL_RUNTIME_ROOT:-${XDG_STATE_HOME:-$HOME/.local/state}/tmall-search-materials/runtime}
CLI="$RUNTIME_ROOT/.venv/bin/tmall-materials"

if [ ! -x "$CLI" ]; then
  "$SCRIPT_DIR/bootstrap.sh"
fi
exec "$CLI" ui-start "$@"
