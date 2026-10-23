#!/bin/sh
# ZIMaker uninstaller for Linux:  sh uninstall.sh   (ZIM files you made are never touched)
DATA="${XDG_DATA_HOME:-$HOME/.local/share}"
DEST="$DATA/zimaker"

echo "Uninstalling ZIMaker"
python3 -c "import urllib.request; urllib.request.urlopen(urllib.request.Request('http://localhost:8765/api/quit', data=b'{\"force\": true}', method='POST'), timeout=3)" >/dev/null 2>&1 && sleep 2

DESKTOP="$(xdg-user-dir DESKTOP 2>/dev/null || echo "$HOME/Desktop")"
for f in "$DATA/applications/zimaker.desktop" "$DESKTOP/zimaker.desktop" "$HOME/.local/bin/zimaker"; do
  if [ -e "$f" ] || [ -L "$f" ]; then rm -f "$f" && echo "Removed $f"; fi
done

if [ "$1" = "--yes" ]; then answer=y; else
  printf "Also remove its libraries and job logs (~/.zimaker, about 150 MB)? [Y/n] "
  read -r answer
fi
case "$answer" in
  n|N) ;;
  *) rm -rf "$HOME/.zimaker" && echo "Removed ~/.zimaker" ;;
esac

if [ -f "$DEST/zimaker.sh" ]; then rm -rf "$DEST" && echo "Removed $DEST"; fi
echo "ZIMaker has been uninstalled. ZIM files you made were not touched."
