"""Wire-level capture for VergeOS API traffic.

Hooks requests.Session.request, so it records exactly what left the
machine and exactly what came back -- status line included. Nothing is
inferred from the SDK's return values.
"""
import json as _json
import os
import requests

CALLS = []
_orig = requests.Session.request


def _hook(self, method, url, **kw):
    resp = _orig(self, method, url, **kw)
    body = kw.get("json", None)
    try:
        text = resp.text
        parsed = _json.loads(text) if text.strip() else None
    except Exception:
        parsed = None
        text = getattr(resp, "text", "")
    CALLS.append({
        "method": method,
        "url": url,
        "endpoint": url.split("/api/v4/")[-1] if "/api/v4/" in url else url,
        "request_body": body,
        "status": resp.status_code,
        "response": parsed,
        "response_text": text[:400],
    })
    return resp


def install():
    requests.Session.request = _hook


def reset():
    CALLS.clear()


def dump(filter_methods=("PUT", "POST", "PATCH", "DELETE"), show_get=False):
    out = []
    for c in CALLS:
        if c["method"] in filter_methods or show_get:
            out.append("%-6s %-28s -> HTTP %s" % (c["method"], c["endpoint"], c["status"]))
            out.append("       request body : %s" % _json.dumps(c["request_body"]))
            r = c["response"]
            rs = _json.dumps(r) if r is not None else c["response_text"]
            out.append("       response     : %s" % (rs[:300]))
    return "\n".join(out)


def client():
    from pyvergeos import VergeClient
    install()
    return VergeClient(
        host=os.environ["VERGEOS_HOST"],
        username=os.environ["VERGEOS_USERNAME"],
        password=os.environ["VERGEOS_PASSWORD"],
        verify_ssl=False,
    )
