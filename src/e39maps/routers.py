"""Two databases: read-only map data, writable state.

- "mapdata": the map database built off-device by the map toolkit and
  shipped (or pushed) as a file. Opened read-only; replaced as a whole on a
  map update. Models of the apps in MAP_DATA_APPS live here; they are
  unmanaged (the toolkit owns the schema), and nothing is ever migrated into
  or written to it.
- "default": the writable state database (users, admin, sessions, user-made
  POIs). Survives map updates.

Queries can't join across databases, so relations between the two aren't
allowed either; refer to map objects by their id instead.
"""

MAP_DATA_APPS = {"mapdata"}
MAP_DB = "mapdata"
STATE_DB = "default"


class MapDataRouter:
    def db_for_read(self, model, **hints):
        if model._meta.app_label in MAP_DATA_APPS:
            return MAP_DB
        return None

    def db_for_write(self, model, **hints):
        if model._meta.app_label in MAP_DATA_APPS:
            # Still the map database: SQLite then refuses the write
            # ("attempt to write a readonly database") instead of Django
            # silently writing map rows into the state database.
            return MAP_DB
        return None

    def allow_relation(self, obj1, obj2, **hints):
        in_map = {obj._meta.app_label in MAP_DATA_APPS for obj in (obj1, obj2)}
        return len(in_map) == 1

    def allow_migrate(self, db, app_label, model_name=None, **hints):
        if db == MAP_DB:
            return False
        if app_label in MAP_DATA_APPS:
            return False
        return None
