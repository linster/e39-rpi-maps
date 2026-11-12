"""A development admin account, created after `migrate`.

DJANGO_DEV_ADMIN="user:password" (settings.DEV_ADMIN): after migrate, create
that superuser in the state database if no user of that name exists yet. An
existing user is never touched, so a password changed in the admin stays
changed. Development (DEBUG on) defaults to admin:e39rpi; production only
gets one when the variable is set (the Yocto image does that for development
images with debug-tweaks).
"""

import logging

from django.conf import settings

log = logging.getLogger(__name__)


def ensure_dev_admin(sender, using="default", **kwargs):
    if using != "default" or not settings.DEV_ADMIN:
        return
    username, _, password = settings.DEV_ADMIN.partition(":")
    if not username or not password:
        log.warning("DJANGO_DEV_ADMIN must look like user:password; no admin created")
        return

    from django.contrib.auth import get_user_model

    User = get_user_model()
    if User.objects.using(using).filter(username=username).exists():
        return
    User.objects.db_manager(using).create_superuser(username=username, email="", password=password)
    print(f"Created the development admin '{username}' (DJANGO_DEV_ADMIN).")
