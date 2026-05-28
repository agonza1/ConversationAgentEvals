#!/usr/bin/env bash
set -euo pipefail

venv_dir="${1:?usage: ensure-venv.sh VENV_DIR [REQUIREMENTS_FILE]}"
requirements_file="${2:-}"

if [ -x "$venv_dir/bin/python" ]; then
  if [ -n "$requirements_file" ] && "$venv_dir/bin/python" -m pip --version >/dev/null 2>&1; then
    "$venv_dir/bin/python" -m pip install -q -r "$requirements_file"
  fi
  exit 0
fi

if [ -d "$venv_dir" ]; then
  rm -rf "$venv_dir"
fi

python_bin=""
python_has_ensurepip=""

for candidate in python3.13 python3.12 python3.11 /opt/homebrew/bin/python3.13 /opt/homebrew/bin/python3 python3; do
  if command -v "$candidate" >/dev/null 2>&1 && "$candidate" - <<'PY' >/dev/null 2>&1
import venv
PY
  then
    python_bin="$(command -v "$candidate")"
    if "$candidate" - <<'PY' >/dev/null 2>&1
import ensurepip
PY
    then
      python_has_ensurepip=1
    fi
    break
  fi
done

if [ -z "$python_bin" ]; then
  echo "No usable python3 interpreter with venv found for $venv_dir" >&2
  exit 1
fi

if [ -n "$python_has_ensurepip" ]; then
  "$python_bin" -m venv "$venv_dir"
else
  "$python_bin" -m venv --without-pip "$venv_dir"
fi

if ! "$venv_dir/bin/python" -m pip --version >/dev/null 2>&1; then
  tmp_get_pip="$(mktemp)"
  cleanup() {
    rm -f "$tmp_get_pip"
  }
  trap cleanup EXIT

  "$python_bin" - <<'PY' >"$tmp_get_pip"
from urllib.request import urlopen

with urlopen("https://bootstrap.pypa.io/get-pip.py", timeout=30) as response:
    print(response.read().decode("utf-8"), end="")
PY
  "$venv_dir/bin/python" "$tmp_get_pip" --quiet
fi

if [ -n "$requirements_file" ]; then
  "$venv_dir/bin/python" -m pip install -q -r "$requirements_file"
fi
