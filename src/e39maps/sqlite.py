"""SQLite connection setup.

Django 5.0 (the version in the Yocto image's meta-python) has no
OPTIONS["init_command"] for SQLite (added in 5.1), so the PRAGMAs are set
when a connection is opened instead (connected in core.apps).

Only the writable state database ("default") gets them: the map database is
opened read-only and immutable, where journal_mode=WAL would fail and nothing
is ever written.
"""

STATE_DB_PRAGMAS = (
    # Readers don't block on the writer (gRPC stream readers vs. admin writes).
    "PRAGMA journal_mode=WAL",
    "PRAGMA synchronous=NORMAL",
    "PRAGMA foreign_keys=ON",
)


def configure_sqlite_connection(sender, connection, **kwargs):
    if connection.vendor != "sqlite" or connection.alias != "default":
        return
    with connection.cursor() as cursor:
        for pragma in STATE_DB_PRAGMAS:
            cursor.execute(pragma)
