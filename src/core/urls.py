from django.urls import path

from . import views

app_name = "core"

urlpatterns = [
    path("", views.index, name="index"),
    path("healthz", views.healthz, name="healthz"),
    path("timezone", views.timezone, name="timezone"),
    path("geocode", views.geocode, name="geocode"),
]
