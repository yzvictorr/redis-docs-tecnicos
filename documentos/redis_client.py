from functools import lru_cache

import redis
from django.conf import settings


@lru_cache(maxsize=None)
def _cliente(url):
    # decode_responses=True: o Redis devolve str em vez de bytes.
    # O cliente mantém um pool de conexões, então reaproveitamos uma instância por URL.
    return redis.Redis.from_url(url, decode_responses=True)


def get_redis():
    return _cliente(settings.REDIS_URL)
