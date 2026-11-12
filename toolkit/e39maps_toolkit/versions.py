"""Is everyone on the same Valhalla? Routing tiles only load with the
Valhalla version that built them, so these must agree:

- the toolkit's pyvalhalla (builds the tiles),
- the backend's frozen version (mapdata.schema.VALHALLA_VERSION, also the
  pip pin in src/requirements/base.txt),
- the image's valhalla recipe (E39_VALHALLA_VERSION in e39-rpi-yocto's
  meta-e39rpi-maps conf/layer.conf).
"""

import re
from pathlib import Path

from mapdata import schema


def toolkit_version():
    try:
        import valhalla
    except ImportError:
        return None
    return getattr(valhalla, "__version__", "").split("-")[0] or None


def yocto_version(yocto_dir):
    """E39_VALHALLA_VERSION from the yocto checkout, else the recipe file's
    version (valhalla_<version>.bb); None if neither is there."""
    if not yocto_dir:
        return None
    layer = Path(yocto_dir) / "layers" / "meta-e39rpi-maps"
    conf = layer / "conf" / "layer.conf"
    if conf.exists():
        m = re.search(r'^E39_VALHALLA_VERSION\s*\??=\s*"([^"]+)"', conf.read_text(), re.M)
        if m:
            return m.group(1)
    recipes = sorted((layer / "recipes-maps" / "valhalla").glob("valhalla_*.bb"))
    if recipes:
        return recipes[-1].stem.split("_", 1)[1]
    return None


def problems(yocto_dir):
    """Human-readable mismatches (empty if all agree)."""
    frozen = schema.VALHALLA_VERSION
    found = []
    tk = toolkit_version()
    if tk is None:
        found.append("pyvalhalla isn't installed: routing tiles can't be built "
                     "(pip install -r toolkit/requirements.txt)")
    elif tk != frozen:
        found.append(f"the toolkit's pyvalhalla is {tk}, but the backend is frozen to {frozen}: "
                     "tiles built now won't load on the device")
    yv = yocto_version(yocto_dir)
    if yocto_dir and yv is None:
        found.append(f"can't find the valhalla recipe's version in {yocto_dir}")
    elif yv is not None and yv != frozen:
        found.append(f"the image's valhalla recipe is {yv}, but the backend is frozen to {frozen}: "
                     "routing tiles won't load on images built from it")
    return found


def summary(yocto_dir):
    return (f"Valhalla: toolkit {toolkit_version() or 'missing'}, backend {schema.VALHALLA_VERSION}, "
            f"image recipe {yocto_version(yocto_dir) or 'unknown'}")
