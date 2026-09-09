#!/bin/zsh
# Root-level shortcut for the existing double-click workflow.
set -euo pipefail

SOURCE_PATH="${(%):-%N}"
PROJECT_ROOT="$(cd -P "$(dirname "$SOURCE_PATH")" && pwd)"
exec /bin/zsh "$PROJECT_ROOT/scripts/Photo Caption Print.command"
