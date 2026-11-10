# ZIMaker in a container: the same converter and window as on Windows and Linux, opened in a browser at
# http://localhost:8765. See "Docker" in README.md.
#
#   docker compose up -d --build

# zimcheck (validates finished ZIMs) comes from openZIM's own image: their download server is too slow from build
# servers, and Debian's zim-tools package is too old for the ZIMs this app writes. That image exists for Intel/AMD
# only; on ARM, docker-build.sh leaves zimcheck out and ZIMs are checked with libzim instead.
FROM --platform=linux/amd64 ghcr.io/openzim/zim-tools:3.8.0 AS zimtools

FROM python:3.12-slim-bookworm

# The optional tools come with the image. LibreOffice is the big one (about 400 MB): leave it out with
# --build-arg LIBREOFFICE=0  if you never convert old Office files (DOC, XLS, PPT).
ARG LIBREOFFICE=1

# The whole repository, so "Check for updates" can compare this copy with GitHub file by file.
COPY . /opt/zimaker
COPY --from=zimtools /usr/local/bin/zimcheck /opt/zimcheck-amd64/zimcheck

# Everything else is in docker-build.sh (shared with docker-compose.nas.yml). The sed is for a copy checked out on
# Windows with CRLF line ends.
RUN sed -i 's/\r$//' /opt/zimaker/docker-*.sh && LIBREOFFICE="$LIBREOFFICE" sh /opt/zimaker/docker-build.sh

VOLUME ["/data", "/output"]
EXPOSE 8765
HEALTHCHECK --interval=1m --timeout=5s --start-period=20s \
  CMD curl -fsS -o /dev/null http://localhost:8765/api/ping || exit 1

# tini passes "docker stop" on to ZIMaker (which then cancels a running job cleanly) and tidies up after the
# helper programs; the entrypoint switches from root to an ordinary user, so ZIM files don't belong to root.
ENTRYPOINT ["tini", "--", "sh", "/opt/zimaker/docker-entrypoint.sh"]
CMD ["python3", "-I", "/opt/zimaker/app/server.py"]
