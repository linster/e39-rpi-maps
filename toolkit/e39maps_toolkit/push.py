"""Getting a map database onto a running device/QEMU, or into the image.

Live swap: copy the file next to the installed one over ssh, then run the
device's installer (e39maps-mapdb, from the Yocto layer), which checks it,
stops the services that use the map database, moves it into place and
starts them again. Only the read-only map database is touched; the state
database (users, the user's POIs) is left alone.

Update image: put the file where the Yocto build reads the map data and
start the image build, so the next image (QEMU, SD card) contains it.
"""

import json
import os
import re
import shutil
import subprocess
import time
import uuid
from pathlib import Path

from mapdata import schema

DATA_DIR = "/usr/share/e39rpi-maps/data"
INCOMING = f"{DATA_DIR}/.incoming-e39maps-osm.sqlite"
INCOMING_ROUTING = f"{DATA_DIR}/.incoming-{schema.ROUTING_DEVICE_NAME}"
INSTALLER = "/usr/libexec/e39maps-mapdb"
DB_NAME = "e39maps-osm.sqlite"


# Reads the device's Valhalla version without importing the module.
DEVICE_VALHALLA_VERSION = ("cat /usr/lib/python3*/site-packages/valhalla/__version__.py 2>/dev/null "
                           "| sed -n 's/^__version__ = \"\\(.*\\)\"/\\1/p' | grep . || echo none")


class PushError(Exception):
    pass


def parse_target(target, default_port=22):
    host, _, port = target.strip().partition(":")
    if not host:
        raise PushError("no target host")
    return host, int(port or default_port)


def _ssh_base(tool, host, port, user, password):
    port_flag = "-P" if tool == "scp" else "-p"
    cmd = [tool, port_flag, str(port), "-o", "StrictHostKeyChecking=no",
           "-o", "UserKnownHostsFile=/dev/null", "-o", "LogLevel=ERROR", "-o", "ConnectTimeout=10"]
    if shutil.which("sshpass"):
        # The image's root has an empty password (debug-tweaks); sshpass
        # answers the prompt. Without sshpass, ssh keys must work.
        cmd = ["sshpass", "-p", password] + cmd
    elif password:
        raise PushError("a password is set, but sshpass isn't installed")
    return cmd


def _run(cmd, log, timeout):
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    lines = []
    for line in proc.stdout:
        line = line.rstrip()
        lines.append(line)
        log(f"  {line}")
    try:
        code = proc.wait(timeout=timeout)
    except subprocess.TimeoutExpired:
        proc.kill()
        raise PushError(f"timed out: {' '.join(cmd[:4])}...")
    return code, lines


def paired_files(db_path, log=print):
    """(meta, routing tiles path or None), checked to belong together."""
    meta = schema.check(db_path)
    if schema.routing_info(meta) is None:
        return meta, None
    tiles = Path(db_path).with_name(meta["routing_file"])
    log(f"Checking the routing tiles {tiles.name}...")
    schema.check_routing(meta, tiles)
    return meta, tiles


def live_swap(db_path, target, user="root", password="", log=print):
    db_path = Path(db_path)
    meta, tiles = paired_files(db_path, log)
    host, port = parse_target(target)
    ssh = _ssh_base("ssh", host, port, user, password) + [f"{user}@{host}"]
    log(f"Map database {db_path.name}: built {meta['built_at']}, {meta['poi_count']} POIs, "
        f"{db_path.stat().st_size / 1e6:.1f} MB")

    code, lines = _run(ssh + [f"test -x {INSTALLER} && echo ok || echo missing"], log, 30)
    if code != 0:
        raise PushError(f"can't ssh to {user}@{host}:{port} (is it running the e39-rpi image?)")
    if lines[-1:] != ["ok"]:
        raise PushError(f"{host} has no {INSTALLER}: its image is older than the map toolkit; "
                        "update the image first")
    if tiles:
        # Tiles only load with the Valhalla that built them: ask the device.
        code, lines = _run(ssh + [DEVICE_VALHALLA_VERSION], log, 30)
        device = (lines[-1] if lines else "").strip()
        built = meta["valhalla_version"].split("-")[0]
        if not device or device == "none":
            raise PushError(f"{host} has no Valhalla (python3-valhalla): update its image first")
        if device.split("-")[0] != built:
            raise PushError(f"{host} has Valhalla {device}, but these routing tiles were built with {built}: "
                            "they wouldn't load. Update the image, or build with the matching pyvalhalla.")

    copies = [(db_path, INCOMING)] + ([(tiles, INCOMING_ROUTING)] if tiles else [])
    for src, dest in copies:
        log(f"Copying {src.name} ({src.stat().st_size / 1e6:.0f} MB) to {host}:{dest}...")
        started = time.monotonic()
        scp = _ssh_base("scp", host, port, user, password) + ["-q", str(src), f"{user}@{host}:{dest}"]
        code, _ = _run(scp, log, 3600)
        if code != 0:
            raise PushError(f"copying {src.name} failed")
        log(f"  copied in {time.monotonic() - started:.0f} s")

    log("Installing (check, stop services, swap, start)...")
    routing_arg = f" --routing {INCOMING_ROUTING}" if tiles else ""
    code, _ = _run(ssh + [f"{INSTALLER} install --move{routing_arg} {INCOMING}"], log, 900)
    if code != 0:
        raise PushError(f"the installer on {host} failed (exit {code}); the old map database stays")
    log(f"Done: {host} uses the map database built {meta['built_at']}.")


def yocto_data_dir(yocto_dir):
    """Where the image build reads the map data: E39_MAPS_DATA_DIR from the
    build's local.conf, else the e39-rpi-maps submodule's output/db."""
    yocto_dir = Path(yocto_dir)
    local_conf = yocto_dir / "build_dir" / "build" / "conf" / "local.conf"
    if local_conf.exists():
        for line in local_conf.read_text().splitlines():
            m = re.match(r'\s*E39_MAPS_DATA_DIR\s*\??\??=\s*"([^"]+)"', line)
            if m:
                return Path(m.group(1)).expanduser()
    return yocto_dir / "e39-rpi-maps" / "output" / "db"


def update_image(db_path, yocto_dir, machine, build=True, log=print):
    db_path = Path(db_path)
    meta, tiles = paired_files(db_path, log)
    yocto_dir = Path(yocto_dir)
    if not (yocto_dir / "scripts" / "build.sh").exists():
        raise PushError(f"{yocto_dir} isn't an e39-rpi-yocto checkout (no scripts/build.sh)")
    data_dir = yocto_data_dir(yocto_dir)
    data_dir.mkdir(parents=True, exist_ok=True)
    # Routing tiles first (under the name the database records), then the
    # database; tiles of an older map are removed.
    for old in data_dir.glob("*.routing.tar"):
        if tiles is None or old.name != tiles.name:
            old.unlink()
    for src, name in ([(tiles, tiles.name)] if tiles else []) + [(db_path, DB_NAME)]:
        tmp = data_dir / f".{name}.copying"
        shutil.copyfile(src, tmp)
        tmp.replace(data_dir / name)
    log(f"Installed into the image build: {data_dir} (built {meta['built_at']}, {meta['poi_count']} POIs, "
        + (f"routing tiles {tiles.name})" if tiles else "no routing tiles)"))
    if not build:
        return None
    log(f"Starting the image build for {machine} (progress window)...")
    host_builds = os.environ.get("E39MAPS_HOST_BUILDS")
    if host_builds:
        text, ok = _request_host_build(Path(host_builds), yocto_dir, machine)
    else:
        out = subprocess.run([str(yocto_dir / "scripts" / "with-progress.sh"), "scripts/build.sh", machine],
                             cwd=yocto_dir, capture_output=True, text=True, timeout=60)
        text, ok = out.stdout + out.stderr, out.returncode == 0
    for line in text.splitlines():
        log(f"  {line}")
    if not ok:
        raise PushError("couldn't start the build")
    m = re.search(r"log: (\S+)", text)
    return m.group(1) if m else None


def _request_host_build(requests, yocto_dir, machine, timeout=120):
    """In the toolkit container (map-toolkit-container.sh): bitbake runs on
    the host, so ask the launcher's helper to start the build there."""
    rid = uuid.uuid4().hex
    tmp = requests / f"{rid}.tmp"
    tmp.write_text(json.dumps({"yocto_dir": str(yocto_dir), "machine": machine}))
    tmp.rename(requests / f"{rid}.request")
    reply = requests / f"{rid}.reply"
    deadline = time.monotonic() + timeout
    while not reply.exists():
        if time.monotonic() > deadline:
            raise PushError("the host didn't start the build (is map-toolkit-container.sh's helper running?)")
        time.sleep(0.5)
    text = reply.read_text()
    reply.unlink()
    return text, text.rstrip().endswith("exit=0")


USB_FOLDER = "RPIMAPS"
FAT32_MAX = 4 * 1024 ** 3 - 1


def copy_to_usb(db_path, stick_dir, log=print, progress=None):
    """Put the map pair on a USB stick: <stick>/RPIMAPS/, under the names the
    database records, so the device's USB import (e39maps-usb-import@)
    installs it when the stick is plugged in. Returns the folder."""
    db_path = Path(db_path)
    meta, tiles = paired_files(db_path, log)
    stick = Path(stick_dir)
    if not stick.is_dir():
        raise PushError(f"{stick} isn't a folder (the stick's mount point, e.g. /media/$USER/STICK)")
    folder = next((d for d in stick.iterdir() if d.is_dir() and d.name.upper() == USB_FOLDER),
                  stick / USB_FOLDER)
    folder.mkdir(exist_ok=True)
    files = [db_path] + ([tiles] if tiles else [])
    if (folder / db_path.name).exists():
        raise PushError(f"{folder} has a {db_path.name} already (not imported yet?): remove it first")
    need = sum(f.stat().st_size for f in files)
    free = shutil.disk_usage(folder).free
    if free < need:
        raise PushError(f"not enough space on the stick: {need / 1e6:.0f} MB needed, {free / 1e6:.0f} MB free")
    big = [f for f in files if f.stat().st_size > FAT32_MAX]
    if big:
        log(f"Note: {big[0].name} is over 4 GB: the stick must be exFAT or ext4, not FAT32")
    for f in files:
        log(f"Copying {f.name} ({f.stat().st_size / 1e6:.0f} MB) to {folder}...")
        tmp = folder / f".{f.name}.copying"
        with open(f, "rb") as src, open(tmp, "wb") as out:
            done, size = 0, f.stat().st_size
            while chunk := src.read(8 << 20):
                out.write(chunk)
                done += len(chunk)
                if progress:
                    progress(done / size if size else 1, f"{f.name}: {done / 1e6:.0f} / {size / 1e6:.0f} MB")
            out.flush()
            os.fsync(out.fileno())
        tmp.rename(folder / f.name)
    os.sync()
    log(f"Done: {folder} has the map built {meta['built_at']}. Eject the stick, plug it into the car; "
        f"once imported, the files are renamed .copied-<date> on the stick.")
    return folder
