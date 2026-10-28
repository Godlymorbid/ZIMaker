#!/bin/sh
# ZIMaker installer for Linux (per user, no root needed):  sh install.sh
set -e
SRC="$(cd "$(dirname "$0")" && pwd)"
DATA="${XDG_DATA_HOME:-$HOME/.local/share}"
DEST="$DATA/zimaker"

echo "ZIMaker installer"
echo "Turns websites, archive.org books, ebooks, documents and pictures into ZIM files for Kiwix."
echo

if ! command -v python3 >/dev/null 2>&1; then
  echo "Python 3 is needed. Install it with your package manager, e.g.:  sudo apt install python3" >&2
  exit 1
fi
if ! python3 -c 'import sys; sys.exit(0 if sys.version_info >= (3, 9) else 1)'; then
  echo "Python 3.9 or newer is needed (found $(python3 --version 2>&1))." >&2
  exit 1
fi
case "$(uname -m)" in
  x86_64|aarch64) ;;
  *) echo "Sorry, only 64-bit Intel/AMD (x86_64) and ARM (aarch64) PCs are supported." >&2; exit 1 ;;
esac

# stop a running copy (when upgrading)
python3 -c "import urllib.request; urllib.request.urlopen(urllib.request.Request('http://localhost:8765/api/quit', data=b'{\"force\": true}', method='POST'), timeout=3)" >/dev/null 2>&1 && sleep 2 || true

echo "== Installing to $DEST"
mkdir -p "$DEST"
if [ "$SRC" != "$DEST" ]; then
  cp -R "$SRC/." "$DEST/"
fi
find "$DEST" -name __pycache__ -type d -prune -exec rm -rf {} + 2>/dev/null || true
chmod +x "$DEST/zimaker.sh" "$DEST/install.sh" "$DEST/uninstall.sh"

echo
echo "== Setting up the converter's libraries (first time: about 150 MB)"
python3 -I "$DEST/app/setup.py"

echo
echo "== Adding ZIMaker to your applications"
mkdir -p "$HOME/.local/bin" "$DATA/applications"
ln -sf "$DEST/zimaker.sh" "$HOME/.local/bin/zimaker"
ENTRY="$DATA/applications/zimaker.desktop"
cat > "$ENTRY" <<EOF
[Desktop Entry]
Type=Application
Name=ZIMaker
Comment=Turn websites, books and documents into ZIM files for Kiwix
Exec="$DEST/zimaker.sh"
Icon=$DEST/app/icon.png
Terminal=false
Categories=Utility;Office;
EOF
chmod +x "$ENTRY"
command -v update-desktop-database >/dev/null 2>&1 && update-desktop-database "$DATA/applications" >/dev/null 2>&1 || true
echo "  applications menu: $ENTRY"
echo "  command:           zimaker"

DESKTOP="$(xdg-user-dir DESKTOP 2>/dev/null || echo "$HOME/Desktop")"
if [ -d "$DESKTOP" ]; then
  cp "$ENTRY" "$DESKTOP/zimaker.desktop"
  chmod +x "$DESKTOP/zimaker.desktop"
  command -v gio >/dev/null 2>&1 && gio set "$DESKTOP/zimaker.desktop" metadata::trusted true >/dev/null 2>&1 || true
  echo "  desktop icon:      $DESKTOP/zimaker.desktop"
fi

# remove the old install from before the rename ("ZIM Creator"), if there is one
for f in "$DATA/applications/zim-creator.desktop" "$DESKTOP/zim-creator.desktop" "$HOME/.local/bin/zim-creator"; do
  if [ -e "$f" ] || [ -L "$f" ]; then rm -f "$f" && echo "  removed old $f"; fi
done
if [ -f "$DATA/zim-creator/zim-creator.sh" ]; then rm -rf "$DATA/zim-creator" && echo "  removed old install $DATA/zim-creator"; fi

echo
echo "ZIMaker is installed. Open it from your applications menu, or run: zimaker"
echo "To remove it, run: sh \"$DEST/uninstall.sh\""
