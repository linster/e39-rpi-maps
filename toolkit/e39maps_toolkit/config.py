"""Defaults and the settings remembered between runs
(~/.config/e39maps-toolkit/settings.json)."""

import json
import os
import subprocess
from dataclasses import asdict, dataclass, field
from pathlib import Path

from mapdata import schema

from .build import Region
from .extracts import default_cache_dir

REPO = Path(__file__).resolve().parents[2]

# Centred on Ottawa; the radius reaches London, ON (518 km) and Quebec City
# (379 km) with some margin. Canada only: just the Ontario and Quebec
# extracts (Geofabrik clips them at the border).
DEFAULT_REGION = Region(
    name="Ottawa +530 km (London ON - Quebec City), Canada",
    center_lat=45.4215,
    center_lon=-75.6972,
    radius_km=530,
    extracts=["north-america/canada/ontario", "north-america/canada/quebec"],
)

QEMU_TARGET = "localhost:2222"
MACHINES = ["qemuarm64", "raspberrypi4-64"]


def _settings_file():
    base = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config"))
    return base / "e39maps-toolkit" / "settings.json"


def _default_yocto():
    for candidate in (REPO.parent / "e39-rpi-yocto", Path.home() / "e39-rpi-yocto"):
        if (candidate / "scripts" / "build.sh").exists():
            return str(candidate)
    return ""


@dataclass
class Settings:
    region: Region = field(default_factory=lambda: Region(**asdict(DEFAULT_REGION)))
    categories: list = field(default_factory=lambda: list(schema.CATEGORIES))
    routing: bool = True               # build routing tiles (Valhalla) with the map
    cache_dir: str = field(default_factory=lambda: str(default_cache_dir()))
    output: str = str(REPO / "output" / "db" / "e39maps-osm.sqlite")
    target: str = QEMU_TARGET          # "localhost:2222" (QEMU) or host[:port]
    device_host: str = ""
    ssh_user: str = "root"
    ssh_password: str = ""             # root has none on debug-tweaks images
    yocto_dir: str = field(default_factory=_default_yocto)
    machine: str = MACHINES[0]

    @classmethod
    def load(cls):
        settings = cls()
        try:
            data = json.loads(_settings_file().read_text())
        except (OSError, ValueError):
            return settings
        for key, value in data.items():
            if key == "region" and isinstance(value, dict):
                settings.region = Region(**{**asdict(settings.region), **value})
            elif hasattr(settings, key):
                setattr(settings, key, value)
        return settings

    def save(self):
        path = _settings_file()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(asdict(self), indent=2) + "\n")


def routing_cache_dir(cache_dir):
    """Routing tiles cache, next to the extract cache."""
    return Path(cache_dir).parent / "routing"


def toolkit_version():
    try:
        return subprocess.run(["git", "-C", str(REPO), "describe", "--always", "--dirty"],
                              capture_output=True, text=True, timeout=10).stdout.strip()
    except OSError:
        return ""
