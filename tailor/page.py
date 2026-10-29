"""Placeholder until the Tailor page is written."""

import json


def html(data: dict) -> str:
    return "<!doctype html><title>Tailor</title><script>const D=" + json.dumps(data).replace("<", "\\u003c") + ";</script>"
