#!/bin/bash
# Double-click launcher for the LP preview tool web app (macOS equivalent of
# "Start LP tool.bat"). Kills any already-running instance first, so a
# background copy can't keep serving stale code after an update, starts the
# app minimized to the background, and opens it in the browser.
#
# Resolves its own real location even when launched through a symlink/alias
# (e.g. a shortcut placed on the Desktop) - $0 would otherwise point at the
# symlink's path, not this script's actual folder, and `cd` would land in
# the wrong place.
SOURCE="${BASH_SOURCE[0]}"
while [ -h "$SOURCE" ]; do
  DIR="$(cd -P "$(dirname "$SOURCE")" >/dev/null 2>&1 && pwd)"
  SOURCE="$(readlink "$SOURCE")"
  [[ $SOURCE != /* ]] && SOURCE="$DIR/$SOURCE"
done
DIR="$(cd -P "$(dirname "$SOURCE")" >/dev/null 2>&1 && pwd)"
cd "$DIR"

# Kills only the exact process from the last run, tracked by PID in
# .app.pid (app.py writes its own pid there on startup) - not a
# `pkill -f app.py` pattern match, which would kill any unrelated Python
# script anywhere on the system that happens to share that common filename.
PID_FILE="$DIR/.app.pid"
if [ -f "$PID_FILE" ]; then
  OLD_PID="$(cat "$PID_FILE" 2>/dev/null)"
  if [ -n "$OLD_PID" ] && kill -0 "$OLD_PID" 2>/dev/null; then
    kill "$OLD_PID" 2>/dev/null
  fi
  rm -f "$PID_FILE"
fi
sleep 1
source .venv/bin/activate
nohup python app.py > app.log 2>&1 &
sleep 2
open http://localhost:5001
