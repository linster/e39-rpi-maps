import signal
import threading

from django.conf import settings
from django.core.management.base import BaseCommand

from api.server import build_server


class Command(BaseCommand):
    help = "Run the gRPC server for the HMI (POIs, routes) until stopped."

    def add_arguments(self, parser):
        parser.add_argument("--bind", default=settings.GRPC_BIND,
                            help="host:port (default: DJANGO_GRPC_BIND or %(default)s)")

    def handle(self, *args, bind, **options):
        server, port = build_server(bind)
        server.start()
        self.stdout.write(f"gRPC server listening on {bind.rsplit(':', 1)[0]}:{port}", ending="\n")
        self.stdout.flush()

        stopped = threading.Event()
        for sig in (signal.SIGTERM, signal.SIGINT):
            signal.signal(sig, lambda *_: stopped.set())
        stopped.wait()
        self.stdout.write("stopping (5 s grace for running calls)")
        server.stop(grace=5).wait()
