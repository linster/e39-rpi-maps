from django.conf import settings
from django.core.management.base import BaseCommand

from tiles.prune import prune


class Command(BaseCommand):
    help = "Keep the map tile cache under DJANGO_TILE_CACHE_MAX_MB (oldest-fetched tiles go first)."

    def add_arguments(self, parser):
        parser.add_argument("--max-mb", type=int, default=None,
                            help="limit in MB (default: DJANGO_TILE_CACHE_MAX_MB)")

    def handle(self, *args, max_mb=None, **options):
        limit = max_mb if max_mb is not None else settings.TILE_CACHE_MAX_MB
        count, total, removed, freed = prune(settings.TILE_CACHE_DIR, limit * 1_000_000)
        self.stdout.write(f"tile cache {settings.TILE_CACHE_DIR}: {count} tiles, {total / 1e6:.1f} MB "
                          f"(limit {limit} MB); removed {removed} tiles, {freed / 1e6:.1f} MB")
