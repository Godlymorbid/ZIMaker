#!/bin/sh
# ZIM Creator launcher for Linux: starts the converter if it isn't running, then opens its window.
# The converter stops by itself once the window is closed and no conversion is running.
DIR="$(cd "$(dirname "$(readlink -f "$0")")" && pwd)"
URL="http://localhost:8765/"

up() {
  python3 -c "import urllib.request; urllib.request.urlopen('${URL}api/ping', timeout=2)" >/dev/null 2>&1
}

if ! up; then
  mkdir -p "$HOME/.zim-creator"
  nohup python3 -I "$DIR/app/server.py" >"$HOME/.zim-creator/server.log" 2>&1 &
  i=0
  while ! up && [ "$i" -lt 90 ]; do sleep 1; i=$((i + 1)); done
fi
if ! up; then
  msg="ZIM Creator didn't start. Details: $HOME/.zim-creator/server.log"
  command -v notify-send >/dev/null 2>&1 && notify-send "ZIM Creator" "$msg"
  echo "$msg" >&2
  exit 1
fi

# its own window if a Chromium-based browser is installed, otherwise the default browser
for b in google-chrome google-chrome-stable chromium chromium-browser microsoft-edge microsoft-edge-stable brave-browser; do
  if command -v "$b" >/dev/null 2>&1; then
    exec "$b" --app="$URL" --window-size=1000,950 >/dev/null 2>&1
  fi
done
exec xdg-open "$URL" >/dev/null 2>&1
