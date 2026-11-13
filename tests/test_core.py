"""Unit tests: the maths and the parsers, checked against independent
implementations and real export formats."""
import csv
import os
import random
import tempfile
import unittest
from collections import deque

import numpy as np

from _helpers import mod

lp = mod("link_plan")


def dense_pagerank(n, edges, w=None, tele=None, d=0.85, iters=500):
    """Independent reference: dense transition matrix, power iteration."""
    w = [1.0] * len(edges) if w is None else w
    tele = np.full(n, 1 / n) if tele is None else np.asarray(tele, float)
    M = np.zeros((n, n))
    for (s, t), x in zip(edges, w):
        M[t, s] += x
    col = M.sum(0)
    dangling = col == 0
    M[:, ~dangling] /= col[~dangling]
    pr = tele.copy()
    for _ in range(iters):
        pr = (1 - d) * tele + d * (M @ pr + pr[dangling].sum() * tele)
    return pr


class TestPageRank(unittest.TestCase):
    def setUp(self):
        random.seed(4)
        self.n = 60
        self.edges = list({(random.randrange(self.n), random.randrange(self.n)) for _ in range(240)})
        self.edges = [e for e in self.edges if e[0] != e[1]]

    def test_matches_dense_reference(self):
        s = np.array([e[0] for e in self.edges])
        t = np.array([e[1] for e in self.edges])
        np.testing.assert_allclose(lp.pagerank(self.n, s, t), dense_pagerank(self.n, self.edges), atol=1e-9)

    def test_weighted_and_personalised(self):
        s = np.array([e[0] for e in self.edges])
        t = np.array([e[1] for e in self.edges])
        w = [random.choice([0.5, 1.0]) for _ in self.edges]
        tele = np.random.default_rng(1).random(self.n)
        tele /= tele.sum()
        np.testing.assert_allclose(lp.pagerank(self.n, s, t, w=w, tele=tele),
                                   dense_pagerank(self.n, self.edges, w=w, tele=tele), atol=1e-9)

    def test_sums_to_one_and_cycle_is_uniform(self):
        pr = lp.pagerank(4, np.array([0, 1, 2, 3]), np.array([1, 2, 3, 0]))
        self.assertAlmostEqual(pr.sum(), 1.0)
        np.testing.assert_allclose(pr, 0.25, atol=1e-12)


class TestHarmonic(unittest.TestCase):
    def test_matches_brute_force(self):
        random.seed(9)
        n = 140  # more than two 64-source batches
        edges = [e for e in {(random.randrange(n), random.randrange(n)) for _ in range(500)} if e[0] != e[1]]
        hc = lp.harmonic(n, [e[0] for e in edges], [e[1] for e in edges])
        adj = [[] for _ in range(n)]
        for a, b in edges:
            adj[a].append(b)
        ref = np.zeros(n)
        for s in range(n):
            dist = {s: 0}
            q = deque([s])
            while q:
                x = q.popleft()
                for y in adj[x]:
                    if y not in dist:
                        dist[y] = dist[x] + 1
                        q.append(y)
            for v, k in dist.items():
                if k:
                    ref[v] += 1 / k
        np.testing.assert_allclose(hc, ref / (n - 1), atol=1e-12)

    def test_unreachable_is_zero(self):
        hc = lp.harmonic(3, [0], [1])
        self.assertEqual(hc[2], 0.0)
        self.assertAlmostEqual(hc[1], 0.5)


class TestPopcount(unittest.TestCase):
    def test_fallback_matches(self):
        a = np.array([0, 1, 3, 2 ** 63, 2 ** 64 - 1, 123456789], dtype=np.uint64)
        expect = [bin(int(x)).count("1") for x in a]
        self.assertEqual(list(lp._popcount(a, fallback=True)), expect)
        self.assertEqual(list(lp._popcount(a)), expect)


class TestPercentile(unittest.TestCase):
    def test_ties_share_a_rank(self):
        p = lp.pctile(np.array([1.0, 2.0, 2.0, 3.0]))
        self.assertEqual(p[1], p[2])
        self.assertEqual(p[0], 0.0)
        self.assertEqual(p[3], 100.0)


class TestParsers(unittest.TestCase):
    def write(self, text):
        f = tempfile.NamedTemporaryFile("w", suffix=".csv", delete=False, encoding="utf-8")
        f.write(text)
        f.close()
        self.addCleanup(os.remove, f.name)
        return f.name

    def test_gsc_pages_export(self):
        # Search Console Performance > Pages export (UTF-8 BOM, % CTR, thousands separators).
        p = self.write("﻿Top pages,Clicks,Impressions,CTR,Position\n"
                       "https://www.example.com/,\"1,204\",\"88,310\",1.36%,3.2\n"
                       "https://www.example.com/a/,7,410,1.7%,11.4\n")
        g = lp.load_gsc(p, {})
        self.assertEqual(g["https://www.example.com/"]["clicks"], 1204)
        self.assertEqual(g["https://www.example.com/a"]["impressions"], 410)
        self.assertAlmostEqual(g["https://www.example.com/a/"]["position"], 11.4)

    def test_gsc_rejects_wrong_file(self):
        p = self.write("Query,Clicks\nwidgets,4\n")
        with self.assertRaises(SystemExit):
            lp.load_gsc(p, {})

    def test_backlinks_search_console(self):
        p = self.write("Target page,Incoming links,Linking sites\nhttps://www.example.com/a/,120,14\n")
        self.assertEqual(lp.load_backlinks(p, {})["https://www.example.com/a/"], 14)

    def test_backlinks_ahrefs(self):
        p = self.write("Page URL,Referring domains,Backlinks\nhttps://www.example.com/b/,9,300\n")
        self.assertEqual(lp.load_backlinks(p, {})["https://www.example.com/b/"], 9)


class TestPlacement(unittest.TestCase):
    PARA = ("Expert Full Service Business is designed for small business owners that must file their taxes "
            "as a partnership, S corporation, or multi-member LLC. You can be")

    def test_clean_title(self):
        self.assertEqual(lp.clean_title("Married Filing Jointly vs Separately: How Should You File? - Brand Tax Tips & Videos"),
                         "Married Filing Jointly vs Separately: How Should You File?")
        self.assertEqual(lp.clean_title("Widgets® | Example"), "Widgets")

    def test_finds_specific_anchor(self):
        idf = {"corporation": 3.0, "partnership": 3.0, "taxes": 0.1, "business": 0.2}
        para, anchor, _ = lp.place_link([self.PARA], {"corporation", "taxes"}, idf, {"corporation"})
        self.assertEqual(anchor, "S corporation")

    def test_never_spans_a_sentence_or_starts_on_filler(self):
        idf = {"llc": 3.0, "multi-member": 3.0}
        _, anchor, _ = lp.place_link([self.PARA], {"llc", "multi-member"}, idf, {"llc", "multi-member"})
        self.assertNotIn("You", anchor)
        self.assertFalse(anchor.lower().startswith(("to ", "or ", "the ")))

    def test_generic_overlap_is_not_a_place(self):
        para, anchor, _ = lp.place_link([self.PARA], {"taxes", "business"}, {"taxes": 0.1, "business": 0.1}, {"corporation"})
        self.assertEqual((para, anchor), ("", ""))

    def test_headings_are_not_paragraphs(self):
        para, _, _ = lp.place_link(["Garden Widgets 2026 | Pricing and Plans for Every Size of Garden"], {"widgets"}, {"widgets": 2.0}, {"widgets"})
        self.assertEqual(para, "")


if __name__ == "__main__":
    unittest.main()
