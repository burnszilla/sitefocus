"""End to end: crawl a local fixture site, embed with a fake Ollama, plan and
report, and check every case the tool must get right."""
import csv
import json
import os
import shutil
import tempfile
import unittest

import fixtures
from _helpers import run

WORK = None
SITE = OLL = None
BASE = ""


def setUpModule():
    global WORK, SITE, OLL, BASE
    SITE = fixtures.Site().server()
    OLL = fixtures.fake_ollama()
    BASE = f"http://127.0.0.1:{SITE.server_port}"
    os.environ["SITEFOCUS_OLLAMA"] = f"http://127.0.0.1:{OLL.server_port}"
    WORK = tempfile.mkdtemp(prefix="sitefocus-test-")
    with open(os.path.join(WORK, "priority.txt"), "w") as f:
        f.write(f"{BASE}/products/widgets/\n{BASE}/products/industrial/\n")
    with open(os.path.join(WORK, "gsc.csv"), "w") as f:
        f.write("Top pages,Clicks,Impressions,CTR,Position\n"
                f"{BASE}/products/widgets/,40,900,4.4%,6.1\n{BASE}/blog/2/,3,300,1%,12.0\n")
    run("site_crawl", ["--start", f"{BASE}/", "--sitemap", f"{BASE}/sitemap.xml", "--out", f"{WORK}/crawl",
                       "--workers", 2, "--delay", 0])
    run("site_focus", ["--pages", f"{WORK}/crawl/pages.jsonl", "--out", f"{WORK}/embed", "--k", 3,
                       "--ollama", os.environ["SITEFOCUS_OLLAMA"]])
    run("link_plan", ["--crawl", f"{WORK}/crawl", "--focus", f"{WORK}/embed", "--priority", f"{WORK}/priority.txt",
                      "--gsc", f"{WORK}/gsc.csv", "--out", f"{WORK}/plan"])
    run("focus_report", ["--focus", f"{WORK}/embed", "--plan", f"{WORK}/plan", "--site", "fixture",
                         "--out", f"{WORK}/report/index.html"])


def tearDownModule():
    SITE.shutdown()
    OLL.shutdown()
    shutil.rmtree(WORK, ignore_errors=True)


def crawled():
    return {json.loads(l)["url"]: json.loads(l) for l in open(os.path.join(WORK, "crawl", "pages.jsonl"))}


class TestCrawl(unittest.TestCase):
    def test_sitemap_only_page_is_found(self):
        self.assertIn(f"{BASE}/products/industrial/", crawled())

    def test_robots_disallow_is_obeyed(self):
        self.assertFalse(any("/private/" in u for u in crawled()))

    def test_javascript_only_page_is_not_discovered(self):
        self.assertNotIn(f"{BASE}/js-only/", crawled())

    def test_redirect_records_final_url(self):
        self.assertEqual(crawled()[f"{BASE}/old-widgets"]["final"], f"{BASE}/products/widgets/")

    def test_rate_limited_page_is_retried(self):
        self.assertEqual(crawled()[f"{BASE}/flaky/"]["status"], 200)

    def test_root_is_one_page(self):
        pages = json.load(open(os.path.join(WORK, "embed", "pages.json")))
        roots = [p for p in pages if p["url"].rstrip("/") == BASE]
        self.assertEqual(len(roots), 1)


class TestIndexability(unittest.TestCase):
    def setUp(self):
        self.p = {x["url"]: x for x in json.load(open(os.path.join(WORK, "embed", "pages.json")))}

    def test_meta_noindex(self):
        self.assertFalse(self.p[f"{BASE}/search/"]["indexable"])

    def test_x_robots_tag_noindex(self):
        self.assertFalse(self.p[f"{BASE}/blog/6/"]["indexable"])

    def test_page_level_nofollow(self):
        self.assertTrue(self.p[f"{BASE}/blog/4/"]["nofollow_page"])


class TestPlan(unittest.TestCase):
    def setUp(self):
        self.P = json.load(open(os.path.join(WORK, "plan", "plan.json")))

    def test_orphan_ignores_links_from_nofollow_pages(self):
        orph = [o["url"] for o in self.P["orphaned_priority"]]
        self.assertIn(f"{BASE}/products/industrial/", orph)

    def test_orphan_gets_links_and_reach(self):
        I = self.P["impact"]
        self.assertGreaterEqual(I["orphans"][0], 1)
        self.assertEqual(I["orphans"][1], 0)
        self.assertGreaterEqual(I["priority_reach_min"][1], I["priority_reach_min"][0])
        adds = list(csv.DictReader(open(os.path.join(WORK, "plan", "link_adds.csv"))))
        self.assertTrue(any(r["target"] == f"{BASE}/products/industrial/" for r in adds))

    def test_hygiene_redirect_and_broken(self):
        h = {r["target"]: r for r in csv.DictReader(open(os.path.join(WORK, "plan", "hygiene.csv")))}
        self.assertEqual(h[f"{BASE}/old-widgets"]["issue"], "links to a redirect")
        self.assertEqual(h[f"{BASE}/old-widgets"]["points_to"], f"{BASE}/products/widgets/")
        self.assertEqual(h[f"{BASE}/missing-page"]["issue"], "links to a broken page")
        self.assertEqual(h[f"{BASE}/missing-page"]["rechecked_live"], "1")

    def test_flaky_page_is_not_reported_broken(self):
        h = {r["target"]: r for r in csv.DictReader(open(os.path.join(WORK, "plan", "hygiene.csv")))}
        self.assertNotIn(f"{BASE}/flaky/", h)

    def test_scores_are_in_range(self):
        for x in self.P["scenarios"]:
            self.assertTrue(0 <= x["lfs"] <= 100)
            self.assertTrue(0 <= x["priority"] <= 1)

    def test_traffic_needs_enough_data(self):
        t = self.P["impact"]["traffic"]
        self.assertFalse(t["available"])
        self.assertIn("not enough", t["reason"])

    def test_every_add_has_an_instruction(self):
        for r in csv.DictReader(open(os.path.join(WORK, "plan", "link_adds.csv"))):
            self.assertTrue(r["instruction"])
            self.assertTrue(r["suggested_anchor"])


class TestReport(unittest.TestCase):
    def test_sections_and_files(self):
        html = open(os.path.join(WORK, "report", "index.html"), encoding="utf-8").read()
        for s in ("Estimated impact if every change ships", "How to execute, in order", "Link hygiene",
                  "Where nofollow belongs", "Reach:", "pagemap"):
            self.assertIn(s, html)
        ex = os.listdir(os.path.join(WORK, "report", "execution"))
        self.assertTrue(any("fix-link-hygiene" in f for f in ex))
        self.assertTrue(any("orphaned-priority" in f for f in ex))

    def test_titles_are_escaped(self):
        html = open(os.path.join(WORK, "report", "index.html"), encoding="utf-8").read()
        self.assertNotIn("<script>alert", html)


if __name__ == "__main__":
    unittest.main()
