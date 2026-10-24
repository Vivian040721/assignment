#!/usr/bin/env sh
set -eu
task_root=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
cd "$task_root"
"$task_root/.venv-v2/bin/python" -m PyInstaller --noconfirm --distpath data/release/backend --workpath data/build/backend scripts/backend.spec
