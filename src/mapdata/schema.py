"""The map database's file format: the one definition shared by
- the map toolkit (toolkit/), which builds the database,
- the backend (mapdata.models), which reads it, and
- the device's installer (e39maps-mapdb in the Yocto layer), which checks a
  pushed or USB-copied file before swapping it in.

Plain Python, no Django: the toolkit and the installer import it without
setting Django up.

Identification: SQLite's application_id header field holds APPLICATION_ID
and user_version holds SCHEMA_VERSION, so a file can be recognised (and
its format version read) from its first 100 bytes, without trusting a
table in it. Bump SCHEMA_VERSION on incompatible changes; the backend
reads only the version(s) it knows.
"""

import sqlite3

APPLICATION_ID = 0x45333950  # "E39P"
SCHEMA_VERSION = 1

# What a POI from the map data is. The keys are stored in map_poi.category
# and are the API's PoiCategory names in lower case (e39maps.v1).
CATEGORIES = {
    "fuel": "Fuel station",
    "restaurant": "Restaurant",
    "ski_area": "Ski hill",
    "museum": "Museum",
    "airport": "Airport",
    "park": "Park",
    "trailhead": "Trailhead",
    "car_parts": "Car parts store",
    "junkyard": "Junkyard",
}

# Facts in e39maps_meta. Required ones must be present for a valid file.
REQUIRED_META = ("schema_version", "built_at", "poi_count")

SCHEMA = """
CREATE TABLE e39maps_meta (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

-- POIs from OpenStreetMap. id = osm_id * 4 + {node: 1, way: 2, relation: 3},
-- so the same OSM object keeps its id across map updates.
CREATE TABLE map_poi (
    id        INTEGER PRIMARY KEY,
    osm_type  TEXT    NOT NULL CHECK (osm_type IN ('node', 'way', 'relation')),
    osm_id    INTEGER NOT NULL,
    category  TEXT    NOT NULL,
    name      TEXT    NOT NULL,
    latitude  REAL    NOT NULL,
    longitude REAL    NOT NULL,
    -- JSON object of selected OSM tags: kind (the matching tag, e.g.
    -- "amenity=fast_food"), brand, cuisine, phone, website, opening_hours,
    -- iata, addr:*.
    details   TEXT    NOT NULL DEFAULT '{}'
);
CREATE INDEX map_poi_location ON map_poi (latitude, longitude);
CREATE INDEX map_poi_category ON map_poi (category);
"""

OSM_TYPE_CODES = {"node": 1, "way": 2, "relation": 3}


def poi_id(osm_type, osm_id):
    return osm_id * 4 + OSM_TYPE_CODES[osm_type]


def create(conn):
    """Create the tables in an empty database and stamp the header."""
    conn.executescript(SCHEMA)
    conn.execute(f"PRAGMA application_id = {APPLICATION_ID}")
    conn.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")


class InvalidMapDatabase(Exception):
    pass


def check(path, quick_check=True):
    """Return the e39maps_meta facts of the map database at path, or raise
    InvalidMapDatabase saying what's wrong. Opens the file read-only."""
    try:
        conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    except sqlite3.Error as exc:
        raise InvalidMapDatabase(f"can't open {path}: {exc}") from exc
    try:
        try:
            app_id = conn.execute("PRAGMA application_id").fetchone()[0]
        except sqlite3.DatabaseError as exc:
            raise InvalidMapDatabase(f"{path} isn't an SQLite database ({exc})") from exc
        if app_id != APPLICATION_ID:
            raise InvalidMapDatabase(f"{path} isn't an e39 map database (application_id {app_id:#x})")
        version = conn.execute("PRAGMA user_version").fetchone()[0]
        if version != SCHEMA_VERSION:
            raise InvalidMapDatabase(
                f"{path} has map database format {version}; this software reads {SCHEMA_VERSION}")
        if quick_check:
            result = conn.execute("PRAGMA quick_check").fetchone()[0]
            if result != "ok":
                raise InvalidMapDatabase(f"{path} is damaged: {result}")
        meta = dict(conn.execute("SELECT key, value FROM e39maps_meta"))
        missing = [k for k in REQUIRED_META if k not in meta]
        if missing:
            raise InvalidMapDatabase(f"{path} lacks {', '.join(missing)} in e39maps_meta")
        return meta
    except sqlite3.DatabaseError as exc:
        raise InvalidMapDatabase(f"{path}: {exc}") from exc
    finally:
        conn.close()
