"""Build the gRPC server (used by `manage.py run_grpc_server` and the tests)."""

from concurrent import futures

import grpc
from django.conf import settings

from e39maps_api.v1 import pois_pb2_grpc, routes_pb2_grpc

from .services import PoiService, RouteService


def build_server(bind=None, max_workers=None):
    """Return (server, port). bind "host:0" picks a free port."""
    server = grpc.server(futures.ThreadPoolExecutor(
        max_workers=max_workers or settings.GRPC_MAX_WORKERS, thread_name_prefix="grpc"))
    pois_pb2_grpc.add_PoiServiceServicer_to_server(PoiService(), server)
    routes_pb2_grpc.add_RouteServiceServicer_to_server(RouteService(), server)
    port = server.add_insecure_port(bind or settings.GRPC_BIND)
    if not port:
        raise RuntimeError(f"can't listen on {bind or settings.GRPC_BIND}")
    return server, port
