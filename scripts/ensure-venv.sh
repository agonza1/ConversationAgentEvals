#!/usr/bin/env bash
set -euo pipefail

venv_dir="${1:?usage: ensure-venv.sh VENV_DIR [REQUIREMENTS_FILE]}"
requirements_file="${2:-}"

if [ -x "$venv_dir/bin/python" ]; then
  if [ -n "$requirements_file" ] && [ -x "$venv_dir/bin/pip" ]; then
    "$venv_dir/bin/pip" install -q -r "$requirements_file"
  fi
  exit 0
fi

if [ -d "$venv_dir" ]; then
  rm -rf "$venv_dir"
fi

python_bin=""

for candidate in python3.13 /opt/homebrew/bin/python3.13 /opt/homebrew/bin/python3 python3; do
  if command -v "$candidate" >/dev/null 2>&1 && "$candidate" - <<'PY' >/dev/null 2>&1
import ensurepip
import venv
PY
  then
    python_bin="$(command -v "$candidate")"
    break
  fi
done

if [ -z "$python_bin" ]; then
  echo "No usable python3 interpreter with venv/ensurepip found for $venv_dir" >&2
  exit 1
fi

"$python_bin" -m venv "$venv_dir"

if [ -n "$requirements_file" ]; then
  "$venv_dir/bin/pip" install -q -r "$requirements_file"
fi
