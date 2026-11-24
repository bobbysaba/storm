"""The day's shared items from ``[mqtt] shared_items_url``, read once at
startup: for brokers that don't send retained messages on wildcard
subscriptions (AWS IoT). The layout is in https://bobbysaba.com/docs/storm/ref/wire-format/#retained-messages.
"""
from __future__ import annotations

import json
from urllib.request import Request, urlopen

from data import auth


def fetch(url: str, timeout: float = 10) -> dict:
    """{item id: payload as published}, asked with url's [[auth]] key and CA."""
    full, headers, context = auth.request(url, {"User-Agent": "Mozilla/5.0 STORM/1.0"})
    with urlopen(Request(full, headers=headers), timeout=timeout, context=context) as resp:
        data = json.loads(resp.read().decode())
    if not isinstance(data, dict):
        raise ValueError("shared items must be a JSON object of items by ID")
    return data
