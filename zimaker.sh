#!/bin/sh
# ZIMaker launcher for Linux: starts the converter if it isn't running, then opens its window.
# The converter stops by itself once the window is closed and no conversion is running.
DIR="$(cd "$(dirname "$(readlink -f "$0")")" && pwd)"
URL="http://localhost:8765/"

up() {
  python3 -c "import urllib.request; urllib.request.urlopen('${URL}api/ping', timeout=2)" >/dev/null 2>&1
}

if ! up; then
  mkdir -p "$HOME/.zimaker"
  nohup python3 -I "$DIR/app/server.py" >"$HOME/.zimaker/server.log" 2>&1 &
  i=0
  while ! up && [ "$i" -lt 90 ]; do sleep 1; i=$((i + 1)); done
fi
if ! up; then
  msg="ZIMaker didn't start. Details: $HOME/.zimaker/server.log"
  command -v notify-send >/dev/null 2>&1 && notify-send "ZIMaker" "$msg"
  echo "$msg" >&2
  exit 1
fi

# The browser chosen in Settings. Chromium-based browsers open ZIMaker in its own app window; Firefox has no app
# mode, so it gets a new window. "auto": the first Chromium-based browser installed. Otherwise the default browser.
CHOICE=$(python3 -c 'import json, os; print(json.load(open(os.path.expanduser("~/.zimaker/settings.json"))).get("browser", "auto"))' 2>/dev/null)
case "$CHOICE" in
  chrome)  LIST="google-chrome google-chrome-stable chromium chromium-browser" ;;
  brave)   LIST="brave-browser brave" ;;
  edge)    LIST="microsoft-edge microsoft-edge-stable" ;;
  firefox) LIST="" ;;
  default) LIST="" ;;
  *)       LIST="google-chrome google-chrome-stable chromium chromium-browser microsoft-edge microsoft-edge-stable brave-browser" ;;
esac
if [ "$CHOICE" = firefox ] && command -v firefox >/dev/null 2>&1; then
  exec firefox -new-window "$URL" >/dev/null 2>&1
fi
for b in $LIST; do
  if command -v "$b" >/dev/null 2>&1; then
    exec "$b" --app="$URL" --window-size=1000,950 >/dev/null 2>&1
  fi
done
exec xdg-open "$URL" >/dev/null 2>&1
