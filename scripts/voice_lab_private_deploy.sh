#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"
VENV_PY="$ROOT/apps/api/.venv/bin/python"
SITE_BASE="$ROOT/artifacts/voice-lab-private-site"
CURRENT_SITE="$SITE_BASE/current"
PARENT_SITE="$SITE_BASE/parent-current"
RELEASES_DIR="$SITE_BASE/releases"
SESSION_NAME="voice-lab-private-site"
HOST="127.0.0.1"
PORT="18767"
LOG_PATH="$SITE_BASE/server.log"
PID_PATH="$SITE_BASE/server.pid"

ensure_venv() {
  "$ROOT/scripts/ensure-venv.sh" apps/api/.venv apps/api/requirements.txt
}

current_url() {
  printf 'http://%s:%s/' "$HOST" "$PORT"
}

has_tmux() {
  command -v tmux >/dev/null 2>&1
}

server_pid() {
  if [ -f "$PID_PATH" ]; then
    cat "$PID_PATH"
  fi
}

server_running() {
  local pid
  pid="$(server_pid)"
  [ -n "$pid" ] && kill -0 "$pid" >/dev/null 2>&1
}

clear_stale_pid() {
  if [ -f "$PID_PATH" ] && ! server_running; then
    rm -f "$PID_PATH"
  fi
}

require_site() {
  if [ ! -f "$CURRENT_SITE/index.html" ]; then
    echo "Missing published site at $CURRENT_SITE. Run '$0 build' first." >&2
    exit 1
  fi
}

archive_current_site() {
  if [ ! -d "$CURRENT_SITE" ]; then
    return 0
  fi

  mkdir -p "$RELEASES_DIR"
  local stamp archive_dir
  stamp="$(date -u +%Y%m%dT%H%M%SZ)"
  archive_dir="$RELEASES_DIR/site-$stamp"
  cp -R "$CURRENT_SITE" "$archive_dir"
  echo "$archive_dir"
}

mirror_parent_site() {
  rm -rf "$PARENT_SITE"
  cp -R "$CURRENT_SITE" "$PARENT_SITE"
}

build_site() {
  ensure_venv
  local archived_dir manifest_path
  archived_dir="$(archive_current_site)"
  if [ -n "$archived_dir" ]; then
    echo "Archived previous site to $archived_dir"
  fi
  "$VENV_PY" scripts/voice_lab_private_site.py build     --artifact-root artifacts/voice-lab     --site-root artifacts/voice-lab-private-site/current
  mirror_parent_site
  manifest_path="$CURRENT_SITE/manifest.json"
  echo "Published site: $CURRENT_SITE"
  echo "Mirrored site: $PARENT_SITE"
  echo "Manifest: $manifest_path"
}

start_tmux_site() {
  if tmux has-session -t "$SESSION_NAME" 2>/dev/null; then
    echo "tmux session '$SESSION_NAME' is already running"
  else
    : > "$LOG_PATH"
    tmux new-session -d -s "$SESSION_NAME"       "cd '$ROOT' && exec '$VENV_PY' scripts/voice_lab_private_site.py serve --site-root artifacts/voice-lab-private-site/current --host $HOST --port $PORT >> '$LOG_PATH' 2>&1"
  fi
}

start_pid_site() {
  clear_stale_pid
  if server_running; then
    echo "background server is already running with pid $(server_pid)"
    return 0
  fi

  : > "$LOG_PATH"
  nohup "$VENV_PY" scripts/voice_lab_private_site.py serve     --site-root artifacts/voice-lab-private-site/current     --host "$HOST"     --port "$PORT" >> "$LOG_PATH" 2>&1 &
  echo $! > "$PID_PATH"
}

start_site() {
  require_site
  mkdir -p "$SITE_BASE"
  if has_tmux; then
    start_tmux_site
  else
    start_pid_site
  fi
  sleep 1
  curl --fail --show-error --silent "$(current_url)" >/dev/null
  echo "Serving $(current_url)"
  echo "Log: $LOG_PATH"
  if has_tmux; then
    echo "Session: tmux:$SESSION_NAME"
  else
    echo "Session: pid:$(server_pid)"
  fi
}

stop_tmux_site() {
  if tmux has-session -t "$SESSION_NAME" 2>/dev/null; then
    tmux kill-session -t "$SESSION_NAME"
    echo "Stopped tmux session '$SESSION_NAME'"
  else
    echo "tmux session '$SESSION_NAME' is not running"
  fi
}

stop_pid_site() {
  clear_stale_pid
  if server_running; then
    local pid
    pid="$(server_pid)"
    kill "$pid"
    rm -f "$PID_PATH"
    echo "Stopped background server pid $pid"
  else
    echo "background server is not running"
  fi
}

stop_site() {
  if has_tmux; then
    stop_tmux_site
  else
    stop_pid_site
  fi
}

status_site() {
  require_site
  clear_stale_pid
  local session_state http_state session_backend session_ref
  if has_tmux; then
    session_backend="tmux"
    session_ref="$SESSION_NAME"
    if tmux has-session -t "$SESSION_NAME" 2>/dev/null; then
      session_state="running"
    else
      session_state="stopped"
    fi
  else
    session_backend="pid"
    session_ref="$(server_pid)"
    if server_running; then
      session_state="running"
    else
      session_state="stopped"
    fi
  fi

  if curl --fail --show-error --silent "$(current_url)" >/dev/null 2>&1; then
    http_state="reachable"
  else
    http_state="unreachable"
  fi

  printf '{
'
  printf '  "url": "%s",
' "$(current_url)"
  printf '  "session_backend": "%s",
' "$session_backend"
  printf '  "session_ref": "%s",
' "$session_ref"
  printf '  "session_state": "%s",
' "$session_state"
  printf '  "http_state": "%s",
' "$http_state"
  printf '  "current_manifest": "%s",
' "$CURRENT_SITE/manifest.json"
  printf '  "parent_manifest": "%s",
' "$PARENT_SITE/manifest.json"
  printf '  "log_path": "%s"
' "$LOG_PATH"
  printf '}
'
}

smoke_site() {
  require_site
  curl --fail --show-error --silent "$(current_url)" >/dev/null
  ensure_venv
  local stamp smoke_path
  stamp="$(date -u +%Y%m%dT%H%M%SZ)"
  smoke_path="$SITE_BASE/smoke-$stamp.json"
  "$VENV_PY" scripts/voice_lab_private_site.py smoke --base-url "$(current_url)" > "$smoke_path"
  echo "Saved smoke report: $smoke_path"
  cat "$smoke_path"
}

rollback_site() {
  local requested_release="${1:-latest}"
  local release_dir
  mkdir -p "$RELEASES_DIR"

  if [ "$requested_release" = "latest" ]; then
    release_dir="$(find "$RELEASES_DIR" -mindepth 1 -maxdepth 1 -type d -name 'site-*' | sort | tail -n 1)"
  else
    release_dir="$RELEASES_DIR/$requested_release"
  fi

  if [ -z "${release_dir:-}" ] || [ ! -d "$release_dir" ]; then
    echo "Unable to find rollback release: $requested_release" >&2
    exit 1
  fi

  stop_site >/dev/null
  rm -rf "$CURRENT_SITE" "$PARENT_SITE"
  cp -R "$release_dir" "$CURRENT_SITE"
  cp -R "$release_dir" "$PARENT_SITE"
  rm -f "$PID_PATH"
  echo "Restored site from $release_dir"
  echo "Current site: $CURRENT_SITE"
  echo "Parent mirror: $PARENT_SITE"
}

usage() {
  echo "Usage: $0 <build|start|status|smoke|stop|rollback> [release-id]" >&2
}

main() {
  local command="${1:-}"
  case "$command" in
    build)
      build_site
      ;;
    start)
      start_site
      ;;
    status)
      status_site
      ;;
    smoke)
      smoke_site
      ;;
    stop)
      stop_site
      ;;
    rollback)
      rollback_site "${2:-latest}"
      ;;
    *)
      usage
      exit 1
      ;;
  esac
}

main "$@"
