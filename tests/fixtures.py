"""Offline fixtures for the sitefocus tests: a small website with every case
the tool must handle, and a fake Ollama that returns deterministic vectors.
Both run on 127.0.0.1 on free ports."""
import hashlib
import json
import math
import re
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

NAV = '<nav><a href="/">Home</a> <a href="/products/widgets/">Widgets</a> <a href="/blog/">Blog</a> <a href="/old-widgets">Old widgets</a> <a href="/search/">Search</a> <a href="/flaky/">Flaky</a></nav>'
FOOT = '<footer><a href="/missing-page">Gone</a> <a href="/private/secret">Private</a> <a href="{root}">Home again</a></footer>'

ARTICLES = {
    1: ("Choosing garden widgets", "Garden widgets help you water plants. A sturdy garden widget lasts for years. Compare widget pricing before you buy."),
    2: ("Widget maintenance tips", "Clean your widgets every spring. Widget maintenance keeps the motor running. Industrial widgets need oiling."),
    3: ("Watering schedules", "Water tomatoes in the morning. Plants need deep watering once a week in summer. Mulch keeps soil moist."),
    4: ("Composting basics", "Compost turns kitchen scraps into soil. Turn the pile every week. Brown and green material should balance."),
    5: ("Industrial widget sizing", "Industrial widgets come in three sizes. Sizing an industrial widget depends on flow rate and pressure."),
    6: ("Hidden article", "This article is noindexed by header. Widgets and compost both appear here for testing."),
}


def page(title, body, links="", robots="", canonical="", root="/"):
    meta = f'<meta name="robots" content="{robots}">' if robots else ""
    canon = f'<link rel="canonical" href="{canonical}">' if canonical else ""
    return (f"<!doctype html><html><head><title>{title} - Example Widgets</title>{meta}{canon}</head><body>"
            f"<header><a href=\"/\">Example Widgets</a></header>{NAV}<main><h1>{title}</h1><p>{body}</p>{links}</main>"
            f"{FOOT.format(root=root)}</body></html>")


class Site:
    def __init__(self):
        self.flaky_hits = 0
        self.lock = threading.Lock()

    def routes(self, base):
        r = {}
        r["/robots.txt"] = (200, "text/plain", f"User-agent: *\nDisallow: /private/\nSitemap: {base}/sitemap.xml\n", {})
        locs = ["/", "/products/widgets/", "/products/industrial/", "/blog/"] + [f"/blog/{i}/" for i in ARTICLES]
        r["/sitemap.xml"] = (200, "application/xml", '<?xml version="1.0"?><urlset>' + "".join(f"<loc>{base}{u}</loc>" for u in locs) + "</urlset>", {})
        r["/"] = (200, "text/html", page("Example Widgets home", "We sell garden widgets and industrial widgets, with honest widget pricing.", root=base), {})
        r["/products/widgets/"] = (200, "text/html", page("Garden widget pricing", "Garden widgets from 20 dollars. Compare garden widget pricing and buy online.", root=base), {})
        # Orphan: only in the sitemap; the only link to it sits on a page-level nofollow page.
        r["/products/industrial/"] = (200, "text/html", page("Industrial widget pricing", "Industrial widgets for factories. Industrial widget pricing depends on size.", root=base), {})
        r["/blog/"] = (200, "text/html", page("Blog", "Articles about widgets and gardens.", "".join(f'<a href="/blog/{i}/">{t}</a> ' for i, (t, _) in ARTICLES.items() if i != 5), root=base), {})
        for i, (t, b) in ARTICLES.items():
            links = ""
            if i == 1:
                links = '<p>See <a href="/products/widgets/">garden widget pricing</a>.</p>'
            hdr = {"X-Robots-Tag": "noindex"} if i == 6 else {}
            robots = "nofollow" if i == 4 else ""
            if i == 4:
                links = '<p><a href="/products/industrial/">industrial widgets</a></p>'
            r[f"/blog/{i}/"] = (200, "text/html", page(t, b, links, robots=robots, root=base), hdr)
        r["/old-widgets"] = (301, "text/html", "", {"Location": f"{base}/products/widgets/"})
        r["/search/"] = (200, "text/html", page("Search", "Search results.", robots="noindex", root=base), {})
        r["/js-only/"] = (200, "text/html", page("JS only", "Only linked from JavaScript.", root=base), {})
        return r

    def server(self):
        site = self

        class H(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def do_GET(self):
                base = f"http://127.0.0.1:{self.server.server_port}"
                path = self.path.split("?")[0]
                if path == "/flaky/":
                    with site.lock:
                        site.flaky_hits += 1
                        first = site.flaky_hits == 1
                    if first:
                        self.send_response(429)
                        self.end_headers()
                        return
                    body = page("Flaky but fine", "This page rate limits once, then works.", root=base).encode()
                    self.send_response(200)
                    self.send_header("Content-Type", "text/html; charset=utf-8")
                    self.end_headers()
                    self.wfile.write(body)
                    return
                rt = site.routes(base).get(path)
                if not rt:
                    self.send_response(404)
                    self.send_header("Content-Type", "text/html")
                    self.end_headers()
                    self.wfile.write(b"<html><body>not found</body></html>")
                    return
                code, ctype, body, hdr = rt
                self.send_response(code)
                self.send_header("Content-Type", ctype + ("; charset=utf-8" if "html" in ctype else ""))
                for k, v in hdr.items():
                    self.send_header(k, v)
                self.end_headers()
                self.wfile.write(body.encode())

        srv = ThreadingHTTPServer(("127.0.0.1", 0), H)
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        return srv


def fake_vector(text, dim=64):
    """Deterministic bag-of-words hash embedding: similar texts get similar vectors."""
    v = [0.0] * dim
    for w in re.findall(r"[a-z]+", text.lower()):
        if len(w) < 3:
            continue
        h = int(hashlib.md5(w.encode()).hexdigest(), 16)
        v[h % dim] += 1.0 if (h >> 8) % 2 else -1.0
    n = math.sqrt(sum(x * x for x in v)) or 1.0
    return [x / n for x in v]


def fake_ollama():
    class H(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def _json(self, obj):
            body = json.dumps(obj).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):
            if self.path == "/api/tags":
                self._json({"models": [{"name": "nomic-embed-text:latest"}]})
            else:
                self.send_response(404)
                self.end_headers()

        def do_POST(self):
            data = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            if self.path == "/api/embed":
                self._json({"embeddings": [fake_vector(t) for t in data["input"]]})
            elif self.path == "/api/generate":
                self._json({"response": "NONE"})
            else:
                self.send_response(404)
                self.end_headers()

    srv = ThreadingHTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv
