from django.apps import AppConfig
from django.db.backends.signals import connection_created
from django.db.models.signals import post_migrate


class CoreConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'core'

    def ready(self):
        from e39maps.sqlite import configure_sqlite_connection

        connection_created.connect(configure_sqlite_connection,
                                   dispatch_uid="e39maps.configure_sqlite_connection")

        from core.dev_admin import ensure_dev_admin

        # Once per migrate (not per app): sender is this app's config.
        post_migrate.connect(ensure_dev_admin, sender=self, dispatch_uid="e39maps.ensure_dev_admin")
