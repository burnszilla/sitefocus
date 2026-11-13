#!/usr/bin/env python3
"""Polite single-site crawler for sitefocus. Standard library only.

  site_crawl.py --start https://www.example.com/ --sitemap URL [--sitemap URL ...] \
      --out work/crawl [--max-pages 8000] [--workers 2] [--delay 0.5] [--fresh]

Scope. The crawl origin is the host the start URL resolves to: if
example.com redirects to www.example.com, the crawl covers www.example.com.
Every request, including every redirect hop and nested sitemap, must stay on
that origin and be allowed by its robots.txt. Anything else is recorded, not
fetched.

Politeness. robots.txt is fetched first with the crawler's User-Agent: a 404
or 410 means no rules; 401 or 403 stops the crawl (the site is blocking it);
429 or a server error is retried and, if it persists, stops the crawl. One
shared schedule spaces requests to the site at least --delay seconds apart,
or the robots.txt Crawl-delay if larger, across all workers. A 429 or 503
pauses every worker, honouring Retry-After when given.

Resume. Rerun with the same --out to continue; pages that failed (errors,
429, 5xx) are retried. --fresh moves the previous crawl aside into
out/previous-<timestamp>/ and starts over, which is what you want when
measuring changes you have shipped.

Writes:
  out/pages.jsonl  one record per fetch: url, status, final, canonical, robots,
                   x_robots, title, h1, words, text (main-content blocks, one
                   per line, first 40,000 characters), links [[href, zone,
                   anchor, rel]]; zone is body | nav | header | footer
  out/edges.csv    source,target,zone,anchor,nofollow (internal links only)
  out/meta.json    start URL, crawl origin, robots.txt status and crawl delay
"""
import argparse
import codecs
import csv
import email.utils
import gzip
import json
import os
import re
import shutil
import threading
import time
import urllib.error
import urllib.request
import urllib.robotparser
import zlib
from collections import deque
from html.parser import HTMLParser
from urllib.parse import urldefrag, urljoin, urlsplit, urlunsplit

try:
    from .common import read_pages, repair_jsonl_tail
except ImportError:
    from common import read_pages, repair_jsonl_tail

_CONTACT = os.environ.get("SITEFOCUS_CONTACT") or os.environ.get("CCRA_CONTACT")
UA = "Mozilla/5.0 (compatible; sitefocus/1.0" + (f"; +mailto:{_CONTACT}" if _CONTACT else "") + ")"
BACKOFF = float(os.environ.get("SITEFOCUS_BACKOFF", "30"))  # seconds; first wait after a 429 or 503
MAX_BYTES = 5 * 1024 * 1024        # largest response body read
MAX_INFLATED = 20 * 1024 * 1024    # largest decompressed body
MAX_SITEMAP_FILES = 200
SKIP_EXT = re.compile(r"\.(pdf|jpe?g|png|gif|svg|webp|avif|zip|gz|mp4|mp3|css|js|xml|ico|woff2?|ttf|xlsx?|docx?|pptx?)$", re.I)
ZONE_TAGS = {"nav": "nav", "header": "header", "footer": "footer"}
ZONE_HINT = re.compile(r"(^|[-_ ])(nav|navbar|menu|megamenu|header|footer|breadcrumb)", re.I)
DROP_TAGS = {"script", "style", "noscript", "svg", "template"}
BLOCK_TAGS = {"p", "div", "li", "ul", "ol", "dl", "dt", "dd", "h1", "h2", "h3", "h4", "h5", "h6", "td", "th", "tr", "table",
              "section", "article", "aside", "header", "footer", "nav", "main", "blockquote", "pre", "figure", "figcaption",
              "form", "fieldset", "details", "summary", "br", "hr", "address", "caption", "body", "title"}
VOID = {"br", "img", "meta", "link", "input", "hr", "source", "area", "base", "col", "embed", "param", "track", "wbr"}


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None


_OPENER = urllib.request.build_opener(_NoRedirect)


class Page(HTMLParser):
    """Extracts title, H1, meta robots, canonical, links and main-content
    text. Text is grouped into blocks at block-level elements, so a paragraph
    with bold text or a link inside it stays one block."""

    def __init__(self, base):
        super().__init__(convert_charrefs=True)
        self.base = base
        self._base_set = False
        self.stack = []  # (tag, zone)
        self.blocks, self.links = [], []
        self._buf, self._buf_zone = [], "body"
        self.title = self.h1 = ""
        self.canonical = self.robots = ""
        self._a = None
        self._in = {"title": False, "h1": False}

    def zone(self):
        for _, z in reversed(self.stack):
            if z:
                return z
        return "body"

    def _flush(self):
        t = " ".join(" ".join(self._buf).split())
        if t and self._buf_zone == "body":
            self.blocks.append(t)
        self._buf = []

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        if tag in BLOCK_TAGS:
            self._flush()
        if tag == "base" and a.get("href") and not self._base_set:
            self.base = urljoin(self.base, a["href"])
            self._base_set = True
        z = ZONE_TAGS.get(tag)
        if not z:
            hint = " ".join([a.get("class") or "", a.get("id") or "", a.get("role") or ""])
            m = ZONE_HINT.search(hint)
            if m and tag in ("div", "ul", "section", "aside"):
                z = {"navbar": "nav", "menu": "nav", "megamenu": "nav", "breadcrumb": "nav"}.get(m.group(2).lower(), m.group(2).lower())
            if a.get("role") == "navigation":
                z = "nav"
        if tag not in VOID:
            self.stack.append((tag, z))
        if tag == "meta" and (a.get("name") or "").lower() == "robots":
            self.robots = a.get("content") or ""
        if tag == "link" and "canonical" in (a.get("rel") or "").lower().split():
            self.canonical = urljoin(self.base, a.get("href") or "")
        if tag == "a" and a.get("href"):
            self._a = [urljoin(self.base, a["href"]), self.zone(), [], (a.get("rel") or "")]
        if tag in self._in:
            self._in[tag] = True
        self._buf_zone = self.zone()

    def handle_endtag(self, tag):
        if tag == "a" and self._a:
            href, zone, anchor, rel = self._a
            self.links.append([href, zone, " ".join(" ".join(anchor).split())[:120], rel])
            self._a = None
        if tag in self._in:
            self._in[tag] = False
        if tag in BLOCK_TAGS:
            self._flush()
        for i in range(len(self.stack) - 1, -1, -1):
            if self.stack[i][0] == tag:
                del self.stack[i:]
                break
        self._buf_zone = self.zone()

    def handle_data(self, data):
        if any(t in DROP_TAGS for t, _ in self.stack):
            return
        if self._in["title"]:
            self.title += data
            return
        if self._in["h1"]:
            self.h1 += data
        if self._a is not None:
            self._a[2].append(data)
        if data.strip():
            if self.zone() != self._buf_zone:
                self._flush()
                self._buf_zone = self.zone()
            self._buf.append(data)

    def close(self):
        super().close()
        self._flush()


def norm(u, keep_query=False):
    """Absolute http(s) URL without fragment, lower-case scheme and host, '/'
    for an empty path. Without keep_query the query string is dropped, so
    ?sort= and ?page= variants collapse into one URL."""
    u = urldefrag(u.strip())[0]
    p = urlsplit(u)
    if p.scheme.lower() not in ("http", "https") or not p.hostname:
        return None
    netloc = p.hostname.lower() + (f":{p.port}" if p.port and p.port != {"http": 80, "https": 443}[p.scheme.lower()] else "")
    return urlunsplit((p.scheme.lower(), netloc, p.path or "/", p.query if keep_query else "", ""))


def origin_of(u):
    p = urlsplit(u)
    return (p.hostname or "").lower(), (p.port or {"http": 80, "https": 443}.get(p.scheme.lower(), 0))


def same_site(u, origin):
    """Same host and port as the crawl origin; http and https both count."""
    p = urlsplit(u)
    if p.scheme.lower() not in ("http", "https") or not p.hostname:
        return False
    port = p.port or {"http": 80, "https": 443}[p.scheme.lower()]
    host, oport = origin
    default = {80, 443}
    return p.hostname.lower() == host and (port == oport or (port in default and oport in default))


class Gate:
    """One request schedule for the whole site, shared by all workers."""

    def __init__(self, interval):
        self.interval = interval
        self.next = 0.0
        self.lock = threading.Lock()

    def wait(self):
        with self.lock:
            now = time.time()
            t = max(now, self.next)
            self.next = t + self.interval
        if t > now:
            time.sleep(t - now)

    def cool(self, seconds):
        with self.lock:
            self.next = max(self.next, time.time() + seconds)


def _retry_after(headers, default):
    v = (headers or {}).get("Retry-After") if headers else None
    if not v:
        return default
    try:
        return max(0.0, min(float(v), 3600.0))
    except ValueError:
        try:
            dt = email.utils.parsedate_to_datetime(v)
            return max(0.0, min(dt.timestamp() - time.time(), 3600.0))
        except Exception:
            return default


def _read_body(r):
    raw = r.read(MAX_BYTES + 1)
    truncated = len(raw) > MAX_BYTES
    raw = raw[:MAX_BYTES]
    enc = (r.headers.get("Content-Encoding") or "").lower()
    if enc == "gzip" or raw[:2] == b"\x1f\x8b":
        d = zlib.decompressobj(16 + zlib.MAX_WBITS)
        try:
            raw = d.decompress(raw, MAX_INFLATED)
            truncated = truncated or bool(d.unconsumed_tail)
        except zlib.error:
            pass
    return raw, truncated


def _charset(ctype, raw):
    m = re.search(r"charset=([\w.:-]+)", ctype or "", re.I)
    cs = m.group(1) if m else None
    if not cs:
        m = re.search(rb"<meta[^>]+charset=[\"']?([\w.:-]+)", raw[:4096], re.I)
        cs = m.group(1).decode("ascii", "ignore") if m else "utf-8"
    try:
        codecs.lookup(cs)
    except LookupError:
        cs = "utf-8"
    return cs


def fetch(u, origin, rp, gate, max_hops=5, check=True):
    """GET u, following redirects only while each hop stays on the crawl
    origin and is allowed by robots.txt. Returns (record, body, ctype)."""
    rec = {"url": u}
    cur = u
    for hop in range(max_hops + 1):
        if check and (not same_site(cur, origin) or not rp.can_fetch(UA, cur)):
            rec["status"] = rec.get("status", 0)
            rec["blocked_redirect"] = cur
            return rec, b"", ""
        for attempt in range(3):
            gate.wait()
            try:
                req = urllib.request.Request(cur, headers={"User-Agent": UA, "Accept": "text/html,application/xhtml+xml",
                                                           "Accept-Encoding": "gzip"})
                r = _OPENER.open(req, timeout=40)
                rec["status"] = r.status
                rec["final"] = norm(cur, True) or cur
                rec["x_robots"] = (r.headers.get("X-Robots-Tag") or "").lower()
                ctype = r.headers.get("Content-Type", "")
                if "html" not in ctype.lower():
                    r.close()
                    return rec, b"", ctype
                body, truncated = _read_body(r)
                r.close()
                if truncated:
                    rec["truncated"] = True
                return rec, body, ctype
            except urllib.error.HTTPError as ex:
                code, hdrs = ex.code, ex.headers
                ex.close()
                if 300 <= code < 400 and hdrs and hdrs.get("Location"):
                    nxt = urljoin(cur, hdrs["Location"])
                    rec.setdefault("redirects", []).append([code, nxt])
                    cur = nxt
                    break
                rec["status"] = code
                if code in (429, 503) and attempt < 2:
                    gate.cool(_retry_after(hdrs, BACKOFF * (attempt + 1)))
                    continue
                return rec, b"", ""
            except Exception as ex:
                rec["error"] = str(ex)[:200]
                return rec, b"", ""
        else:
            return rec, b"", ""
    rec["error"] = "too many redirects"
    return rec, b"", ""


def load_robots(start, gate):
    """Fetch and parse robots.txt. Returns (parser, status, crawl_delay)."""
    p = urlsplit(start)
    url = f"{p.scheme}://{p.netloc}/robots.txt"
    rp = urllib.robotparser.RobotFileParser()
    for attempt in range(3):
        gate.wait()
        try:
            req = urllib.request.Request(url, headers={"User-Agent": UA})
            with urllib.request.urlopen(req, timeout=30) as r:
                raw, _ = _read_body(r)
                rp.parse(raw.decode("utf-8", "replace").splitlines())
                return rp, r.status, rp.crawl_delay(UA)
        except urllib.error.HTTPError as ex:
            code, hdrs = ex.code, ex.headers
            ex.close()
            if code in (404, 410):
                rp.parse([])
                return rp, code, None
            if code in (401, 403):
                raise SystemExit(f"robots.txt returned {code} for this crawler; the site is blocking it. Stopping.")
            if attempt < 2:
                gate.cool(_retry_after(hdrs, BACKOFF * (attempt + 1)))
                continue
            raise SystemExit(f"robots.txt returned {code} three times; not crawling a site whose rules cannot be read. Try again later.")
        except urllib.error.URLError as ex:
            raise SystemExit(f"could not fetch {url}: {ex.reason}")
    raise SystemExit(f"could not read {url}")


def resolve_start(start, gate):
    """Follow the start URL's redirects (any host, at most 5 hops) to find the
    site's real origin."""
    cur = start
    for _ in range(6):
        gate.wait()
        try:
            req = urllib.request.Request(cur, headers={"User-Agent": UA, "Accept": "text/html"})
            r = _OPENER.open(req, timeout=40)
            r.close()
            return norm(cur, True) or cur
        except urllib.error.HTTPError as ex:
            code, hdrs = ex.code, ex.headers
            ex.close()
            if 300 <= code < 400 and hdrs and hdrs.get("Location"):
                cur = urljoin(cur, hdrs["Location"])
                continue
            return norm(cur, True) or cur
        except Exception:
            return norm(cur, True) or cur
    return norm(cur, True) or cur


def sitemap_urls(url, origin, rp, gate, limit, roots, seen=None, depth=0):
    """URLs from a sitemap or sitemap index. Nested sitemaps are fetched only
    on the same host as a sitemap you supplied, or the crawl origin."""
    seen = seen if seen is not None else set()
    if url in seen or len(seen) >= MAX_SITEMAP_FILES or depth > 3:
        return []
    seen.add(url)
    host = urlsplit(url).hostname
    if not (same_site(url, origin) or host in roots):
        return []
    gate.wait()
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept-Encoding": "gzip"})
    with urllib.request.urlopen(req, timeout=60) as r:
        body, _ = _read_body(r)
    out = []
    is_index = b"<sitemapindex" in body[:4000]
    for l in re.findall(rb"<loc>\s*([^<\s]+)\s*</loc>", body):
        l = l.decode("utf-8", "replace").replace("&amp;", "&")
        if is_index:
            out += sitemap_urls(l, origin, rp, gate, limit - len(out), roots, seen, depth + 1)
        else:
            out.append(l)
        if len(out) >= limit:
            break
    return out[:limit]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--start", required=True)
    ap.add_argument("--sitemap", action="append", default=[])
    ap.add_argument("--out", required=True)
    ap.add_argument("--max-pages", type=int, default=8000)
    ap.add_argument("--workers", type=int, default=2)
    ap.add_argument("--delay", type=float, default=0.5, help="minimum seconds between requests to the site (all workers together)")
    ap.add_argument("--keep-query", action="store_true", help="keep query strings (default: drop them, collapsing ?sort= variants)")
    ap.add_argument("--fresh", action="store_true", help="move the previous crawl aside and start a new one")
    a = ap.parse_args()
    os.makedirs(a.out, exist_ok=True)
    pages_path = os.path.join(a.out, "pages.jsonl")

    if a.fresh and os.path.exists(pages_path):
        prev = os.path.join(a.out, "previous-" + time.strftime("%Y%m%d-%H%M%S"))
        os.makedirs(prev)
        for f in ("pages.jsonl", "edges.csv", "meta.json"):
            if os.path.exists(os.path.join(a.out, f)):
                shutil.move(os.path.join(a.out, f), os.path.join(prev, f))
        print(f"previous crawl moved to {prev}", flush=True)

    gate = Gate(a.delay)
    start = norm(a.start, True)
    if not start:
        raise SystemExit(f"--start must be an http(s) URL: {a.start}")
    # robots.txt comes before any page request, for the start host and, if
    # the start URL redirects elsewhere, for the host it lands on.
    rp, robots_status, cdelay = load_robots(start, gate)
    if not rp.can_fetch(UA, start):
        raise SystemExit(f"robots.txt disallows the start URL {start} for this crawler. Stopping.")
    gate.interval = max(a.delay, float(cdelay or 0))
    final_start = resolve_start(start, gate)
    origin = origin_of(final_start)
    if origin_of(start) != origin:
        print(f"start URL redirects to {final_start}; crawling {origin[0]}", flush=True)
        rp, robots_status, cdelay = load_robots(final_start, gate)
    gate.interval = max(a.delay, float(cdelay or 0))
    if cdelay:
        print(f"robots.txt Crawl-delay {cdelay}s: one request every {gate.interval:g}s", flush=True)
    with open(os.path.join(a.out, "meta.json"), "w") as f:
        json.dump({"start": start, "start_final": final_start, "origin_host": origin[0], "origin_port": origin[1],
                   "robots_status": robots_status, "crawl_delay": cdelay, "interval": gate.interval,
                   "keep_query": a.keep_query, "user_agent": UA}, f, indent=1)

    if repair_jsonl_tail(pages_path):
        print("repaired a partial last line in pages.jsonl (kept in pages.jsonl.damaged)", flush=True)
    previous = read_pages(pages_path)
    retry = lambda r: r.get("error") or r.get("status") in (0, 429, 500, 502, 503, 504) or (r.get("status") is None)
    done = {r["url"] for r in previous if not retry(r)}

    queue, queued = deque(), set()
    frontier_cap = max(5 * a.max_pages, 50000)

    def push(u):
        u = norm(u, a.keep_query)
        if (not u or u in queued or len(queued) >= frontier_cap or not same_site(u, origin)
                or SKIP_EXT.search(urlsplit(u).path) or not rp.can_fetch(UA, u)):
            return
        queued.add(u)
        queue.append(u)

    push(final_start)
    roots = {origin[0]} | {urlsplit(s).hostname for s in a.sitemap}
    for sm in a.sitemap:
        try:
            got = sitemap_urls(sm, origin, rp, gate, 5 * a.max_pages, roots)
        except Exception as ex:
            print(f"warning: sitemap {sm} could not be read ({ex}); continuing without it", flush=True)
            continue
        print(f"sitemap {sm}: {len(got)} URLs", flush=True)
        for u in got:
            push(u)
    for r in previous:
        for l in r.get("links", []):
            push(l[0])

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
            rec, body, ctype = fetch(u, origin, rp, gate)
            if body:
                p = Page(rec.get("final") or u)
                p.feed(body.decode(_charset(ctype, body), "replace"))
                p.close()
                text = "\n".join(p.blocks)
                rec.update(canonical=p.canonical, robots=p.robots.lower(), title=" ".join(p.title.split()),
                           h1=" ".join(p.h1.split()), words=len(text.split()), text=text[:40000],
                           links=[[norm(h, a.keep_query) or h, z, t, rel] for h, z, t, rel in p.links])
                with lock:
                    for h, *_ in rec["links"]:
                        push(h)
            with lock:
                out.write(json.dumps(rec) + "\n")
                out.flush()
                if count[0] % 250 == 0:
                    print(f"{count[0]} fetched, {len(queue)} queued", flush=True)

    ts = [threading.Thread(target=worker) for _ in range(max(1, a.workers))]
    for t in ts:
        t.start()
    for t in ts:
        t.join()
    out.close()

    # Edges, deduplicated, internal only, from the latest record of each URL.
    seen = set()
    with open(os.path.join(a.out, "edges.csv"), "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["source", "target", "zone", "anchor", "nofollow"])
        for r in read_pages(pages_path):
            src = norm(r.get("final") or r["url"], a.keep_query)
            for h, z, t, rel in r.get("links", []):
                if not same_site(h, origin) or h == src:
                    continue
                k = (src, h, z, "nofollow" in (rel or "").lower())
                if k in seen:
                    continue
                seen.add(k)
                w.writerow([src, h, z, t, int("nofollow" in (rel or "").lower())])
    print(f"done: {count[0]} pages; edges in {a.out}/edges.csv")


if __name__ == "__main__":
    main()
