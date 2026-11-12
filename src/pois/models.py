"""The user's points of interest, in the writable state database.

Created/edited/deleted by the HMI over gRPC (e39maps_api.v1.PoiService) and
in the Django admin. Being in the state database, they survive map updates.
The fields mirror the HMI's Poi (name, location, icon, isVisible).
"""

from django.core.exceptions import ValidationError
from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models


class Poi(models.Model):
    class IconKind(models.TextChoices):
        NONE = "none", "No icon"
        COLORED_CIRCLE = "colored_circle", "Coloured circle"
        BUNDLED = "bundled", "Bundled icon"

    name = models.CharField(max_length=200)
    latitude = models.FloatField(validators=[MinValueValidator(-90), MaxValueValidator(90)])
    longitude = models.FloatField(validators=[MinValueValidator(-180), MaxValueValidator(180)])
    icon_kind = models.CharField(max_length=20, choices=IconKind.choices, default=IconKind.NONE)
    # 0xAARRGGBB (Compose Color.toArgb()), for COLORED_CIRCLE.
    icon_argb = models.PositiveBigIntegerField(
        null=True, blank=True, validators=[MaxValueValidator(0xFFFFFFFF)],
        help_text="0xAARRGGBB, for a coloured circle")
    # A file bundled with the HMI, for BUNDLED.
    icon_file = models.CharField(max_length=255, blank=True)
    visible = models.BooleanField(default=True, help_text="Shown on the HMI's map")
    created = models.DateTimeField(auto_now_add=True)
    updated = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["name", "id"]
        indexes = [models.Index(fields=["latitude", "longitude"], name="poi_location")]
        verbose_name = "POI"

    def __str__(self):
        return self.name

    def clean(self):
        if self.icon_kind == self.IconKind.COLORED_CIRCLE and self.icon_argb is None:
            raise ValidationError({"icon_argb": "A coloured circle needs a colour."})
        if self.icon_kind == self.IconKind.BUNDLED and not self.icon_file:
            raise ValidationError({"icon_file": "A bundled icon needs a file name."})
