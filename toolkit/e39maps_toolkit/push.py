"""Getting a map database onto a running device/QEMU, or into the image.

Live swap: copy the file next to the installed one over ssh, then run the
device's installer (e39maps-mapdb, from the Yocto layer), which checks it,
stops the services that use the map database, moves it into place and
starts them again. Only the read-only map database is touched; the state
database (users, the user's POIs) is left alone.

Update image: put the file where the Yocto build reads the map data and
start the image build, so the next image (QEMU, SD card) contains it.
"""

import re
import shutil
import subprocess
import time
from pathlib import Path

from mapdata import schema

DATA_DIR = "/usr/share/e39rpi-maps/data"
INCOMING = f"{DATA_DIR}/.incoming-e39maps-osm.sqlite"
INCOMING_ROUTING = f"{DATA_DIR}/.incoming-{schema.ROUTING_DEVICE_NAME}"
INSTALLER = "/usr/libexec/e39maps-mapdb"
DB_NAME = "e39maps-osm.sqlite"


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
    out = subprocess.run([str(yocto_dir / "scripts" / "with-progress.sh"), "scripts/build.sh", machine],
                         cwd=yocto_dir, capture_output=True, text=True, timeout=60)
    for line in (out.stdout + out.stderr).splitlines():
        log(f"  {line}")
    if out.returncode != 0:
        raise PushError("couldn't start the build")
    m = re.search(r"log: (\S+)", out.stdout)
    return m.group(1) if m else None
