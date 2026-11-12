from django.urls import path

from . import views

app_name = "tiles"

urlpatterns = [
    path("tiles/<int:z>/<int:x>/<int:y>.png", views.tile, name="tile"),
]
