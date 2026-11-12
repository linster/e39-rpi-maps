"""Models of the read-only map database (DATABASES["mapdata"]).

The map toolkit builds that database off-device and owns its schema, so
these models are unmanaged: Django never creates, migrates or writes these
tables (see e39maps.routers).
"""

from django.db import models


class MapMeta(models.Model):
    """Key/value facts about the map database, written by the toolkit:
    schema_version, built_at (ISO 8601, UTC), region, source, ..."""

    key = models.TextField(primary_key=True)
    value = models.TextField()

    class Meta:
        managed = False
        db_table = "e39maps_meta"
        verbose_name = "map database fact"

    def __str__(self):
        return f"{self.key} = {self.value}"

    @classmethod
    def as_dict(cls):
        return dict(cls.objects.values_list("key", "value"))
