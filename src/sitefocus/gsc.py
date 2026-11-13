#!/usr/bin/env python3
"""Google Search Console connection for sitefocus. Standard library only.

  gsc.py login [--client-secret client_secret.json]
  gsc.py sites
  gsc.py pull --site sc-domain:example.com [--days 90] --out pages.csv
  gsc.py pull --url https://www.example.com/ --out pages.csv   (finds the property)

Read-only access (webmasters.readonly). Credentials, in order:
  1. ~/.config/sitefocus/gsc.json, written by `login` (OAuth desktop client,
     browser consent once, refresh token stored with 0600 permissions)
  2. gcloud application-default credentials, if they carry the Search Console
     scope:  gcloud auth application-default login \
       --scopes=https://www.googleapis.com/auth/webmasters.readonly,https://www.googleapis.com/auth/cloud-platform

To get client_secret.json: Google Cloud Console > APIs & Services > enable
"Google Search Console API" > Credentials > Create credentials > OAuth client
ID > Desktop app > Download JSON. The Google account you sign in with needs
access to the Search Console property.

The pull writes the same columns as a Search Console Pages export, so the
rest of sitefocus treats it exactly like a manual export.
"""
import argparse
import base64
import csv
import datetime
import hashlib
import json
import os
import secrets
import sys
import urllib.error
import urllib.parse
import urllib.request
import webbrowser
from http.server import BaseHTTPRequestHandler, HTTPServer

try:
    from .common import load_json, write_private
except ImportError:
    from common import load_json, write_private

SCOPE = "https://www.googleapis.com/auth/webmasters.readonly"
AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
TOKEN_URL = os.environ.get("SITEFOCUS_GOOGLE_TOKEN_URL", "https://oauth2.googleapis.com/token")
API = os.environ.get("SITEFOCUS_GSC_API", "https://searchconsole.googleapis.com/webmasters/v3")
CONFIG_DIR = os.path.expanduser(os.environ.get("SITEFOCUS_CONFIG_DIR", "~/.config/sitefocus"))
STORE = os.path.join(CONFIG_DIR, "gsc.json")
ADC = os.path.expanduser(os.environ.get("SITEFOCUS_ADC", "~/.config/gcloud/application_default_credentials.json"))

HOW_TO_CONNECT = """Search Console is not connected.

With it, sitefocus protects pages that earn clicks, sends links to pages
ranking 4 to 20 first, and estimates the traffic effect of the plan.

Connect (read-only) in one of two ways:
  1. sitefocus gsc-login --client-secret client_secret.json
     (Google Cloud Console > APIs & Services > enable "Google Search Console API"
      > Credentials > OAuth client ID > Desktop app > Download JSON)
  2. gcloud auth application-default login --scopes=https://www.googleapis.com/auth/webmasters.readonly,https://www.googleapis.com/auth/cloud-platform
"""


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    """Google's token and API endpoints never redirect; a redirect is refused
    so a token or bearer header can never be sent to another address."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise urllib.error.HTTPError(req.full_url, code, f"redirect to {newurl} refused (credentials are never forwarded)",
                                     headers, fp)


_OPENER = urllib.request.build_opener(_NoRedirect)


def _post_form(url, data):
    req = urllib.request.Request(url, data=urllib.parse.urlencode(data).encode(),
                                 headers={"Content-Type": "application/x-www-form-urlencoded"})
    with _OPENER.open(req, timeout=60) as r:
        return json.load(r)


def _api(method, path, token, body=None):
    req = urllib.request.Request(f"{API}{path}", method=method,
                                 data=json.dumps(body).encode() if body is not None else None,
                                 headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"})
    try:
        with _OPENER.open(req, timeout=120) as r:
            return json.load(r)
    except urllib.error.HTTPError as ex:
        if 300 <= ex.code < 400:
            ex.close()
            raise SystemExit(f"Search Console API answered {ex.code} with a redirect for {path}; refused, credentials are never forwarded")
        detail = ex.read().decode("utf-8", "replace")[:300]
        ex.close()
        raise SystemExit(f"Search Console API {ex.code} for {path}: {detail}")


def credentials():
    """Return (client_id, client_secret, refresh_token, source) or None."""
    for path, src in ((STORE, "sitefocus"), (ADC, "gcloud")):
        if os.path.exists(path):
            try:
                d = load_json(path)
            except Exception:
                continue
            if d.get("refresh_token") and d.get("client_id") and d.get("client_secret"):
                return d["client_id"], d["client_secret"], d["refresh_token"], src
    return None


def access_token(creds=None):
    creds = creds or credentials()
    if not creds:
        raise SystemExit(HOW_TO_CONNECT)
    cid, csec, rtok, src = creds
    try:
        tok = _post_form(TOKEN_URL, {"client_id": cid, "client_secret": csec, "refresh_token": rtok, "grant_type": "refresh_token"})
    except urllib.error.HTTPError as ex:
        ex.close()
        raise SystemExit(f"Could not refresh the {src} Search Console credentials ({ex.code}). Run sitefocus gsc-login again.")
    granted = tok.get("scope", "").split()
    if granted and not ({SCOPE, "https://www.googleapis.com/auth/webmasters"} & set(granted)):
        raise SystemExit(f"The {src} credentials do not include Search Console access. " + HOW_TO_CONNECT)
    return tok["access_token"]


def login(client_secret_path, open_browser=True, timeout=300):
    d = load_json(client_secret_path)
    d = d.get("installed") or d.get("web") or d
    cid, csec = d["client_id"], d["client_secret"]
    verifier = base64.urlsafe_b64encode(secrets.token_bytes(48)).rstrip(b"=").decode()
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
    state = secrets.token_urlsafe(16)
    got = {}

    class H(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def do_GET(self):
            q = {k: v[0] for k, v in urllib.parse.parse_qs(urllib.parse.urlsplit(self.path).query).items()}
            ours = q.get("state") == state
            if ours and ("code" in q or "error" in q) and not got:
                got.update(q)  # only the first callback carrying our state counts
            ok = ours and "code" in q
            self.send_response(200 if ours else 404)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.end_headers()
            self.wfile.write(("<p>sitefocus is connected to Search Console. You can close this tab.</p>" if ok
                              else "<p>Connection failed. Return to the terminal.</p>" if ours else "").encode())

    srv = HTTPServer(("127.0.0.1", 0), H)
    srv.timeout = 2
    redirect = f"http://127.0.0.1:{srv.server_port}/"
    url = AUTH_URL + "?" + urllib.parse.urlencode({
        "client_id": cid, "redirect_uri": redirect, "response_type": "code", "scope": SCOPE,
        "access_type": "offline", "prompt": "consent", "state": state,
        "code_challenge": challenge, "code_challenge_method": "S256"})
    print("Opening your browser to connect Search Console (read-only). If it does not open, visit:\n" + url, flush=True)
    if open_browser:
        webbrowser.open(url)
    deadline = __import__("time").time() + timeout
    while not got and __import__("time").time() < deadline:
        srv.handle_request()  # stray or forged callbacks without our state are ignored
    srv.server_close()
    if not got:
        raise SystemExit(f"No response from the browser within {timeout // 60} minutes; run sitefocus gsc-login again.")
    if "code" not in got:
        raise SystemExit(f"Search Console connection was refused: {got.get('error', 'unknown error')}")
    tok = _post_form(TOKEN_URL, {"client_id": cid, "client_secret": csec, "code": got["code"], "code_verifier": verifier,
                                 "grant_type": "authorization_code", "redirect_uri": redirect})
    if not tok.get("refresh_token"):
        raise SystemExit("Google returned no refresh token; remove sitefocus from https://myaccount.google.com/permissions and try again.")
    write_private(STORE, json.dumps({"client_id": cid, "client_secret": csec, "refresh_token": tok["refresh_token"]}))
    print(f"Connected. Credentials saved to {STORE} (readable only by you).")


def sites(token=None):
    token = token or access_token()
    return [(s["siteUrl"], s.get("permissionLevel", "")) for s in _api("GET", "/sites", token).get("siteEntry", [])]


def pick_site(url, available):
    """Best property for a URL: the URL-prefix property with the same scheme,
    host and port and the longest path prefix (on a path boundary), else the
    narrowest domain property (sc-domain:) the host belongs to."""
    p = urllib.parse.urlsplit(url)
    host = (p.hostname or "").lower()
    scheme = p.scheme.lower()
    dport = {"http": 80, "https": 443}.get(scheme)
    path = p.path or "/"
    best = None
    for s_ in available:
        if s_.startswith("sc-domain:"):
            continue
        q = urllib.parse.urlsplit(s_)
        if q.scheme.lower() != scheme or (q.hostname or "").lower() != host or (q.port or dport) != (p.port or dport):
            continue
        qp = q.path or "/"
        if not qp.endswith("/"):
            qp += "/"
        if (path if path.endswith("/") else path + "/").startswith(qp):
            if best is None or len(qp) > best[0]:
                best = (len(qp), s_)
    if best:
        return best[1]
    dom = [s_ for s_ in available if s_.startswith("sc-domain:") and (host == s_[10:].lower() or host.endswith("." + s_[10:].lower()))]
    return max(dom, key=len) if dom else None


def pull_pages(site, days=90, token=None, out=None, host=None):
    """Clicks, impressions, CTR and position per page for the last `days`
    days (ending 3 days ago; Search Console data lags). Paginates past 25,000."""
    token = token or access_token()
    end = datetime.date.today() - datetime.timedelta(days=3)
    start = end - datetime.timedelta(days=days - 1)
    rows, start_row = [], 0
    path = f"/sites/{urllib.parse.quote(site, safe='')}/searchAnalytics/query"
    while True:
        body = {"startDate": start.isoformat(), "endDate": end.isoformat(), "dimensions": ["page"],
                "rowLimit": 25000, "startRow": start_row, "type": "web"}
        if host:
            body["dimensionFilterGroups"] = [{"filters": [{"dimension": "page", "operator": "contains", "expression": f"://{host}/"}]}]
        got = _api("POST", path, token, body).get("rows", [])
        rows += got
        if len(got) < 25000:
            break
        start_row += 25000
    if out:
        os.makedirs(os.path.dirname(os.path.abspath(out)), exist_ok=True)
        with open(out, "w", newline="") as f:
            w = csv.writer(f)
            w.writerow(["Top pages", "Clicks", "Impressions", "CTR", "Position"])
            for r in rows:
                w.writerow([r["keys"][0], int(r["clicks"]), int(r["impressions"]), f"{100 * r['ctr']:.2f}%", round(r["position"], 1)])
    return {"site": site, "start": start.isoformat(), "end": end.isoformat(), "pages": len(rows),
            "clicks": int(sum(r["clicks"] for r in rows)), "impressions": int(sum(r["impressions"] for r in rows))}


def status():
    c = credentials()
    if not c:
        return "not connected"
    try:
        n = len(sites(access_token(c)))
        return f"connected via {c[3]} credentials, {n} properties"
    except SystemExit as ex:
        return f"credentials found ({c[3]}) but not usable: {str(ex).splitlines()[0]}"


def main():
    ap = argparse.ArgumentParser(description="Search Console connection for sitefocus")
    sub = ap.add_subparsers(dest="cmd", required=True)
    lg = sub.add_parser("login")
    lg.add_argument("--client-secret", default=os.environ.get("SITEFOCUS_GSC_CLIENT"))
    lg.add_argument("--no-browser", action="store_true")
    sub.add_parser("sites")
    sub.add_parser("status")
    pl = sub.add_parser("pull")
    pl.add_argument("--site")
    pl.add_argument("--url")
    pl.add_argument("--days", type=int, default=90)
    pl.add_argument("--out", required=True)
    a = ap.parse_args()
    if a.cmd == "login":
        if not a.client_secret:
            raise SystemExit("Pass --client-secret client_secret.json (or set SITEFOCUS_GSC_CLIENT).\n\n" + HOW_TO_CONNECT)
        login(a.client_secret, open_browser=not a.no_browser)
    elif a.cmd == "status":
        print(status())
    elif a.cmd == "sites":
        for s, perm in sites():
            print(f"{s}\t{perm}")
    else:
        site = a.site
        host = None
        if not site:
            if not a.url:
                raise SystemExit("Pass --site (a property from `sites`) or --url (the site's home page).")
            site = pick_site(a.url, [s for s, _ in sites()])
            if not site:
                raise SystemExit(f"No Search Console property you can read covers {a.url}. Properties: " + ", ".join(s for s, _ in sites()))
            host = urllib.parse.urlsplit(a.url).hostname
        info = pull_pages(site, a.days, out=a.out, host=host)
        print(json.dumps(info))


if __name__ == "__main__":
    sys.exit(main())
