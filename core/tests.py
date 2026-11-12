import django
from django.test import TestCase
from django.urls import reverse


class IndexViewTests(TestCase):
    def test_index_renders_hello_world(self):
        response = self.client.get(reverse("core:index"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Hello from Django")
        self.assertContains(response, django.get_version())

    def test_healthz(self):
        response = self.client.get(reverse("core:healthz"))

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"status": "ok"})
