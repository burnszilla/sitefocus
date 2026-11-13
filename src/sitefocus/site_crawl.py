#!/usr/bin/env python3
"""Polite same-host crawler for site-focus and internal-link work. Standard
library only. Resumable: rerun with the same --out to continue.

  site_crawl.py --start https://www.example.com/ --sitemap URL [--sitemap URL ...] \
      --out work/ [--max-pages 8000] [--workers 2] [--delay 0.5]

Seeds from the start URL plus every <loc> in the given sitemaps (sitemap
indexes are followed), then follows internal links. Obeys robots.txt for the
User-Agent below. Skips URLs with query strings unless --keep-query.

Writes:
  out/pages.jsonl  one line per fetched URL: url, status, final, canonical,
                   robots, title, h1, words, text (main content, nav/header/
                   footer stripped where marked up, blocks joined by newlines, first 40,000
                   chars; strip site-wide boilerplate downstream), links:[[href, zone, anchor]]
                   zone is body | nav | header | footer
  out/edges.csv    source,target,zone,anchor (internal links only, deduplicated)
"""
import argparse
import csv
import gzip
import json
import os
import re
import threading
import time
import urllib.request
import urllib.robotparser
from collections import deque
from html.parser import HTMLParser
from urllib.parse import urljoin, urldefrag, urlsplit

# Identify yourself: SITEFOCUS_CONTACT=you@example.com
_CONTACT = os.environ.get("SITEFOCUS_CONTACT") or os.environ.get("CCRA_CONTACT")
UA = "Mozilla/5.0 (compatible; sitefocus/1.0" + (f"; +mailto:{_CONTACT}" if _CONTACT else "") + ")"
SKIP_EXT = re.compile(r"\.(pdf|jpe?g|png|gif|svg|webp|zip|mp4|mp3|css|js|xml|ico|woff2?|ttf|xlsx?|docx?)$", re.I)
ZONE_TAGS = {"nav": "nav", "header": "header", "footer": "footer"}
ZONE_HINT = re.compile(r"(^|[-_ ])(nav|navbar|menu|megamenu|header|footer|breadcrumb)", re.I)
DROP_TAGS = {"script", "style", "noscript", "svg", "template"}


class Page(HTMLParser):
    def __init__(self, base):
        super().__init__(convert_charrefs=True)
        self.base = base
        self.stack = []  # (tag, zone)
        self.text, self.links = [], []
        self.title = self.h1 = ""
        self.canonical = self.robots = ""
        self._a = None
        self._in = {"title": False, "h1": False}

    def zone(self):
        for _, z in reversed(self.stack):
            if z:
                return z
        return "body"

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        z = ZONE_TAGS.get(tag)
        if not z:
            hint = " ".join([a.get("class") or "", a.get("id") or "", a.get("role") or ""])
            m = ZONE_HINT.search(hint)
            if m and tag in ("div", "ul", "section", "aside"):
                z = {"navbar": "nav", "menu": "nav", "megamenu": "nav", "breadcrumb": "nav"}.get(m.group(2).lower(), m.group(2).lower())
            if a.get("role") == "navigation":
                z = "nav"
        if tag not in ("br", "img", "meta", "link", "input", "hr", "source"):
            self.stack.append((tag, z))
        if tag == "meta" and (a.get("name") or "").lower() == "robots":
            self.robots = a.get("content") or ""
        if tag == "link" and "canonical" in (a.get("rel") or "").lower():
            self.canonical = urljoin(self.base, a.get("href") or "")
        if tag == "a" and a.get("href"):
            self._a = [urljoin(self.base, a["href"]), self.zone(), [], (a.get("rel") or "")]
        if tag in self._in:
            self._in[tag] = True

    def handle_endtag(self, tag):
        if tag == "a" and self._a:
            href, zone, anchor, rel = self._a
            self.links.append([href, zone, " ".join(" ".join(anchor).split())[:120], rel])
            self._a = None
        if tag in self._in:
            self._in[tag] = False
        for i in range(len(self.stack) - 1, -1, -1):
            if self.stack[i][0] == tag:
                del self.stack[i:]
                break

    def handle_data(self, data):
        if any(t in DROP_TAGS for t, _ in self.stack):
            return
        if self._in["title"]:
            self.title += data
        if self._in["h1"]:
            self.h1 += data
        if self._a is not None:
            self._a[2].append(data)
        if self.zone() == "body" and data.strip():
            self.text.append(data.strip())


def norm(u, keep_query):
    u = urldefrag(u)[0]
    p = urlsplit(u)
    if p.scheme not in ("http", "https"):
        return None
    if p.query and not keep_query:
        u = u.split("?")[0]
    if not urlsplit(u).path:
        u += "/"  # https://example.com and https://example.com/ are the same page
    return u


def sitemap_urls(url, seen=None):
    seen = seen or set()
    if url in seen:
        return []
    seen.add(url)
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept-Encoding": "gzip"})
    with urllib.request.urlopen(req, timeout=60) as r:
        body = r.read()
        if r.headers.get("Content-Encoding") == "gzip" or body[:2] == b"\x1f\x8b":
            body = gzip.decompress(body)
    locs = re.findall(rb"<loc>\s*([^<\s]+)\s*</loc>", body)
    out = []
    for l in locs:
        l = l.decode().replace("&amp;", "&")
        if b"<sitemapindex" in body[:2000] or l.endswith(".xml") and "sitemap" in l:
            out += sitemap_urls(l, seen)
        else:
            out.append(l)
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--start", required=True)
    ap.add_argument("--sitemap", action="append", default=[])
    ap.add_argument("--out", required=True)
    ap.add_argument("--max-pages", type=int, default=8000)
    ap.add_argument("--workers", type=int, default=2)
    ap.add_argument("--delay", type=float, default=0.5)
    ap.add_argument("--keep-query", action="store_true")
    a = ap.parse_args()
    os.makedirs(a.out, exist_ok=True)
    host = urlsplit(a.start).hostname
    # Fetch robots.txt with our own User-Agent: RobotFileParser.read() uses
    # Python's default UA, which CDNs often answer with 403, and it then
    # treats the whole site as disallowed.
    rp = urllib.robotparser.RobotFileParser()
    robots_url = f"{urlsplit(a.start).scheme}://{host}/robots.txt"
    try:
        req = urllib.request.Request(robots_url, headers={"User-Agent": UA})
        with urllib.request.urlopen(req, timeout=30) as r:
            rp.parse(r.read().decode("utf-8", "replace").splitlines())
    except urllib.error.HTTPError as ex:
        if ex.code in (401, 403):
            raise SystemExit(f"robots.txt returned {ex.code} for our User-Agent; the site is blocking this crawler. Stopping.")
        rp.parse([])  # 404 and similar: no rules, everything allowed
    except Exception as ex:
        raise SystemExit(f"could not fetch {robots_url}: {ex}")

    pages_path = os.path.join(a.out, "pages.jsonl")
    done = set()
    if os.path.exists(pages_path):
        for line in open(pages_path):
            try:
                done.add(json.loads(line)["url"])
            except Exception:
                pass

    queue, queued = deque(), set()

    def push(u):
        u = norm(u, a.keep_query)
        if not u or u in queued or urlsplit(u).hostname != host or SKIP_EXT.search(urlsplit(u).path):
            return
        if not rp.can_fetch(UA, u):
            return
        queued.add(u)
        queue.append(u)

    push(a.start)
    for sm in a.sitemap:
        try:
            got = sitemap_urls(sm)
        except Exception as ex:
            print(f"warning: sitemap {sm} could not be read ({ex}); continuing without it", flush=True)
            continue
        print(f"sitemap {sm}: {len(got)} URLs", flush=True)
        for u in got:
            push(u)
    # Re-seed links from pages already fetched (resume).
    if done:
        for line in open(pages_path):
            try:
                for l in json.loads(line)["links"]:
                    push(l[0])
            except Exception:
                pass

    lock = threading.Lock()
    out = open(pages_path, "a")
    count = [len(done)]

    def worker():
        while True:
            with lock:
                while queue and queue[0] in done:
                    queue.popleft()
                if not queue or count[0] >= a.max_pages:
                    return
                u = queue.popleft()
                done.add(u)
                count[0] += 1
            rec = {"url": u}
            for attempt in range(3):
                rec = {"url": u}
                try:
                    req = urllib.request.Request(u, headers={"User-Agent": UA, "Accept": "text/html", "Accept-Encoding": "gzip"})
                    with urllib.request.urlopen(req, timeout=40) as r:
                        rec["status"] = r.status
                        rec["final"] = norm(r.geturl(), True) or r.geturl()
                        ctype = r.headers.get("Content-Type", "")
                        body = r.read()
                        if r.headers.get("Content-Encoding") == "gzip":
                            body = gzip.decompress(body)
                    if "html" in ctype:
                        p = Page(rec["final"])
                        p.feed(body.decode("utf-8", "replace"))
                        text = "\n".join(p.text)
                        rec.update(canonical=p.canonical, robots=p.robots.lower(), title=" ".join(p.title.split()),
                                   h1=" ".join(p.h1.split()), words=len(text.split()), text=text[:40000],
                                   links=[[norm(h, a.keep_query) or h, z, t, rel] for h, z, t, rel in p.links])
                        with lock:
                            for h, *_ in rec["links"]:
                                push(h)
                    break
                except urllib.error.HTTPError as ex:
                    rec["status"] = ex.code
                    if ex.code in (429, 503) and attempt < 2:
                        time.sleep(30 * (attempt + 1))  # rate limited: back off, then retry
                        continue
                    break
                except Exception as ex:
                    rec["error"] = str(ex)[:200]
                    break
            with lock:
                out.write(json.dumps(rec) + "\n")
                out.flush()
                if count[0] % 250 == 0:
                    print(f"{count[0]} fetched, {len(queue)} queued", flush=True)
            time.sleep(a.delay)

    ts = [threading.Thread(target=worker) for _ in range(a.workers)]
    for t in ts:
        t.start()
    for t in ts:
        t.join()
    out.close()

    # Edges, deduplicated, internal only, keyed by final URL of the source.
    seen = set()
    with open(os.path.join(a.out, "edges.csv"), "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["source", "target", "zone", "anchor", "nofollow"])
        for line in open(pages_path):
            r = json.loads(line)
            src = norm(r.get("final") or r["url"], a.keep_query)
            for h, z, t, rel in r.get("links", []):
                if urlsplit(h).hostname != host or h == src:
                    continue
                k = (src, h, z)
                if k in seen:
                    continue
                seen.add(k)
                w.writerow([src, h, z, t, int("nofollow" in rel.lower())])
    print(f"done: {count[0]} pages; edges in {a.out}/edges.csv")


if __name__ == "__main__":
    main()
