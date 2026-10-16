"""usage/beacon.py — validate and record one event from static/usage.js.

Everything in the body comes from the browser, so it is treated as untrusted:
the type must be one we know, strings are cut short, the page path must be a
Pinakes route, and nothing that fails a check is stored.
"""

from __future__ import annotations

import json
import re

from flask import current_app, request
from werkzeug.exceptions import HTTPException

from .capture import client_context, record_event, route_pattern
from .tools import classify, tool_for_hash

MAX_BODY = 2048
EVENT_TYPES = frozenset({"view", "nav", "out", "dl", "click", "leave"})
_PV = re.compile(r"^[a-z0-9]{1,16}$")
_DOI = re.compile(r"^(?:dx\.)?doi\.org/(10\.\d{4,9}/\S+)$", re.I)


def parse_body() -> dict | None:
    raw = request.get_data(cache=False, as_text=True)
    if not raw or len(raw) > MAX_BODY:
        return None
    try:
        body = json.loads(raw)
    except ValueError:
        return None
    return body if isinstance(body, dict) else None


def _endpoint_for(path: str) -> str | None:
    try:
        adapter = current_app.url_map.bind(request.host or "localhost")
        endpoint, _ = adapter.match(path, method="GET")
        return endpoint
    except HTTPException:
        return None
    except Exception:  # noqa: BLE001 — a redirect or odd path just isn't mapped
        return None


def build_event(body: dict) -> dict | None:
    etype = body.get("t")
    if etype not in EVENT_TYPES:
        return None
    path = body.get("p")
    if not isinstance(path, str) or not path.startswith("/") or len(path) > 300:
        return None
    pv = body.get("pv")
    pv = pv if isinstance(pv, str) and _PV.match(pv) else None

    endpoint = _endpoint_for(path)
    if endpoint is None:
        return None
    t = classify(endpoint, path)
    area, tool = t.area, t.tool

    target, value = None, None
    frag = body.get("h")
    if etype in ("view", "nav") and isinstance(frag, str) and frag:
        frag = frag[:80]
        sub = tool_for_hash(endpoint, frag)
        if sub is not None:
            area, tool = sub.area, sub.tool
        target = frag
    elif etype in ("out", "dl", "click"):
        x = body.get("x")
        if not isinstance(x, str) or not x:
            return None
        x = x.strip()[:300]
        if etype == "out":
            m = _DOI.match(x)
            target = ("doi:" + m.group(1).lower()) if m else x.lower()
        else:
            target = x.lower()
    elif etype == "leave":
        v = body.get("v")
        if not isinstance(v, (int, float)) or v < 0:
            return None
        value = int(min(v, 86_400_000))
    if etype == "nav" and target is None:
        return None

    ctx = client_context()
    return {
        "ts": ctx["ts"], "day": ctx["day"], "visitor": ctx["visitor"],
        # The route pattern, not the path, for the same reason as capture's
        # referrer: an /author/<name> path can hold a removed author's name.
        "type": etype, "page": route_pattern(path), "area": area, "tool": tool,
        "target": target, "value": value, "pv": pv,
    }


def handle() -> bool:
    """Parse, validate and queue the current request's event. True if kept."""
    body = parse_body()
    if body is None:
        return False
    event = build_event(body)
    if event is None:
        return False
    record_event(event)
    return True
