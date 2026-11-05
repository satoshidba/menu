"""CI helper: sign in to a seeded Larder and print a fresh agent-scoped API token."""
import json
import sys
import urllib.request
from http.cookiejar import CookieJar

base, email, password = sys.argv[1], sys.argv[2], sys.argv[3]
opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(CookieJar()))


def call(path, body=None, headers=None):
    req = urllib.request.Request(base + path, data=json.dumps(body).encode() if body is not None else None,
                                 headers={"Content-Type": "application/json", **(headers or {})})
    return json.load(opener.open(req))


csrf = call("/api/v1/auth/login", {"email": email, "password": password})["csrf_token"]
people = [p["id"] for p in call("/api/v1/people") if p["kind"] == "adult"]
scopes = ["recipes:read", "recipes:write", "pantry:read", "pantry:write", "plan:read", "plan:write", "receipts:write",
          "intake:read", "intake:write"]
print(call("/api/v1/tokens", {"name": "CI agent", "scopes": scopes, "person_ids": people}, {"X-CSRF-Token": csrf})["token"])
