"""Regression tests for the third independent review (November 2026),
findings T1 to T9, plus integration checks the review said were missing."""
import importlib.util
import io
import json
import os
import shutil
import tempfile
import threading
import unittest
from email.message import Message
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from unittest import mock
from urllib.error import HTTPError, URLError

import numpy as np

import fixtures
from _helpers import HERE, jload, mod, run, wtext


def server(routes):
    """routes: path -> (status, [(header, value), ...], body). Repeated
    headers are allowed. Request paths are recorded in srv.hits."""
    hits = []

    class H(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def do_GET(self):
            hits.append(self.path)
            st, hdrs, body = routes.get(self.path, (404, [], b"not found"))
            self.send_response(st)
            for k, v in hdrs:
                self.send_header(k, v)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        do_POST = do_GET

    srv = ThreadingHTTPServer(("127.0.0.1", 0), H)
    srv.hits = hits
    srv.base = f"http://127.0.0.1:{srv.server_port}"
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv


HTML = [("Content-Type", "text/html; charset=utf-8")]
TXT = [("Content-Type", "text/plain")]


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.servers = []

    def tearDown(self):
        for s in self.servers:
            s.shutdown()
            s.server_close()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def srv(self, routes):
        s = server(routes)
        self.servers.append(s)
        return s


class TestRobots(Base):
    def test_rules_for_sitefocus_by_name_are_obeyed(self):  # T1
        s = self.srv({"/robots.txt": (200, TXT, b"User-agent: sitefocus\nDisallow: /private\nCrawl-delay: 0.2\n\n"
                                                b"User-agent: *\nDisallow:\n"),
                      "/": (200, HTML, b"<a href='/private'>p</a><a href='/ok'>ok</a>"),
                      "/ok": (200, HTML, b"<title>ok</title>"),
                      "/private": (200, HTML, b"<title>private</title>")})
        run("site_crawl", ["--start", f"{s.base}/", "--out", self.tmp, "--delay", 0])
        self.assertNotIn("/private", s.hits)
        self.assertIn("/ok", s.hits)
        meta = jload(os.path.join(self.tmp, "meta.json"))
        self.assertEqual(meta["crawl_delay"], 0.2)
        self.assertGreaterEqual(meta["interval"], 0.2)

    def test_crawl_delay_parsing(self):  # T1: decimals, our group first, else *
        cd = mod("site_crawl").crawl_delay
        for bad in ("1e309", "nan", "-1", "inf", "soon"):  # fourth review F2
            self.assertIsNone(cd(["User-agent: *", f"Crawl-delay: {bad}"]), bad)
        self.assertEqual(cd(["User-agent: *", "Crawl-delay: 2", "", "User-agent: sitefocus", "Crawl-delay: 0.5"]), 0.5)
        self.assertEqual(cd(["User-agent: googlebot", "Crawl-delay: 9", "", "User-agent: *", "Crawl-delay: 1.5"]), 1.5)
        self.assertEqual(cd(["User-agent: a", "User-agent: sitefocus", "Disallow: /x", "Crawl-delay: 3"]), 3.0)
        self.assertIsNone(cd(["User-agent: *", "Disallow:"]))

    def test_impractical_crawl_delay_stops(self):  # fourth review F2: honour it or stop, never overflow
        s = self.srv({"/robots.txt": (200, TXT, b"User-agent: *\nCrawl-delay: 1e300\n"), "/": (200, HTML, b"<title>x</title>")})
        with self.assertRaises(SystemExit) as cm:
            run("site_crawl", ["--start", f"{s.base}/", "--out", self.tmp, "--delay", 0])
        self.assertIn("impractical", str(cm.exception))
        self.assertEqual(s.hits, ["/robots.txt"])

    def test_rules_for_mozilla_do_not_apply(self):  # T1: the header starts with Mozilla, but that is not our product
        rp = mod("site_crawl").urllib.robotparser.RobotFileParser()
        rp.parse(["User-agent: Mozilla", "Disallow: /"])
        self.assertTrue(mod("site_crawl")._allows(rp, "https://x.test/a"))

    def test_transport_error_after_robots_redirect_stops(self):  # T2
        sc = mod("site_crawl")
        h = Message()
        h["Location"] = "/rules"
        with mock.patch.object(sc._OPENER, "open", side_effect=[
                HTTPError("http://x.test/robots.txt", 302, "redirect", h, io.BytesIO()), URLError("connection refused")]):
            with self.assertRaises(SystemExit):
                sc.load_robots("http://x.test/", sc.Gate(0))

    def test_server_error_after_robots_redirect_stops(self):  # T2
        s = self.srv({"/robots.txt": (302, [("Location", "/rules")], b""), "/rules": (500, [], b"")})
        sc = mod("site_crawl")
        with self.assertRaises(SystemExit):
            sc.load_robots(f"{s.base}/", sc.Gate(0))

    def test_too_many_robots_redirects_means_no_rules(self):  # T2: the RFC 9309 case still allows
        s = self.srv({f"/r{i}": (302, [("Location", f"/r{i + 1}")], b"") for i in range(8)} |
                     {"/robots.txt": (302, [("Location", "/r0")], b"")})
        sc = mod("site_crawl")
        rp, status, _ = sc.load_robots(f"{s.base}/", sc.Gate(0))
        self.assertEqual(status, "too-many-redirects")

    def test_supplied_sitemap_host_obeys_its_own_robots(self):  # T7
        sc = mod("site_crawl")
        ext = self.srv({"/robots.txt": (200, TXT, b"User-agent: *\nDisallow: /\n"),
                        "/private.xml": (200, [], b"<urlset><url><loc>http://x.test/a</loc></url></urlset>")})
        robots = sc.Robots(sc.Gate(0))
        with self.assertRaises(ValueError):
            sc.sitemap_urls(ext.base + "/private.xml", ("x.test", 443), robots, sc.Gate(0), 100, {sc.origin_of(ext.base + "/")})
        self.assertEqual(ext.hits, ["/robots.txt"])

    def test_http_and_https_rules_are_separate(self):  # T7
        sc = mod("site_crawl")
        robots = sc.Robots(sc.Gate(0))
        allow, deny = sc.urllib.robotparser.RobotFileParser(), sc.urllib.robotparser.RobotFileParser()
        allow.parse([])
        deny.parse(["User-agent: *", "Disallow: /private"])
        robots.cache[("http", "x.test", 80)] = (allow, 200, None)
        robots.cache[("https", "x.test", 443)] = (deny, 200, None)
        policy = sc.site_policy(("x.test", 443), robots)
        self.assertIsNone(policy("http://x.test/private"))
        self.assertEqual(policy("https://x.test/private"), "robots.txt")

    def test_combined_directives_keep_none_and_repeated_headers(self):  # T6
        s = self.srv({"/robots.txt": (200, TXT, b""),
                      "/": (200, HTML + [("X-Robots-Tag", "follow"), ("X-Robots-Tag", "noindex, nofollow")], b"<title>h</title>")})
        sc = mod("site_crawl")
        rp = sc.urllib.robotparser.RobotFileParser()
        rp.parse([])
        rec, _, _ = sc.fetch(f"{s.base}/", sc.origin_of(s.base + "/"), rp, sc.Gate(0))
        self.assertIn("noindex", rec["x_robots"])
        p = sc.Page("https://x.test/")
        p.feed('<meta name="robots" content="none"><meta name="robots" content="follow">')
        p.close()
        # Through the embed step: "none" must still mean noindex and nofollow.
        oll = fixtures.fake_ollama()
        try:
            src = os.path.join(self.tmp, "pages.jsonl")
            recs = [{"url": "https://x.test/", "status": 200, "title": "a", "h1": "", "text": "garden widgets", "robots": p.robots},
                    {"url": "https://x.test/b/", "status": 200, "title": "b", "h1": "", "text": "garden widgets b", "x_robots": rec["x_robots"]},
                    {"url": "https://x.test/c/", "status": 200, "title": "c", "h1": "", "text": "garden widgets c"}]
            wtext(src, "".join(json.dumps(r) + "\n" for r in recs))
            run("site_focus", ["--pages", src, "--out", self.tmp, "--ollama", f"http://127.0.0.1:{oll.server_port}"])
            got = {r["url"]: r for r in jload(os.path.join(self.tmp, "pages.json"))}
            self.assertFalse(got["https://x.test/"]["indexable"])
            self.assertTrue(got["https://x.test/"]["nofollow_page"])
            self.assertFalse(got["https://x.test/b/"]["indexable"])
            self.assertTrue(got["https://x.test/b/"]["nofollow_page"])
            self.assertTrue(got["https://x.test/c/"]["indexable"])
        finally:
            oll.shutdown()
            oll.server_close()

    def test_every_5xx_is_retried_on_resume(self):  # T9
        s = self.srv({"/robots.txt": (200, TXT, b""), "/": (200, HTML, b"<a href='/fail'>f</a>"), "/fail": (501, [], b"")})
        run("site_crawl", ["--start", f"{s.base}/", "--out", self.tmp, "--delay", 0])
        run("site_crawl", ["--start", f"{s.base}/", "--out", self.tmp, "--delay", 0])
        self.assertEqual(s.hits.count("/fail"), 2)


class TestCredentials(Base):
    def test_api_redirect_never_forwards_the_token(self):  # T3
        g = mod("gsc")
        seen = []

        class Cap(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def do_GET(self):
                seen.append(self.headers.get("Authorization"))
                self.send_response(200)
                self.end_headers()
                self.wfile.write(b"{}")

            do_POST = do_GET
        cap = ThreadingHTTPServer(("127.0.0.1", 0), Cap)
        threading.Thread(target=cap.serve_forever, daemon=True).start()
        self.servers.append(cap)
        red = self.srv({"/v3/sites": (302, [("Location", f"http://127.0.0.1:{cap.server_port}/capture")], b""),
                        "/token": (307, [("Location", f"http://127.0.0.1:{cap.server_port}/capture")], b"")})
        with mock.patch.object(g, "API", red.base + "/v3"):
            with self.assertRaises(SystemExit):
                g._api("GET", "/sites", "FAKE-TOKEN")
        with self.assertRaises(HTTPError):
            g._post_form(red.base + "/token", {"refresh_token": "FAKE"})
        self.assertEqual(seen, [])


class TestEmbedding(Base):
    def test_short_embedding_replies_never_shift_vectors(self):  # T4
        class Short(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def do_POST(self):
                data = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                vecs = [fixtures.fake_vector(t) for t in data["input"]]
                if len(vecs) > 1:
                    vecs = vecs[:-1]  # drop one row from every multi-text reply
                body = json.dumps({"embeddings": vecs}).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(body)
        srv = ThreadingHTTPServer(("127.0.0.1", 0), Short)
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        self.servers.append(srv)
        texts = [f"text number {w} about {w}" for w in ("alpha", "bravo", "charlie", "delta", "echo", "foxtrot", "golf", "hotel")] * 3
        got = mod("site_focus").embed(texts, f"http://127.0.0.1:{srv.server_port}", "m", batch=8)
        np.testing.assert_allclose(got, np.array([fixtures.fake_vector(t) for t in texts], dtype=np.float32), rtol=1e-6)

    def test_empty_vectors_from_the_server_are_dropped_by_embed(self):  # R5 through the writer, not the planner
        class Z(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def do_POST(self):
                data = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                vecs = [[0.0] * 64 if "emptypage" in t else fixtures.fake_vector(t) for t in data["input"]]
                body = json.dumps({"embeddings": vecs}).encode()
                self.send_response(200)
                self.end_headers()
                self.wfile.write(body)
        srv = ThreadingHTTPServer(("127.0.0.1", 0), Z)
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        self.servers.append(srv)
        src = os.path.join(self.tmp, "pages.jsonl")
        recs = [{"url": f"https://x.test/{i}/", "status": 200, "title": f"t{i}", "h1": "",
                 "text": "emptypage" if i == 1 else f"garden widgets {i}"} for i in range(4)]
        wtext(src, "".join(json.dumps(r) + "\n" for r in recs))
        run("site_focus", ["--pages", src, "--out", self.tmp, "--ollama", f"http://127.0.0.1:{srv.server_port}"])
        urls = {r["url"] for r in jload(os.path.join(self.tmp, "pages.json"))}
        self.assertEqual(urls, {"https://x.test/0/", "https://x.test/2/", "https://x.test/3/"})
        with np.load(os.path.join(self.tmp, "chunks.npz")) as z:
            self.assertTrue((np.linalg.norm(z["vecs"], axis=1) > 0).all())

    def test_interruption_at_every_publish_step_is_safe(self):  # R3: kill the writer at each os.replace
        sf, oll = mod("site_focus"), fixtures.fake_ollama()
        self.servers.append(oll)
        base = f"http://127.0.0.1:{oll.server_port}"
        src = os.path.join(self.tmp, "pages.jsonl")

        def pages_(word):
            return [{"url": f"https://x.test/{i}/", "final": f"https://x.test/{i}/", "status": 200, "title": f"P{i}", "h1": "",
                     "text": f"{word} widgets page {i}"} for i in range(4)]
        real = os.replace
        for k in range(1, 5):  # the writer publishes four files; crash at each
            out = os.path.join(self.tmp, f"out{k}")
            wtext(src, "".join(json.dumps(r) + "\n" for r in pages_("garden")))
            run("site_focus", ["--pages", src, "--out", out, "--ollama", base])
            wtext(src, "".join(json.dumps(r) + "\n" for r in pages_("kitchen")[:3]))
            calls = [0]

            def flaky(a, b, k=k):
                calls[0] += 1
                if calls[0] == k:
                    raise OSError("simulated crash")
                return real(a, b)
            with mock.patch.object(sf.os, "replace", flaky):
                with self.assertRaises(OSError):  # the crash really happened
                    run("site_focus", ["--pages", src, "--out", out, "--ollama", base])
            self.assertFalse([f for f in os.listdir(out) if f.endswith(".tmp")])  # no temporary file left behind
            # Whatever was interrupted, what a reader sees is one whole generation or is refused.
            F = jload(os.path.join(out, "focus.json"))
            with np.load(os.path.join(out, "vectors.npz")) as z:
                keys, gen = [str(x) for x in z["keys"]], str(z["generation"])
            urls = [r["url"] for r in jload(os.path.join(out, "pages.json"))]
            consistent = gen == F["generation"] and set(keys) == set(urls)
            if consistent:
                continue
            crawl = os.path.join(self.tmp, f"crawl{k}")
            os.makedirs(crawl)
            wtext(os.path.join(crawl, "pages.jsonl"), "")
            wtext(os.path.join(crawl, "edges.csv"), "source,target,zone,anchor,nofollow\n")
            wtext(os.path.join(self.tmp, "p.txt"), "https://x.test/0/\n")
            with self.assertRaises(SystemExit) as cm:
                run("link_plan", ["--crawl", crawl, "--focus", out, "--priority", os.path.join(self.tmp, "p.txt"),
                                  "--out", os.path.join(self.tmp, "plan"), "--no-recheck"])
            self.assertIn("interrupted", str(cm.exception))


class TestAtomicWrite(Base):
    def test_permissions_are_kept_or_usual(self):  # fifth review: mkstemp made outputs owner-only
        aw = mod("common").atomic_write
        p = os.path.join(self.tmp, "x.csv")
        old = os.umask(0o022)
        try:
            aw(p, lambda f: f.write("a"))
            self.assertEqual(os.stat(p).st_mode & 0o777, 0o644)
            os.chmod(p, 0o640)
            aw(p, lambda f: f.write("b"))
            self.assertEqual(os.stat(p).st_mode & 0o777, 0o640)
        finally:
            os.umask(old)


class TestCli(Base):
    def test_search_console_uses_the_resolved_start(self):  # N4 through the CLI
        path = os.path.join(HERE, "..", "packaging", "cli.py")
        if os.path.exists(path):
            spec = importlib.util.spec_from_file_location("sitefocus_cli_under_test", path)
            cli = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(cli)
        else:
            cli = importlib.import_module("sitefocus.cli")
        calls, gsc_start = [], []

        def fake_run(module, argv):
            calls.append(module)
            if module == "site_crawl":
                out = argv[argv.index("--out") + 1]
                os.makedirs(out, exist_ok=True)
                wtext(os.path.join(out, "meta.json"), json.dumps({"start": "https://x.test/", "start_final": "https://www.x.test/"}))
        wtext(os.path.join(self.tmp, "p.txt"), "https://x.test/\n")
        with mock.patch.object(cli, "_run", fake_run), mock.patch.object(cli, "_gsc_connect", lambda: True), \
                mock.patch.object(cli, "_gsc_for_run", lambda a, start, out: gsc_start.append(start)):
            cli.run(["https://x.test/", "--priority", os.path.join(self.tmp, "p.txt"), "--out", self.tmp])
        self.assertEqual(gsc_start, ["https://www.x.test/"])
        self.assertEqual(calls[0], "site_crawl")


if __name__ == "__main__":
    unittest.main()
