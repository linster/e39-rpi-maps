import json
import re

from django.contrib.auth.models import User
from django.test import TestCase
from django.urls import reverse

from .forms import PoiAdminForm
from .models import Poi


class PoiAdminFormTests(TestCase):

    def form(self, **data):
        return PoiAdminForm(data={"name": "Home", "latitude": "45.42", "longitude": "-75.69",
                                  "visible": "on", "icon_kind": "none", **data})

    def test_colours_as_hex_or_names(self):
        for value, expected in (("#FF7A1A", 0xFFFF7A1A), ("80FF7A1A", 0x80FF7A1A),
                                ("#ff00ff00", 0xFF00FF00), ("magenta", 0xFFFF00FF)):
            form = self.form(icon_kind="colored_circle", icon_argb=value)
            self.assertTrue(form.is_valid(), form.errors)
            self.assertEqual(form.save().icon_argb, expected, value)

    def test_bad_colour_and_missing_colour(self):
        self.assertIn("icon_argb", self.form(icon_kind="colored_circle", icon_argb="#12345").errors)
        self.assertIn("icon_argb", self.form(icon_kind="colored_circle", icon_argb="").errors)

    def test_bundled_icon_keeps_only_its_fields(self):
        form = self.form(icon_kind="bundled", icon_file="poi_home", icon_tint="#00FF00", icon_argb="#FF0000")
        self.assertTrue(form.is_valid(), form.errors)
        poi = form.save()
        self.assertEqual((poi.icon_file, poi.icon_tint, poi.icon_argb), ("poi_home", 0xFF00FF00, None))
        self.assertIn("icon_file", self.form(icon_kind="bundled").errors)

    def test_switching_to_no_icon_clears_icon_fields(self):
        poi = Poi.objects.create(name="x", latitude=1, longitude=2, icon_kind="bundled",
                                 icon_file="poi_home", icon_tint=0xFF00FF00)
        form = PoiAdminForm(instance=poi, data={"name": "x", "latitude": "1", "longitude": "2",
                                                "icon_kind": "none", "icon_file": "poi_home",
                                                "icon_tint": "#00FF00"})
        self.assertTrue(form.is_valid(), form.errors)
        poi = form.save()
        self.assertEqual((poi.icon_file, poi.icon_tint), ("", None))


class PoiAdminViewTests(TestCase):

    def setUp(self):
        self.client.force_login(User.objects.create_superuser("tester", password="x"))
        self.circle = Poi.objects.create(name="Parliament", latitude=45.4236, longitude=-75.7009,
                                         icon_kind="colored_circle", icon_argb=0xFFFF7A1A, visible=False)
        self.bundled = Poi.objects.create(name="Shop", latitude=45.4255, longitude=-75.6920,
                                          icon_kind="bundled", icon_file="poi_shopping_cart")

    def test_changelist_shows_swatches_and_map_links(self):
        html = self.client.get(reverse("admin:pois_poi_changelist")).content.decode()
        self.assertIn("#FFFF7A1A", html)
        self.assertIn("background:#FF7A1AFF", html)       # CSS order: alpha last
        self.assertIn("poi_shopping_cart", html)
        self.assertIn("background:#FFFFFFFF", html)       # untinted icon = white
        self.assertIn("openstreetmap.org/?mlat=45.423600&amp;mlon=-75.700900", html)

    def test_show_and_hide_actions(self):
        url = reverse("admin:pois_poi_changelist")
        self.client.post(url, {"action": "show_on_map", "_selected_action": [self.circle.pk, self.bundled.pk]})
        self.assertEqual(Poi.objects.filter(visible=True).count(), 2)
        self.client.post(url, {"action": "hide_from_map", "_selected_action": [self.circle.pk]})
        self.assertFalse(Poi.objects.get(pk=self.circle.pk).visible)

    def test_change_form_shows_the_grpc_message(self):
        html = self.client.get(reverse("admin:pois_poi_change", args=[self.circle.pk])).content.decode()
        pre = re.search(r"<pre style='margin:0'>(.*?)</pre>", html, re.S).group(1)
        message = json.loads(pre.replace("&quot;", '"'))
        self.assertEqual(message["name"], "Parliament")
        self.assertEqual(message["icon"], {"colored_circle": {"argb": 0xFFFF7A1A}})
        self.assertEqual(message["source"], "POI_SOURCE_USER")
        self.assertIn('type="color" value="#FF7A1A"', html)
        self.assertIn('<datalist id="icon_file_icons">', html)

    def test_add_form_renders_and_saves(self):
        url = reverse("admin:pois_poi_add")
        self.assertEqual(self.client.get(url).status_code, 200)
        response = self.client.post(url, {"name": "New", "latitude": "1.5", "longitude": "2.5", "visible": "on",
                                          "icon_kind": "colored_circle", "icon_argb": "Cyan",
                                          "icon_file": "", "icon_tint": ""})
        self.assertEqual(response.status_code, 302, response.content.decode()[:2000])
        self.assertEqual(Poi.objects.get(name="New").icon_argb, 0xFF00FFFF)
