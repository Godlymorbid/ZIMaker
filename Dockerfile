# ZIMaker in a container: the same converter and window as on Windows and Linux, opened in a browser at
# http://localhost:8765. See "Docker" in README.md, and docker-compose.yml for the usual way to run it.
#
#   docker compose up -d --build
FROM python:3.12-slim-bookworm

# The optional tools (Settings -> Optional tools) come with the image. LibreOffice is the big one (about 400 MB):
# leave it out with  --build-arg LIBREOFFICE=0  if you never convert old Office files (DOC, XLS, PPT).
ARG LIBREOFFICE=1
RUN apt-get update \
 && apt-get install -y --no-install-recommends ca-certificates curl tini ffmpeg 7zip unar fonts-dejavu fonts-liberation \
 && if [ "$LIBREOFFICE" = "1" ]; then apt-get install -y --no-install-recommends \
      libreoffice-writer-nogui libreoffice-calc-nogui libreoffice-impress-nogui libreoffice-draw-nogui; fi \
 && rm -rf /var/lib/apt/lists/*

# ZIMAKER_HOME:    settings, the Jobs list and uploads: a volume, so they outlive the container
# ZIMAKER_RUNTIME: libzim, Pillow, PyMuPDF, Pandoc and zimcheck: part of the image, so a new image brings new ones
# ZIMAKER_OUTPUT:  where ZIM files are saved unless a profile says otherwise: a folder of yours, mounted here
ENV ZIMAKER_DOCKER=1 \
    ZIMAKER_HOME=/data \
    ZIMAKER_RUNTIME=/opt/zimaker-runtime \
    ZIMAKER_OUTPUT=/output \
    HOME=/data \
    PUID=1000 \
    PGID=1000

# The whole repository, so "Check for updates" can compare this copy with GitHub file by file.
COPY . /opt/zimaker

# The same setup the Windows and Linux installers run, done once here instead of on first start; plus HEIC/AVIF
# support, which the app otherwise adds the first time a photo needs it (the image's libraries are read-only).
RUN sed -i 's/\r$//' /opt/zimaker/docker-entrypoint.sh \
 && mkdir -p /data /output /watch /input "$ZIMAKER_RUNTIME" \
 && python3 -I /opt/zimaker/app/setup.py \
 && python3 -I -c "import sys; sys.path.insert(0, '/opt/zimaker/app'); from zc import deps; deps.enable_heif()" \
 && rm -rf /data/* /data/.[!.]* \
 && chmod -R a+rX /opt/zimaker /opt/zimaker-runtime

VOLUME ["/data", "/output"]
EXPOSE 8765
HEALTHCHECK --interval=1m --timeout=5s --start-period=20s \
  CMD curl -fsS -o /dev/null http://localhost:8765/api/ping || exit 1

# tini passes "docker stop" on to ZIMaker (which then cancels a running job cleanly) and tidies up after the
# helper programs; the entrypoint switches from root to PUID:PGID, so ZIM files belong to you and not to root.
ENTRYPOINT ["tini", "--", "sh", "/opt/zimaker/docker-entrypoint.sh"]
CMD ["python3", "-I", "/opt/zimaker/app/server.py"]
