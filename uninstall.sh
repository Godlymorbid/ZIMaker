#!/bin/sh
# ZIM Creator uninstaller for Linux:  sh uninstall.sh   (ZIM files you made are never touched)
DATA="${XDG_DATA_HOME:-$HOME/.local/share}"
DEST="$DATA/zim-creator"

echo "Uninstalling ZIM Creator"
python3 -c "import urllib.request; urllib.request.urlopen(urllib.request.Request('http://localhost:8765/api/quit', data=b'{\"force\": true}', method='POST'), timeout=3)" >/dev/null 2>&1 && sleep 2

DESKTOP="$(xdg-user-dir DESKTOP 2>/dev/null || echo "$HOME/Desktop")"
for f in "$DATA/applications/zim-creator.desktop" "$DESKTOP/zim-creator.desktop" "$HOME/.local/bin/zim-creator"; do
  if [ -e "$f" ] || [ -L "$f" ]; then rm -f "$f" && echo "Removed $f"; fi
done

if [ "$1" = "--yes" ]; then answer=y; else
  printf "Also remove its libraries and job logs (~/.zim-creator, about 150 MB)? [Y/n] "
  read -r answer
fi
case "$answer" in
  n|N) ;;
  *) rm -rf "$HOME/.zim-creator" && echo "Removed ~/.zim-creator" ;;
esac

if [ -f "$DEST/zim-creator.sh" ]; then rm -rf "$DEST" && echo "Removed $DEST"; fi
echo "ZIM Creator has been uninstalled. ZIM files you made were not touched."
