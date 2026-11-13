"""Regression tests for the findings of the November 2026 independent review.
Each test names the defect it guards against."""
import csv
import json
import os
import shutil
import stat
import tempfile
import threading
import time
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import numpy as np

import fixtures
from _helpers import mod, run

SITE = OLL = OFF = None
W = BASE = ""


class _Recorder(BaseHTTPRequestHandler):
    hits = []

    def log_message(self, *a):
        pass

    def do_GET(self):
        _Recorder.hits.append(self.path)
        self.send_response(200)
        self.send_header("Content-Type", "text/html")
        self.end_headers()
        self.wfile.write(b"<html><title>offsite</title><a href='/x'>x</a></html>")


def setUpModule():
    global SITE, OLL, OFF, W, BASE, S
    S = fixtures.Site()
    OFF = ThreadingHTTPServer(("127.0.0.1", 0), _Recorder)
    threading.Thread(target=OFF.serve_forever, daemon=True).start()
    S.offsite = f"http://localhost:{OFF.server_port}/"
    SITE = S.server()
    OLL = fixtures.fake_ollama()
    BASE = f"http://127.0.0.1:{SITE.server_port}"
    os.environ["SITEFOCUS_OLLAMA"] = f"http://127.0.0.1:{OLL.server_port}"
    W = tempfile.mkdtemp(prefix="sitefocus-review-")
    with open(os.path.join(W, "priority.txt"), "w") as f:
        f.write(f"{BASE}/products/widgets/\n{BASE}/products/industrial/\n")
    run("site_crawl", ["--start", f"{BASE}/", "--sitemap", f"{BASE}/sitemap.xml", "--out", f"{W}/crawl", "--delay", 0.05])
    global MAIN_REQUESTS
    MAIN_REQUESTS = list(S.requests)  # this crawl only; later tests crawl again
    run("site_focus", ["--pages", f"{W}/crawl/pages.jsonl", "--out", f"{W}/embed", "--ollama", os.environ["SITEFOCUS_OLLAMA"]])
    run("link_plan", ["--crawl", f"{W}/crawl", "--focus", f"{W}/embed", "--priority", f"{W}/priority.txt", "--out", f"{W}/plan",
                      "--module-section", "products"])
    run("focus_report", ["--focus", f"{W}/embed", "--plan", f"{W}/plan", "--site", "fixture", "--out", f"{W}/report/index.html"])


def tearDownModule():
    SITE.shutdown()
    OLL.shutdown()
    OFF.shutdown()
    shutil.rmtree(W, ignore_errors=True)


def pages():
    return {r["url"]: r for r in mod("common").read_pages(os.path.join(W, "crawl", "pages.jsonl"))}


class TestReportSafety(unittest.TestCase):
    def test_hostile_title_is_escaped(self):  # finding: HTML injection through table cells
        html = open(os.path.join(W, "report", "index.html"), encoding="utf-8").read()
        self.assertNotIn("<img src=x onerror", html)

    def test_markup_is_trusted_only_when_generated(self):
        fr = mod("focus_report")
        self.assertEqual(fr.cell("<b>x</b>"), "&lt;b&gt;x&lt;/b&gt;")
        self.assertEqual(fr.cell(mod("common").Markup("<b>x</b>")), "<b>x</b>")

    def test_regeneration_keeps_user_files(self):  # finding: rmtree of execution/
        keep = os.path.join(W, "report", "execution", "analyst-notes.txt")
        open(keep, "w").write("mine")
        run("focus_report", ["--focus", f"{W}/embed", "--plan", f"{W}/plan", "--site", "fixture", "--out", f"{W}/report/index.html"])
        self.assertTrue(os.path.exists(keep))

    def test_formula_cells_are_neutralised(self):  # finding: CSV formula injection
        rows = list(csv.DictReader(open(os.path.join(W, "plan", "pages.csv"))))
        titles = [r["title"] for r in rows if "HYPERLINK" in r["title"]]
        self.assertTrue(titles and all(t.startswith("'") for t in titles))


class TestCrawlScope(unittest.TestCase):
    def test_redirect_into_robots_disallowed_path_is_not_followed(self):  # finding: redirect hops escape robots
        self.assertFalse(any(p.startswith("/private/") for _, p in S.requests))
        self.assertEqual(pages()[f"{BASE}/go-blocked"].get("blocked_redirect"), f"{BASE}/private/secret")

    def test_redirect_to_another_host_is_not_followed(self):  # finding: redirects escape scope
        self.assertEqual(_Recorder.hits, [])

    def test_scheduler_spaces_workers(self):  # finding: per-worker throttling
        gate = mod("site_crawl").Gate(0.05)
        times, lock = [], threading.Lock()

        def worker():
            for _ in range(5):
                gate.wait()
                with lock:
                    times.append(time.time())
        ts = [threading.Thread(target=worker) for _ in range(3)]
        for t in ts:
            t.start()
        for t in ts:
            t.join()
        times.sort()
        gaps = [b - a for a, b in zip(times, times[1:])]
        self.assertGreaterEqual(min(gaps), 0.045)  # released one at a time, 0.05 s apart

    def test_crawl_rate_is_limited(self):  # end to end: 0.05 s schedule means at most ~20 requests a second
        t = sorted(x for x, p in MAIN_REQUESTS)
        rate = (len(t) - 1) / (t[-1] - t[0])
        self.assertLessEqual(rate, 22)

    def test_paragraph_with_inline_markup_is_one_block(self):  # finding: parser splits paragraphs
        text = pages()[f"{BASE}/odd/inline/"]["text"]
        self.assertIn("Compare our garden widgets before buying a replacement for your garden widget pricing page today.", text.split("\n"))

    def test_declared_charset_is_used(self):  # finding: everything decoded as UTF-8
        self.assertEqual(pages()[f"{BASE}/odd/latin1/"]["title"], "Café widgets")

    def test_base_href_is_honoured(self):  # finding: <base href> ignored
        links = [l[0] for l in pages()[f"{BASE}/odd/base/"]["links"]]
        self.assertIn(f"{BASE}/blog/2/", links)

    def test_large_page_is_bounded(self):  # finding: unbounded reads
        big = pages()[f"{BASE}/odd/big/"]
        self.assertTrue(big.get("truncated"))
        self.assertLessEqual(len(big["text"]), 40000)

    def test_norm_root_with_query(self):  # finding: query normalisation
        norm = mod("site_crawl").norm
        self.assertEqual(norm("https://Example.test?x=1", True), "https://example.test/?x=1")
        self.assertEqual(norm("https://example.test?x=1", False), "https://example.test/")


class TestCrawlLifecycle(unittest.TestCase):
    def test_start_redirect_to_another_host_crawls_the_site(self):  # finding: apex to www
        class R(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def do_GET(self):
                self.send_response(301)
                self.send_header("Location", BASE + self.path)
                self.end_headers()
        red = ThreadingHTTPServer(("localhost", 0), R)
        threading.Thread(target=red.serve_forever, daemon=True).start()
        out = tempfile.mkdtemp()
        try:
            run("site_crawl", ["--start", f"http://localhost:{red.server_port}/", "--out", out, "--max-pages", 6, "--delay", 0])
            got = mod("common").read_pages(os.path.join(out, "pages.jsonl"))
            self.assertGreaterEqual(len(got), 5)
            self.assertEqual(json.load(open(os.path.join(out, "meta.json")))["origin_host"], "127.0.0.1")
        finally:
            red.shutdown()
            shutil.rmtree(out, ignore_errors=True)

    def test_fresh_recrawls_and_keeps_previous(self):  # finding: reruns never recrawl
        out = tempfile.mkdtemp()
        try:
            run("site_crawl", ["--start", f"{BASE}/", "--out", out, "--max-pages", 3, "--delay", 0])
            n0 = len(S.requests)
            run("site_crawl", ["--start", f"{BASE}/", "--out", out, "--max-pages", 3, "--delay", 0, "--fresh"])
            self.assertGreaterEqual(len(S.requests) - n0, 4)  # robots + start + pages again
            self.assertTrue(any(d.startswith("previous-") for d in os.listdir(out)))
        finally:
            shutil.rmtree(out, ignore_errors=True)

    def test_failed_pages_are_retried_and_tail_repaired(self):  # findings: permanent failures, truncated JSONL
        out = tempfile.mkdtemp()
        try:
            with open(os.path.join(out, "pages.jsonl"), "w") as f:
                f.write(json.dumps({"url": f"{BASE}/", "status": 503}) + "\n" + '{"url":')
            run("site_crawl", ["--start", f"{BASE}/", "--out", out, "--max-pages", 2, "--delay", 0])
            got = {r["url"]: r for r in mod("common").read_pages(os.path.join(out, "pages.jsonl"))}
            self.assertEqual(got[f"{BASE}/"]["status"], 200)
            self.assertTrue(os.path.exists(os.path.join(out, "pages.jsonl.damaged")))
        finally:
            shutil.rmtree(out, ignore_errors=True)

    def test_robots_server_error_stops_the_crawl(self):  # finding: robots 5xx treated as allow-all
        s2 = fixtures.Site(robots_status=503)
        srv = s2.server()
        out = tempfile.mkdtemp()
        try:
            with self.assertRaises(SystemExit):
                run("site_crawl", ["--start", f"http://127.0.0.1:{srv.server_port}/", "--out", out, "--delay", 0])
            self.assertEqual([p for _, p in s2.requests if p != "/robots.txt"], [])
        finally:
            srv.shutdown()
            shutil.rmtree(out, ignore_errors=True)


class TestEmbedding(unittest.TestCase):
    def test_changed_text_is_reembedded(self):  # finding: cache keyed by URL, not content
        out = tempfile.mkdtemp()
        try:
            src = os.path.join(out, "pages.jsonl")
            recs = [{"url": f"https://t.test/{i}/", "final": f"https://t.test/{i}/", "status": 200, "title": f"P{i}",
                     "h1": "", "text": f"garden widgets page number {i} with pricing"} for i in range(4)]
            open(src, "w").write("".join(json.dumps(r) + "\n" for r in recs))
            run("site_focus", ["--pages", src, "--out", out, "--ollama", os.environ["SITEFOCUS_OLLAMA"]])
            v1 = np.load(os.path.join(out, "vectors.npy"))
            recs[0]["text"] = "compost heaps and kitchen scraps instead"
            open(src, "w").write("".join(json.dumps(r) + "\n" for r in recs))
            run("site_focus", ["--pages", src, "--out", out, "--ollama", os.environ["SITEFOCUS_OLLAMA"]])
            v2 = np.load(os.path.join(out, "vectors.npy"))
            self.assertFalse(np.allclose(v1[0], v2[0]))
            np.testing.assert_allclose(v1[1:], v2[1:])
        finally:
            shutil.rmtree(out, ignore_errors=True)

    def test_tiny_and_duplicate_sites_do_not_crash(self):  # finding: k-means NaN
        km = mod("site_focus").kmeans
        km(np.array([[1.0, 0.0]]), 12)
        km(np.array([[1.0, 0.0], [1.0, 0.0]]), 2)

    def test_all_noindex_explains(self):  # finding: crash on empty indexable set
        out = tempfile.mkdtemp()
        try:
            src = os.path.join(out, "pages.jsonl")
            open(src, "w").write("".join(json.dumps({"url": f"https://t.test/{i}/", "status": 200, "robots": "noindex", "title": "x",
                                                     "h1": "", "text": "words here"}) + "\n" for i in range(3)))
            with self.assertRaises(SystemExit) as cm:
                run("site_focus", ["--pages", src, "--out", out, "--ollama", os.environ["SITEFOCUS_OLLAMA"]])
            self.assertIn("indexable", str(cm.exception))
        finally:
            shutil.rmtree(out, ignore_errors=True)


class TestModel(unittest.TestCase):
    def test_edgeless_graph_returns_teleport(self):  # finding: empty edge arrays crash
        pr = mod("link_plan").pagerank(3, np.array([]), np.array([]))
        np.testing.assert_allclose(pr, 1 / 3)

    def test_nofollow_share_is_discarded(self):  # finding: link-level nofollow counted as passing
        lp = mod("link_plan")
        followed = lp.pagerank(3, np.array([0, 0]), np.array([1, 2]))
        sink = np.zeros(3)
        sink[0] = 1.0  # page 0 also has one nofollowed link
        with_nf = lp.pagerank(3, np.array([0]), np.array([1]), sink=sink)
        self.assertLess(with_nf[1], followed[1] + 1e-12)  # half of page 0's share is discarded, not passed on
        self.assertLess(with_nf.sum(), 1.0)

    def test_nofollow_pages_are_never_link_sources(self):  # finding: nofollow pages as donors
        adds = list(csv.DictReader(open(os.path.join(W, "plan", "link_adds.csv"))))
        self.assertFalse(any(r["source"] == f"{BASE}/blog/4/" for r in adds))

    def test_pick_site_respects_boundaries(self):  # finding: lookalike GSC property
        g = mod("gsc")
        avail = ["https://example.com/", "https://example.com/shop/", "sc-domain:example.com"]
        self.assertIsNone(g.pick_site("https://example.com.evil.test/", avail))
        self.assertEqual(g.pick_site("https://example.com/shopping/", avail), "https://example.com/")
        self.assertEqual(g.pick_site("https://example.com/shop/a", avail), "https://example.com/shop/")

    def test_traffic_uses_ctr_change_and_real_gainers(self):  # finding: traffic confounded and overstated
        tw = mod("link_plan").traffic_whatif
        pos = np.array([1, 1, 1, 2, 2, 2, 3, 3, 3, 4, 4, 4, 5, 5, 5, 6, 6, 6, 10.0], float)
        impr = np.full(len(pos), 100.0)
        ctr = {1: 0.30, 2: 0.20, 3: 0.12, 4: 0.12, 5: 0.12, 6: 0.12}  # flat from position 3
        clicks = np.array([impr[i] * ctr.get(int(p), 0.01) for i, p in enumerate(pos)])
        clicks[-1] = 1.0  # a page with low CTR at position 10 (outside the curve)
        before = np.ones(len(pos))
        after = before.copy()
        after[-1] = 2.0  # only the last page gains PageRank
        r = tw(impr, clicks, pos, before, after)
        self.assertTrue(r["available"])
        self.assertEqual(r["pages"], 1)
        self.assertEqual(r["gain_1"], 0)  # flat curve: moving 10 -> 9 changes nothing
        after2 = before.copy()
        after2[3] = 2.0  # a page at position 2 gains
        r2 = tw(impr, clicks, pos, before, after2)
        self.assertEqual(r2["gain_1"], 10)  # 100 x (0.30 - 0.20)
        self.assertEqual(tw(impr, clicks, pos, before, before)["pages"], 0)  # unchanged pages never count

    def test_constant_log_chart(self):  # finding: zero division on equal log bounds
        self.assertTrue(mod("charts").dumbbell([("a", 0.1, 0.1, 0.1)], "t", log=True))


class TestCredentials(unittest.TestCase):
    def test_existing_token_file_becomes_private(self):  # finding: 0644 kept on login
        d = tempfile.mkdtemp()
        try:
            p = os.path.join(d, "gsc.json")
            open(p, "w").write("{}")
            os.chmod(p, 0o644)
            mod("common").write_private(p, '{"refresh_token": "x"}')
            self.assertEqual(stat.S_IMODE(os.stat(p).st_mode), 0o600)
            os.remove(p)
            os.symlink(os.path.join(d, "elsewhere"), p)
            with self.assertRaises(SystemExit):
                mod("common").write_private(p, "{}")
        finally:
            shutil.rmtree(d, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()
