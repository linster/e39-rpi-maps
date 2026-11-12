from django.contrib import admin, messages
from django.utils.html import format_html
from google.protobuf.json_format import MessageToJson

from .forms import PoiAdminForm, argb_to_css, argb_to_hex
from .models import Poi


@admin.register(Poi)
class PoiAdmin(admin.ModelAdmin):
    """The user's POIs, as the HMI sees them: for checking what the HMI wrote
    over gRPC, and for setting up POIs to test the HMI with."""

    form = PoiAdminForm
    list_display = ("id", "name", "icon", "location", "visible", "updated")
    list_display_links = ("name",)
    list_editable = ("visible",)
    list_filter = ("visible", "icon_kind")
    search_fields = ("name",)
    actions = ("show_on_map", "hide_from_map")
    readonly_fields = ("id", "map_link", "created", "updated", "as_sent_to_hmi")
    fieldsets = (
        (None, {"fields": ("name", "visible")}),
        ("Location", {"fields": ("latitude", "longitude", "map_link"),
                      "description": "WGS 84 degrees, e.g. 45.4236, -75.7009."}),
        ("Icon", {"fields": ("icon_kind", "icon_argb", "icon_file", "icon_tint")}),
        ("Details", {"fields": ("id", "created", "updated")}),
        ("As the HMI gets it", {"fields": ("as_sent_to_hmi",), "classes": ("collapse",)}),
    )

    class Media:
        js = ("pois/poi_admin.js",)

    @staticmethod
    def _swatch(argb, round_=True):
        radius = "50%" if round_ else "3px"
        return format_html(
            '<span style="display:inline-block;width:12px;height:12px;vertical-align:middle;'
            'border:1px solid #888;border-radius:{};background:{}"></span>', radius, argb_to_css(argb))

    @admin.display(description="Icon", ordering="icon_kind")
    def icon(self, poi):
        if poi.icon_kind == Poi.IconKind.COLORED_CIRCLE and poi.icon_argb is not None:
            return format_html("{} {}", self._swatch(poi.icon_argb), argb_to_hex(poi.icon_argb))
        if poi.icon_kind == Poi.IconKind.BUNDLED:
            tint = poi.icon_tint if poi.icon_tint is not None else 0xFFFFFFFF
            return format_html("{} {}", self._swatch(tint, round_=False), poi.icon_file)
        return "-"

    @admin.display(description="Location")
    def location(self, poi):
        lat, lon = f"{poi.latitude:.6f}", f"{poi.longitude:.6f}"
        return format_html(
            '<a href="https://www.openstreetmap.org/?mlat={0}&amp;mlon={1}#map=17/{0}/{1}" '
            'target="_blank" rel="noopener">{0}, {1}</a>', lat, lon)

    @admin.display(description="Map")
    def map_link(self, poi):
        if poi.pk is None:
            return "-"
        return self.location(poi)

    @admin.display(description="gRPC Poi message (JSON)")
    def as_sent_to_hmi(self, poi):
        if poi.pk is None:
            return "-"
        from api.convert import poi_to_pb

        return format_html("<pre style='margin:0'>{}</pre>",
                           MessageToJson(poi_to_pb(poi), preserving_proto_field_name=True))

    def _set_visible(self, request, queryset, visible):
        # update() bypasses save(): set updated too, so the HMI-facing
        # timestamp reflects the change.
        from django.utils import timezone

        count = queryset.update(visible=visible, updated=timezone.now())
        self.message_user(request, f"{count} POI(s) {'shown on' if visible else 'hidden from'} the map.",
                          messages.SUCCESS)

    @admin.action(description="Show selected POIs on the map")
    def show_on_map(self, request, queryset):
        self._set_visible(request, queryset, True)

    @admin.action(description="Hide selected POIs from the map")
    def hide_from_map(self, request, queryset):
        self._set_visible(request, queryset, False)
