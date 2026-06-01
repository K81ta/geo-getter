from __future__ import annotations

import json
import urllib.error
import urllib.request
from typing import Any

from . import __version__
from .errors import GeoGetterError


USER_AGENT = f"geo-getter/{__version__}"


def fetch_text(url: str, timeout: int = 60) -> str:
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            data = response.read()
    except urllib.error.URLError as exc:
        raise GeoGetterError("network_failed", str(exc)) from exc
    return data.decode("utf-8", "replace")


def fetch_json(url: str, timeout: int = 60) -> Any:
    text = fetch_text(url, timeout=timeout)
    try:
        return json.loads(text)
    except json.JSONDecodeError as exc:
        raise GeoGetterError("url_unavailable", f"Could not parse JSON response: {exc}") from exc
