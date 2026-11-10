#!/bin/sh
# Starts ZIMaker in its container.
# Started as root (the default): picks the user ZIMaker runs as, makes sure the data and output folders can be
# written by that user, then drops root for good. The user is PUID:PGID if set; otherwise the owner of the folder
# mounted at /output, so ZIM files belong to whoever owns the folder they are saved in (1000:1000 if that is root).
# Started as another user (docker run --user, or "user:" in a compose file): runs as that user, changing nothing.
set -e
. "$(dirname "$0")/docker-env.sh"

if [ "$(id -u)" = "0" ]; then
  mkdir -p "$ZIMAKER_HOME" "$ZIMAKER_OUTPUT"
  if [ -z "$PUID" ]; then
    PUID="$(stat -c %u "$ZIMAKER_OUTPUT")"
    [ -n "$PGID" ] || PGID="$(stat -c %g "$ZIMAKER_OUTPUT")"
    if [ "$PUID" = "0" ]; then
      PUID=1000
      PGID=1000
    fi
  fi
  PGID="${PGID:-$PUID}"
  for d in "$ZIMAKER_HOME" "$ZIMAKER_OUTPUT"; do
    # only the folder itself, and only if it isn't that user's already: files inside are left as they are
    if [ "$(stat -c %u "$d")" != "$PUID" ]; then
      chown "$PUID:$PGID" "$d" 2>/dev/null || echo "ZIMaker: couldn't change the owner of $d; if saving fails, make it writable for user $PUID" >&2
    fi
  done
  echo "ZIMaker runs as user $PUID, group $PGID"
  exec setpriv --reuid "$PUID" --regid "$PGID" --clear-groups "$@"
fi
exec "$@"
