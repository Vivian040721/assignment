#!/usr/bin/env sh
set -eu
task_root=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
cd "$task_root"
sh "$task_root/scripts/build-backend.sh"
npm run dist
printf '%s\n' 'Built packages for the current platform under data/release/desktop.'
