"""Admin form for POIs: colours as hex with a picker, the HMI's icons as suggestions."""

import re

from django import forms
from django.utils.html import format_html, format_html_join

from .models import Poi

# The colours and icons the HMI's POI editor offers (e39-rpi,
# CreateOrEditPoiScreen.IconSelectorMenu). Compose's Color.Red etc. as
# 0xAARRGGBB; icons by their Compose resource name.
HMI_COLORS = {
    "White": 0xFFFFFFFF,
    "Red": 0xFFFF0000,
    "Green": 0xFF00FF00,
    "Cyan": 0xFF00FFFF,
    "Magenta": 0xFFFF00FF,
}
HMI_ICONS = ["poi_briefcase", "poi_home", "poi_shopping_cart", "poi_shopping_bag", "notification_map_pin"]


def argb_to_hex(argb):
    return "" if argb is None else f"#{argb:08X}"


def argb_to_css(argb):
    """#RRGGBBAA for CSS (which puts alpha last)."""
    return f"#{argb & 0xFFFFFF:06X}{argb >> 24:02X}"


class ArgbColorWidget(forms.TextInput):
    """A hex text field, a colour picker (RGB) kept in sync with it, and the
    HMI's palette as suggestions."""

    def __init__(self, attrs=None):
        super().__init__({"size": 11, "placeholder": "#AARRGGBB", **(attrs or {})})

    def format_value(self, value):
        if isinstance(value, int):
            return argb_to_hex(value)
        return super().format_value(value)

    def render(self, name, value, attrs=None, renderer=None):
        attrs = {**(attrs or {}), "list": f"{name}_palette"}
        text = super().render(name, value, attrs, renderer)
        hex_value = self.format_value(value) or ""
        rgb = "#" + hex_value[-6:] if re.fullmatch(r"#[0-9A-Fa-f]{6}([0-9A-Fa-f]{2})?", hex_value) else "#ffffff"
        field_id = attrs.get("id", name)
        palette = format_html_join(
            "", '<option value="{}">{}</option>', ((argb_to_hex(v), k) for k, v in HMI_COLORS.items()))
        # Picking a colour writes #FFRRGGBB (opaque, like the HMI's palette);
        # typing a valid hex moves the picker.
        return format_html(
            '{} <input type="color" value="{}" title="Pick a colour" '
            'oninput="document.getElementById(\'{}\').value=\'#FF\'+this.value.slice(1).toUpperCase()">'
            '<datalist id="{}_palette">{}</datalist>'
            '<script>document.getElementById("{}").addEventListener("input", function (e) {{'
            ' var m = /^#(?:[0-9a-f]{{2}})?([0-9a-f]{{6}})$/i.exec(e.target.value.trim());'
            ' if (m) e.target.nextElementSibling.value = "#" + m[1]; }});</script>',
            text, rgb, field_id, name, palette, field_id)


class ArgbColorField(forms.CharField):
    """0xAARRGGBB as "#AARRGGBB", or "#RRGGBB" (opaque); empty = None."""

    widget = ArgbColorWidget

    def to_python(self, value):
        value = (value or "").strip()
        if not value:
            return None
        named = {k.lower(): v for k, v in HMI_COLORS.items()}.get(value.lower())
        if named is not None:
            return named
        m = re.fullmatch(r"#?([0-9A-Fa-f]{6}|[0-9A-Fa-f]{8})", value)
        if not m:
            raise forms.ValidationError("Use #RRGGBB or #AARRGGBB (or White, Red, Green, Cyan, Magenta).")
        digits = m.group(1)
        return int(digits, 16) | (0xFF000000 if len(digits) == 6 else 0)


class IconFileWidget(forms.TextInput):
    def render(self, name, value, attrs=None, renderer=None):
        attrs = {**(attrs or {}), "list": f"{name}_icons"}
        options = format_html_join("", '<option value="{}"></option>', ((i,) for i in HMI_ICONS))
        return format_html('{}<datalist id="{}_icons">{}</datalist>',
                           super().render(name, value, attrs, renderer), name, options)


class PoiAdminForm(forms.ModelForm):
    icon_argb = ArgbColorField(
        required=False, label="Circle colour",
        help_text="For a coloured circle: #RRGGBB or #AARRGGBB (the HMI offers White, Red, Green, Cyan, Magenta).")
    icon_tint = ArgbColorField(
        required=False, label="Icon tint",
        help_text="For a bundled icon; empty = white.")

    class Meta:
        model = Poi
        fields = "__all__"
        widgets = {"icon_file": IconFileWidget(attrs={"size": 30})}
        labels = {"icon_file": "Icon name"}
        help_texts = {
            "icon_file": "For a bundled icon: the HMI's resource name ("
                         + ", ".join(HMI_ICONS) + "). An HMI without that icon shows none.",
        }

    def clean(self):
        cleaned = super().clean()
        # Keep only what the chosen icon kind uses, so nothing stale is stored
        # or sent to the HMI.
        kind = cleaned.get("icon_kind")
        if kind != Poi.IconKind.COLORED_CIRCLE:
            cleaned["icon_argb"] = None
        if kind != Poi.IconKind.BUNDLED:
            cleaned["icon_file"], cleaned["icon_tint"] = "", None
        return cleaned
