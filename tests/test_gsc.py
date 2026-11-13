"""Search Console connection: credentials, property choice, paginated pull,
and that the pulled file reads exactly like a manual export."""
import json
import os
import shutil
import tempfile
import unittest

import fixtures

G = None
TMP = None


def setUpModule():
    global G, TMP
    G = fixtures.fake_google()
    TMP = tempfile.mkdtemp(prefix="sitefocus-gsc-")
    base = f"http://127.0.0.1:{G.server_port}"
    os.environ["SITEFOCUS_GOOGLE_TOKEN_URL"] = base + "/token"
    os.environ["SITEFOCUS_GSC_API"] = base + "/webmasters/v3"
    os.environ["SITEFOCUS_CONFIG_DIR"] = TMP
    os.environ["SITEFOCUS_ADC"] = os.path.join(TMP, "no-adc.json")


def tearDownModule():
    G.shutdown()
    shutil.rmtree(TMP, ignore_errors=True)


def gsc():
    from _helpers import mod
    return mod("gsc")


class TestGsc(unittest.TestCase):
    def setUp(self):
        self.g = gsc()
        if os.path.exists(self.g.STORE):
            os.remove(self.g.STORE)

    def save_creds(self):
        with open(self.g.STORE, "w") as f:
            json.dump({"client_id": "id", "client_secret": "secret", "refresh_token": "rt"}, f)

    def test_not_connected_explains_how(self):
        self.assertIsNone(self.g.credentials())
        self.assertEqual(self.g.status(), "not connected")
        with self.assertRaises(SystemExit) as cm:
            self.g.access_token()
        self.assertIn("gsc-login", str(cm.exception))

    def test_pick_site(self):
        avail = ["sc-domain:example.com", "https://shop.example.com/", "https://other.org/"]
        self.assertEqual(self.g.pick_site("https://shop.example.com/", avail), "https://shop.example.com/")
        self.assertEqual(self.g.pick_site("https://www.example.com/", avail), "sc-domain:example.com")
        self.assertIsNone(self.g.pick_site("https://nothing.net/", avail))

    def test_pull_paginates_and_reads_like_an_export(self):
        self.save_creds()
        self.assertIn("connected via sitefocus", self.g.status())
        out = os.path.join(TMP, "pages.csv")
        info = self.g.pull_pages("sc-domain:example.com", days=30, out=out, host="www.example.com")
        self.assertEqual(info["pages"], 30000)
        self.assertEqual([q["startRow"] for q in G.state["queries"][-2:]], [0, 25000])
        self.assertEqual(G.state["queries"][-1]["dimensionFilterGroups"][0]["filters"][0]["expression"], "://www.example.com/")
        from _helpers import mod
        parsed = mod("link_plan").load_gsc(out, {})
        self.assertEqual(parsed["https://www.example.com/p0/"]["clicks"], 2)
        self.assertAlmostEqual(parsed["https://www.example.com/p0/"]["position"], 7.5)

    def test_wrong_scope_is_rejected(self):
        self.save_creds()
        g2 = fixtures.fake_google(scope="https://www.googleapis.com/auth/cloud-platform")
        old = self.g.TOKEN_URL
        self.g.TOKEN_URL = f"http://127.0.0.1:{g2.server_port}/token"
        try:
            with self.assertRaises(SystemExit) as cm:
                self.g.access_token()
            self.assertIn("do not include Search Console", str(cm.exception))
        finally:
            self.g.TOKEN_URL = old
            g2.shutdown()


if __name__ == "__main__":
    unittest.main()
