# Where things are inside the container (read by docker-build.sh and docker-entrypoint.sh).
#   ZIMAKER_HOME     settings, the Jobs list and uploads: a volume, so they outlive the container
#   ZIMAKER_RUNTIME  libzim, Pillow, PyMuPDF, Pandoc and zimcheck: part of the image
#   ZIMAKER_OUTPUT   where ZIM files are saved unless a profile says otherwise: a folder of yours, mounted here
export ZIMAKER_DOCKER=1
export ZIMAKER_HOME="${ZIMAKER_HOME:-/data}"
export ZIMAKER_RUNTIME="${ZIMAKER_RUNTIME:-/opt/zimaker-runtime}"
export ZIMAKER_OUTPUT="${ZIMAKER_OUTPUT:-/output}"
export HOME="$ZIMAKER_HOME"
