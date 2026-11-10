#!/bin/sh
# Builds ZIMaker's Docker image, from the copy of ZIMaker in /opt/zimaker (run by the Dockerfile, and by the
# Dockerfile inside docker-compose.nas.yml). LIBREOFFICE=0 leaves LibreOffice out (about 400 MB smaller).
set -e
D=/opt/zimaker
. "$D/docker-env.sh"

# The optional tools (Settings -> Optional tools), unless an earlier build step installed them already.
# zimcheck comes from Debian's zim-tools package: openZIM's own download is too slow from build servers.
PKGS="ca-certificates curl tini ffmpeg 7zip unar zim-tools fonts-dejavu fonts-liberation"
OFFICE="libreoffice-writer-nogui libreoffice-calc-nogui libreoffice-impress-nogui libreoffice-draw-nogui"
if [ "${LIBREOFFICE:-1}" = "1" ] && ! dpkg -s libreoffice-writer-nogui >/dev/null 2>&1; then
  PKGS="$PKGS $OFFICE"
  INSTALL=1
fi
if ! command -v tini >/dev/null 2>&1 || ! command -v ffmpeg >/dev/null 2>&1 || ! command -v zimcheck >/dev/null 2>&1; then
  INSTALL=1
fi
if [ -n "$INSTALL" ]; then
  apt-get update
  # shellcheck disable=SC2086
  apt-get install -y --no-install-recommends $PKGS
  rm -rf /var/lib/apt/lists/*
fi

# The same setup the Windows and Linux installers run, done once here instead of on first start; plus HEIC/AVIF
# support, which the app otherwise adds the first time a photo needs it (the image's libraries are read-only).
mkdir -p "$ZIMAKER_HOME" "$ZIMAKER_OUTPUT" "$ZIMAKER_RUNTIME" /watch /input
python3 -I "$D/app/setup.py"
# Pandoc is optional on a desktop (it can be fetched later); an image can't add it later, so a download that
# failed is tried once more, and a build without it or without zimcheck fails instead of shipping incomplete
if ! ls "$ZIMAKER_RUNTIME"/pandoc/*/bin/pandoc >/dev/null 2>&1; then
  python3 -I "$D/app/setup.py"
  ls "$ZIMAKER_RUNTIME"/pandoc/*/bin/pandoc >/dev/null
fi
command -v zimcheck >/dev/null
python3 -I -c "import sys; sys.path.insert(0, '$D/app'); from zc import deps; deps.enable_heif()"
find "$ZIMAKER_HOME" -mindepth 1 -delete
find "$D" -name __pycache__ -type d -prune -exec rm -rf {} +
# compiled now: the user ZIMaker runs as can't write next to the code, and every job starts a new Python
python3 -I -m compileall -q "$D/app" "$ZIMAKER_RUNTIME/lib" >/dev/null 2>&1 || true
chmod -R a+rX "$D" "$ZIMAKER_RUNTIME"
