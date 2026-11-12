from django.apps import AppConfig
from django.db.backends.signals import connection_created


class CoreConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'core'

    def ready(self):
        from e39maps.sqlite import configure_sqlite_connection

        connection_created.connect(configure_sqlite_connection,
                                   dispatch_uid="e39maps.configure_sqlite_connection")
