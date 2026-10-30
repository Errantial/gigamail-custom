"""Per-thread HTTP connection pools; bounded reads and no write retries."""
import threading

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

_local = threading.local()
DEFAULT_TIMEOUT = (5, 30)


class _Retry(Retry):
    def get_retry_after(self, response):
        delay = super().get_retry_after(response)
        return min(delay, 5) if delay is not None else None


def session():
    if not hasattr(_local, "session"):
        client = requests.Session()
        retry = _Retry(total=2, connect=2, read=0, status=2,
                       allowed_methods=frozenset({"GET", "HEAD"}),
                       status_forcelist=(429, 502, 503, 504), backoff_factor=0.25)
        client.mount("https://", HTTPAdapter(max_retries=retry))
        client.mount("http://", HTTPAdapter(max_retries=retry))
        _local.session = client
    return _local.session


def request(method, url, **kwargs):
    kwargs.setdefault("timeout", DEFAULT_TIMEOUT)
    # Requests' adapter may retry connection failures for every method. Use a
    # separate zero-retry pool for writes, including sends and draft creation.
    client = session()
    if method.upper() not in {"GET", "HEAD"}:
        if not hasattr(_local, "writes"):
            _local.writes = requests.Session()
            _local.writes.mount("https://", HTTPAdapter(max_retries=0))
            _local.writes.mount("http://", HTTPAdapter(max_retries=0))
        client = _local.writes
    return client.request(method, url, **kwargs)


def get(url, **kwargs):
    return request("GET", url, **kwargs)


def post(url, **kwargs):
    return request("POST", url, **kwargs)


def patch(url, **kwargs):
    return request("PATCH", url, **kwargs)


def delete(url, **kwargs):
    return request("DELETE", url, **kwargs)
