# The map toolkit's runtime, pinned: Python 3.12, Tk, ssh, pyosmium and
# pyvalhalla at the versions in requirements.txt (Valhalla frozen to the
# backend's and the image's version). The toolkit's code isn't copied in:
# map-toolkit-container.sh mounts the repository, so edits need no rebuild;
# only a change to requirements.txt or this file does (the image tag is a
# hash of both).
#
# Works with podman and docker; built and run by toolkit/map-toolkit-container.sh.

# Debian bookworm: glibc 2.36, new enough for pyvalhalla's manylinux_2_28
# wheel; Python 3.12 for its cp312 wheel.
FROM docker.io/library/python:3.12-slim-bookworm

# tk: the Tcl/Tk libraries for tkinter (the window); fonts for it;
# openssh-client + sshpass: live swap over ssh/scp (root has an empty
# password on development images); git: the toolkit records the maps repo's
# version in the map database.
RUN apt-get -o Acquire::Retries=5 update \
 && apt-get install -y --no-install-recommends tk fonts-dejavu-core openssh-client sshpass git \
 && rm -rf /var/lib/apt/lists/*

COPY requirements.txt /opt/e39maps-toolkit/requirements.txt
RUN pip install --no-cache-dir -r /opt/e39maps-toolkit/requirements.txt \
 && python -c "import valhalla, osmium, tkinter; print('pyvalhalla', valhalla.__version__)"

# The mounted repository is owned by the host user, not by whoever runs in
# the container: let git read it anyway.
RUN git config --system --add safe.directory '*'

ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1
ENTRYPOINT ["python", "-m", "e39maps_toolkit"]
