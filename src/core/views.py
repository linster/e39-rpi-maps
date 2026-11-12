import django
from django.http import JsonResponse
from django.shortcuts import render


def index(request):
    return render(request, "core/index.html", {"django_version": django.get_version()})


def healthz(request):
    """Cheap liveness probe for systemd / the Yocto image."""
    return JsonResponse({"status": "ok"})
