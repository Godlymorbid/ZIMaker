#!/bin/sh
# Starts ZIMaker in its container. Started as root (the default): makes sure the data and output folders can be
# written by the user ZIMaker runs as (PUID:PGID, 1000:1000 unless set), then drops root for good. Started as
# another user (docker run --user, or "user:" in docker-compose.yml): runs as that user, changing nothing.
set -e

if [ "$(id -u)" = "0" ]; then
  PUID="${PUID:-1000}"
  PGID="${PGID:-1000}"
  for d in "${ZIMAKER_HOME:-/data}" "${ZIMAKER_OUTPUT:-/output}"; do
    mkdir -p "$d"
    # only the folder itself, and only if it isn't that user's already: files inside are left as they are
    if [ "$(stat -c %u "$d")" != "$PUID" ]; then
      chown "$PUID:$PGID" "$d" 2>/dev/null || echo "ZIMaker: couldn't change the owner of $d; if saving fails, make it writable for user $PUID" >&2
    fi
  done
  exec setpriv --reuid "$PUID" --regid "$PGID" --clear-groups "$@"
fi
exec "$@"
