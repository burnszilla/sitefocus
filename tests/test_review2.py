"""Regression tests for the second independent review (November 2026). Each
test names the residual (R) or new (N) finding it guards against."""
import csv
import json
import os
import shutil
import sys
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import numpy as np

from _helpers import mod, run


def server(routes):
    """A tiny site. routes: path -> (status, headers, body). Every request
    path is recorded in srv.hits."""
    hits = []

    class H(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def do_GET(self):
            hits.append(self.path)
            st, hdrs, body = routes.get(self.path, (404, {}, b"not found"))
            self.send_response(st)
            for k, v in hdrs.items():
                self.send_header(k, v)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    srv = ThreadingHTTPServer(("127.0.0.1", 0), H)
    srv.hits = hits
    srv.base = f"http://127.0.0.1:{srv.server_port}"
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv


HTML = {"Content-Type": "text/html; charset=utf-8"}
ROBOTS = {"Content-Type": "text/plain"}


class TestRequestPolicy(unittest.TestCase):
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

    def test_start_redirect_into_disallowed_path_is_not_fetched(self):  # R2
        s = self.srv({"/robots.txt": (200, ROBOTS, b"User-agent: *\nDisallow: /blocked\n"),
                      "/start": (302, {"Location": "/blocked"}, b""),
                      "/blocked": (200, HTML, b"<title>secret</title>")})
        with self.assertRaises(SystemExit) as cm:
            run("site_crawl", ["--start", f"{s.base}/start", "--out", self.tmp, "--delay", 0])
        self.assertIn("not allowed", str(cm.exception))
        self.assertNotIn("/blocked", s.hits)

    def test_robots_redirect_to_another_origin_is_not_followed(self):  # R2
        other = self.srv({"/stolen-robots": (200, ROBOTS, b"User-agent: *\nDisallow:\n")})
        s = self.srv({"/robots.txt": (301, {"Location": f"{other.base}/stolen-robots"}, b"")})
        rp, status, _ = mod("site_crawl").load_robots(f"{s.base}/", mod("site_crawl").Gate(0))
        self.assertEqual(status, "redirect-offsite")
        self.assertEqual(other.hits, [])

    def test_sitemap_redirects_and_disallowed_sitemaps_are_not_fetched(self):  # R2
        sc = mod("site_crawl")
        other = self.srv({"/off-sitemap": (200, {}, b"<urlset><url><loc>http://x/</loc></url></urlset>")})
        s = self.srv({"/sm.xml": (301, {"Location": f"{other.base}/off-sitemap"}, b""),
                      "/private.xml": (200, {}, b"<urlset></urlset>")})
        rp = sc.urllib.robotparser.RobotFileParser()
        rp.parse(["User-agent: *", "Disallow: /private"])
        origin = sc.origin_of(s.base + "/")
        roots = {sc.origin_of(s.base + "/sm.xml")}
        for path in ("/sm.xml", "/private.xml"):
            with self.assertRaises(ValueError):
                sc.sitemap_urls(s.base + path, origin, rp, sc.Gate(0), 100, roots)
        self.assertEqual(other.hits, [])
        self.assertNotIn("/private.xml", s.hits)
        # Same host, other port: not the same root.
        self.assertIsNotNone(sc.sitemap_policy(origin, rp, roots)(other.base + "/x.xml"))

    def test_every_rate_limit_cools_the_gate(self):  # R8
        sc = mod("site_crawl")
        s = self.srv({"/x": (429, {"Retry-After": "0.2"}, b"")})
        gate = sc.Gate(0)
        res = sc.request(s.base + "/x", gate, lambda u: None)
        self.assertEqual(res.status, 429)
        self.assertEqual(gate.cools, [0.2, 0.2, 0.2])  # every attempt, the last too, for as long as the server asked
        self.assertEqual(s.hits, ["/x"] * 3)

    def test_start_rate_limited_stops(self):  # R8
        s = self.srv({"/robots.txt": (200, ROBOTS, b""), "/": (429, {"Retry-After": "0"}, b"")})
        with self.assertRaises(SystemExit):
            run("site_crawl", ["--start", f"{s.base}/", "--out", self.tmp, "--delay", 0])

    def test_recheck_uses_the_shared_gate(self):  # R8
        s = self.srv({"/r": (301, {"Location": "/final"}, b""), "/slow": (429, {"Retry-After": "0.2"}, b"")})
        gate = mod("site_crawl").Gate(0)
        got = mod("link_plan").recheck([s.base + "/r", s.base + "/slow"], gate=gate)
        self.assertEqual(got[s.base + "/r"], (301, s.base + "/final"))
        self.assertEqual(got[s.base + "/slow"][0], 429)
        self.assertEqual(gate.cools, [0.2])
        self.assertNotIn("/final", s.hits)  # rechecks never follow redirects

    def test_malformed_link_does_not_kill_the_worker(self):  # N1
        s = self.srv({"/robots.txt": (200, ROBOTS, b""),
                      "/": (200, HTML, b"<a href='http://127.0.0.1:bad/x'>bad</a><a href='http://[x/y'>v6</a><a href='/good'>good</a>"),
                      "/good": (200, HTML, b"<title>good</title>")})
        run("site_crawl", ["--start", f"{s.base}/", "--out", self.tmp, "--delay", 0])
        got = {r["url"] for r in mod("common").read_pages(os.path.join(self.tmp, "pages.jsonl"))}
        self.assertEqual(got, {f"{s.base}/", f"{s.base}/good"})

    def test_worker_failure_fails_the_crawl(self):  # N1
        sc = mod("site_crawl")
        s = self.srv({"/robots.txt": (200, ROBOTS, b""), "/": (200, HTML, b"<title>x</title>")})
        real = sc.fetch

        def boom(*a, **k):
            raise RuntimeError("injected")
        sc.fetch = boom
        try:
            with self.assertRaises(SystemExit) as cm:
                run("site_crawl", ["--start", f"{s.base}/", "--out", self.tmp, "--delay", 0])
            self.assertIn("worker failed", str(cm.exception))
        finally:
            sc.fetch = real

    def test_quoted_http_charset(self):  # R7
        cs = mod("site_crawl")._charset
        self.assertEqual(cs('text/html; charset="windows-1252"', b"caf\xe9"), "windows-1252")
        self.assertEqual(cs("text/html; charset = 'iso-8859-1' ", b""), "iso-8859-1")
        self.assertEqual(cs("text/html", b'<meta charset="latin-1">'), "latin-1")
        self.assertEqual(cs("text/html; charset=bogus-x", b""), "utf-8")

    def test_ipv6_and_bad_ports(self):  # N6, N1
        sc = mod("site_crawl")
        self.assertEqual(sc.norm("http://[::1]:8000/a"), "http://[::1]:8000/a")
        self.assertEqual(sc.norm("http://[::1]:80/"), "http://[::1]/")
        self.assertTrue(sc.same_site("http://[::1]:8000/x", sc.origin_of("http://[::1]:8000/")))
        self.assertIsNone(sc.norm("http://127.0.0.1:bad/x"))
        self.assertFalse(sc.same_site("http://127.0.0.1:bad/x", ("127.0.0.1", 80)))

    def test_robots_meta_tags_accumulate(self):  # N5
        p = mod("site_crawl").Page("https://x.test/")
        p.feed('<meta name="robots" content="noindex"><meta name="robots" content="follow">')
        p.close()
        self.assertIn("noindex", p.robots)


def synthetic(tmp, pages, edges, vectors, priority, meta=None):
    """Write a crawl and an embedding directory by hand. pages: list of
    (url, title, robots); edges: (source, target, nofollow)."""
    crawl, focus = os.path.join(tmp, "crawl"), os.path.join(tmp, "focus")
    os.makedirs(crawl)
    os.makedirs(focus)
    with open(os.path.join(crawl, "pages.jsonl"), "w") as f:
        for u, t, rob in pages:
            f.write(json.dumps({"url": u, "final": u, "status": 200, "title": t, "h1": t, "robots": rob, "text": f"{t} words"}) + "\n")
    with open(os.path.join(crawl, "edges.csv"), "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["source", "target", "zone", "anchor", "nofollow"])
        for s, t, nf in edges:
            w.writerow([s, t, "body", "anchor", int(nf)])
    with open(os.path.join(crawl, "meta.json"), "w") as f:
        json.dump(meta or {"origin_host": "x.test"}, f)
    rows = [{"url": u, "section": u.split("/")[3] or "(root)", "indexable": "noindex" not in rob, "nofollow_page": "nofollow" in rob,
             "title": t, "h1": t, "main_words": 2, "distance": 0.1, "cluster": 0} for u, t, rob in pages]
    with open(os.path.join(focus, "pages.json"), "w") as f:
        json.dump(rows, f)
    np.savez(os.path.join(focus, "vectors.npz"), vecs=np.array(vectors, dtype=np.float32),
             keys=np.array([u for u, _, _ in pages]), generation=np.array("g1"))
    with open(os.path.join(focus, "focus.json"), "w") as f:
        json.dump({"generation": "g1", "sections": [], "site": {"focus": 0.5, "radius_median": 0.1, "radius_p90": 0.2}}, f)
    with open(os.path.join(tmp, "priority.txt"), "w") as f:
        f.write("\n".join(priority) + "\n")
    return crawl, focus


class TestPlanner(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def plan(self, crawl, focus, extra=()):
        out = os.path.join(self.tmp, "plan")
        run("link_plan", ["--crawl", crawl, "--focus", focus, "--priority", os.path.join(self.tmp, "priority.txt"),
                          "--out", out, "--no-recheck"] + list(extra))
        with open(os.path.join(out, "plan.json")) as f:
            return json.load(f), out

    SITE = [("https://x.test/", "Home", ""), ("https://x.test/p/", "Product", ""), ("https://x.test/a/", "Article", ""),
            ("https://x.test/=1+1/b/", "Odd", "")]
    VEC = [[1, 0.2, 0], [0.7, 0.5, 0.3], [0.8, 0.1, 0.3], [0.2, 1, 0]]
    EDGES = [("https://x.test/", "https://x.test/a/", 0), ("https://x.test/", "https://x.test/p/", 1),
             ("https://x.test/a/", "https://x.test/", 0), ("https://x.test/=1+1/b/", "https://x.test/", 0),
             ("https://x.test/", "https://x.test/=1+1/b/", 0)]

    def test_shares_are_normalised_and_add_up(self):  # N2
        crawl, focus = synthetic(self.tmp, self.SITE, self.EDGES, self.VEC, ["https://x.test/p/"])
        P, out = self.plan(crawl, focus)
        now = P["link_focus_score"]["now"]
        self.assertAlmostEqual(sum(s["lfs_contribution"] for s in P["sections"]), now, delta=0.05)
        with open(os.path.join(out, "pages.csv")) as f:
            pr = [float(r["pagerank"]) for r in csv.DictReader(f)]
        self.assertAlmostEqual(sum(pr), 1.0, places=2)
        self.assertLess(P["nofollow_model"]["retained_mass"]["now"], 1.0)  # the nofollow share is discarded
        for x in P["scenarios"]:
            self.assertLessEqual(x["priority"], 1.0)

    def test_scenario_sink_follows_the_scenario(self):  # N3
        lp = mod("link_plan")
        crawl, focus = synthetic(self.tmp, self.SITE, self.EDGES, self.VEC, ["https://x.test/p/"])
        calls, real = [], lp.pagerank

        def rec(n, src, dst, d=0.85, iters=100, w=None, tele=None, sink=None):
            calls.append((set(zip(np.asarray(src).tolist(), np.asarray(dst).tolist())), None if sink is None else sink.copy()))
            return real(n, src, dst, d, iters, w, tele, sink)
        lp.pagerank = rec
        try:
            self.plan(crawl, focus)
        finally:
            lp.pagerank = real
        # Node order follows pages.json: home is 0, /p/ is 1. Home's only
        # nofollowed link is to /p/. In every link state, either that pair is
        # followed (and is no sink) or it is not (and home carries the sink).
        self.assertGreaterEqual(len(calls), 5)
        for edges, sink in calls:
            if (0, 1) in edges:
                self.assertEqual(sink[0], 0.0)
            else:
                self.assertGreater(sink[0], 0.0)
        self.assertTrue(any((0, 1) in e for e, _ in calls))  # the parent-hub link is recommended

    def test_csv_sections_are_formula_safe(self):  # R4
        crawl, focus = synthetic(self.tmp, self.SITE, self.EDGES, self.VEC, ["https://x.test/p/"])
        _, out = self.plan(crawl, focus)
        with open(os.path.join(out, "pages.csv")) as f:
            secs = {r["section"] for r in csv.DictReader(f)}
        self.assertIn("'=1+1", secs)
        self.assertNotIn("=1+1", secs)

    def test_zero_vectors_are_left_out(self):  # R5
        vec = [v[:] for v in self.VEC]
        vec[2] = [0, 0, 0]
        crawl, focus = synthetic(self.tmp, self.SITE, self.EDGES, vec, ["https://x.test/p/"])
        P, out = self.plan(crawl, focus)
        with open(os.path.join(out, "plan.json")) as f:
            self.assertNotIn("NaN", f.read())
        self.assertEqual(P["pages"], 3)

    def test_cancelling_priority_centroid_stops(self):  # R5
        vec = [v[:] for v in self.VEC]
        vec[1], vec[2] = [0, 0, 1], [0, 0, -1]
        crawl, focus = synthetic(self.tmp, self.SITE, self.EDGES, vec, ["https://x.test/p/", "https://x.test/a/"])
        with self.assertRaises(SystemExit) as cm:
            self.plan(crawl, focus)
        self.assertIn("cancel", str(cm.exception))

    def test_original_start_url_is_a_priority_alias(self):  # N4, original finding 4
        site = [(u.replace("x.test", "www.x.test"), t, r) for u, t, r in self.SITE]
        edges = [(s.replace("x.test", "www.x.test"), t.replace("x.test", "www.x.test"), nf) for s, t, nf in self.EDGES]
        crawl, focus = synthetic(self.tmp, site, edges, self.VEC, ["https://x.test/", "https://www.x.test/p/"],
                                 meta={"origin_host": "www.x.test", "start": "https://x.test/", "start_final": "https://www.x.test/"})
        P, _ = self.plan(crawl, focus)
        self.assertEqual(P["priority_pages"], 2)

    def test_interrupted_embedding_generation_is_refused(self):  # R3
        crawl, focus = synthetic(self.tmp, self.SITE, self.EDGES, self.VEC, ["https://x.test/p/"])
        with open(os.path.join(focus, "focus.json")) as f:
            F = json.load(f)
        F["generation"] = "older"
        with open(os.path.join(focus, "focus.json"), "w") as f:
            json.dump(F, f)
        with self.assertRaises(SystemExit) as cm:
            self.plan(crawl, focus)
        self.assertIn("interrupted", str(cm.exception))

    def test_undefined_correlation_is_none(self):  # N7
        c = mod("link_plan")._corr
        self.assertIsNone(c([1, 1, 1], [1, 2, 3]))
        self.assertAlmostEqual(c([1, 2, 3], [2, 4, 6]), 1.0)

    def test_tiled_similar_pairs_match_brute_force(self):  # R6
        sp = mod("link_plan").similar_pairs
        rng = np.random.default_rng(1)
        V = rng.normal(size=(23, 4))
        V[5] = V[17] = V[3]  # exact duplicates
        V /= np.linalg.norm(V, axis=1, keepdims=True)
        idx = list(range(23))
        M = V @ V.T
        np.fill_diagonal(M, 0)
        want = sorted(((float(M[i, j]), i, j) for i in idx for j in idx if j > i and M[i, j] >= 0.5), reverse=True)[:7]
        has, top = sp(V, idx, 0.5, keep=7, block=4)
        self.assertEqual([(round(a, 6), i, j) for a, i, j in top], [(round(a, 6), i, j) for a, i, j in want])
        np.testing.assert_array_equal(has, (M >= 0.5).any(1))


class TestEmbeddingCache(unittest.TestCase):
    def test_reordered_pages_keep_their_own_vectors(self):  # R3
        sys.path.insert(0, os.path.dirname(__file__))
        import fixtures
        oll = fixtures.fake_ollama()
        out = tempfile.mkdtemp()
        try:
            src = os.path.join(out, "pages.jsonl")
            recs = [{"url": f"https://t.test/{i}/", "final": f"https://t.test/{i}/", "status": 200, "title": f"P{i}", "h1": "",
                     "text": f"topic {'garden widgets' if i % 2 else 'kitchen compost'} page {i}"} for i in range(4)]
            ol = f"http://127.0.0.1:{oll.server_port}"

            def vecs():
                with np.load(os.path.join(out, "vectors.npz")) as z:
                    return dict(zip([str(k) for k in z["keys"]], z["vecs"]))
            with open(src, "w") as f:
                f.write("".join(json.dumps(r) + "\n" for r in recs))
            run("site_focus", ["--pages", src, "--out", out, "--ollama", ol])
            a = vecs()
            with open(src, "w") as f:
                f.write("".join(json.dumps(r) + "\n" for r in reversed(recs)))
            run("site_focus", ["--pages", src, "--out", out, "--ollama", ol])
            b = vecs()
            for u in a:
                np.testing.assert_allclose(a[u], b[u])
            self.assertFalse(os.path.exists(os.path.join(out, "chunks.npy")))
        finally:
            oll.shutdown()
            shutil.rmtree(out, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()
