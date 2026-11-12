from django.contrib import admin

from .models import Poi


@admin.register(Poi)
class PoiAdmin(admin.ModelAdmin):
    list_display = ("name", "latitude", "longitude", "icon", "visible", "updated")
    list_filter = ("visible", "icon_kind")
    search_fields = ("name",)
    readonly_fields = ("created", "updated")
    fieldsets = (
        (None, {"fields": ("name", "latitude", "longitude", "visible")}),
        ("Icon", {"fields": ("icon_kind", "icon_argb", "icon_file")}),
        ("Timestamps", {"fields": ("created", "updated")}),
    )

    @admin.display(description="Icon")
    def icon(self, poi):
        if poi.icon_kind == Poi.IconKind.COLORED_CIRCLE and poi.icon_argb is not None:
            return f"circle #{poi.icon_argb:08X}"
        if poi.icon_kind == Poi.IconKind.BUNDLED:
            return f"file {poi.icon_file}"
        return "-"
