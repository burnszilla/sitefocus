#!/usr/bin/env python3
"""Focus-aware internal linking: one score for where a site's link importance
goes relative to its topic, a four-way page map, and a plan that raises the
score, simulated before and after.

  link_plan.py --crawl work/crawl --focus work/focus --priority priority.txt --out work/plan

priority.txt: the pages the site most needs to rank (product or money pages),
one URL per line. They define the topic.

Per page
  topic    0-100: percentile of cosine similarity to the priority-page centre
           (the anchor). Percentiles make the score independent of the
           embedding model's baseline.
  links    0-100: percentile of internal PageRank (damping 0.85, one edge per
           page pair, indexable same-host pages only).

Site
  Link Focus Score (LFS) = sum over pages of PageRank x topic, PageRank
  normalised to 1. 100: all link importance sits on the most on-topic pages.
  50: importance is spread with no regard to topic. Below 50: it favours
  off-topic pages.

Page map (median splits)
  star        on topic, well linked        keep
  underlinked on topic, weakly linked      send links here
  leak        off topic, well linked       stop sending sitewide links here
  long tail   off topic, weakly linked     leave, consolidate or noindex

Plan, in order
  1. orphan       priority pages with 0 to 2 linking pages: link from the
                  parent hub and the 8 most similar pages
  2. priority     on-topic pages link to their closest priority page
  3. underlinked  strong pages (top half of PageRank, on topic) link to the
                  most similar underlinked pages (topic in the top quarter)
  At most --adds-per-page per source, --max-new-inlinks per target, never
  between near-duplicates (similarity >= --near-dup).
  4. swap (separate scenario) sitewide slots pointing at single directory
     entries, campaign pages or stale pages go to orphaned priority pages.
     Core content is never swapped out automatically. Sitewide leaks are
     listed for human review.
Writes out/plan.json, out/pages.csv, out/link_adds.csv, out/sitewide.csv.
"""
import argparse
import csv
import datetime
import json
import os
import re
from collections import Counter, defaultdict
from urllib.parse import urljoin, urlsplit

import numpy as np


def pagerank(n, src, dst, d=0.85, iters=100, w=None, tele=None):
    """Weighted, personalised PageRank. w: per-edge weights (link prominence);
    tele: teleport vector (where external link equity lands), sums to 1."""
    w = np.ones(len(src)) if w is None else np.asarray(w, dtype=np.float64)
    tele = np.full(n, 1 / n) if tele is None else tele
    outw = np.bincount(src, weights=w, minlength=n)
    pr = tele.copy()
    for _ in range(iters):
        new = np.zeros(n)
        np.add.at(new, dst, pr[src] * w / np.maximum(outw[src], 1e-12))
        new = (1 - d) * tele + d * (new + pr[outw == 0].sum() * tele)
        if np.abs(new - pr).sum() < 1e-12:
            return new
        pr = new
    return pr


def _popcount(a, fallback=False):
    """Bits set per uint64 element (numpy >= 2 has bitwise_count)."""
    if hasattr(np, "bitwise_count") and not fallback:
        return np.bitwise_count(a).astype(np.int64)
    return np.unpackbits(a.view(np.uint8).reshape(-1, 8), axis=1).sum(1)


def harmonic(n, src, dst):
    """Exact internal harmonic centrality: HC(v) = sum over u != v of 1 / d(u, v),
    d = shortest link path from u to v, normalised by n - 1. Breadth-first
    search from 64 sources at once, one bit per source, so a 5,000-page site
    takes seconds instead of hours."""
    src = np.asarray(src, dtype=np.int64)
    dst = np.asarray(dst, dtype=np.int64)
    hc = np.zeros(n)
    one = np.uint64(1)
    for b0 in range(0, n, 64):
        seeds = np.arange(b0, min(b0 + 64, n))
        visited = np.zeros(n, dtype=np.uint64)
        visited[seeds] = one << np.arange(len(seeds), dtype=np.uint64)
        frontier = visited.copy()
        k = 0
        while True:
            k += 1
            nxt = np.zeros(n, dtype=np.uint64)
            active = frontier[src] != 0
            if not active.any():
                break
            np.bitwise_or.at(nxt, dst[active], frontier[src][active])
            new = nxt & ~visited
            if not new.any():
                break
            hc += _popcount(new) / k
            visited |= new
            frontier = new
    return hc / max(n - 1, 1)


def pctile(x):
    """0-100 percentile rank, average ranks for ties."""
    order = np.argsort(x, kind="mergesort")
    r = np.empty(len(x))
    r[order] = np.arange(len(x))
    xs = x[order]
    i = 0
    while i < len(xs):
        j = i
        while j + 1 < len(xs) and xs[j + 1] == xs[i]:
            j += 1
        r[order[i:j + 1]] = (i + j) / 2
        i = j + 1
    return 100 * r / max(len(x) - 1, 1)


def section_of(url):
    parts = [p for p in urlsplit(url).path.split("/") if p]
    return parts[0] if parts else "(home)"


def parent_of(url):
    p = urlsplit(url)
    segs = [s for s in p.path.split("/") if s]
    if not segs:
        return None
    return f"{p.scheme}://{p.netloc}/" + ("/".join(segs[:-1]) + "/" if len(segs) > 1 else "")


GSC_URL = ("top pages", "page", "pages", "url", "landing page", "address")


def _num(x):
    x = (x or "").replace(",", "").replace("%", "").strip()
    try:
        return float(x)
    except ValueError:
        return 0.0


def load_gsc(path, redirect):
    """Read a Search Console Pages export (Performance > Pages > Export, or the
    Pages.csv inside the export zip). Columns are matched by name."""
    rows = list(csv.reader(open(path, encoding="utf-8-sig")))
    head = [h.strip().lower() for h in rows[0]]
    col = lambda names: next((head.index(nm) for nm in names if nm in head), None)
    cu, cc, ci, cp = col(GSC_URL), col(("clicks",)), col(("impressions",)), col(("position", "average position"))
    if cu is None or cc is None:
        raise SystemExit(f"{path}: need a page URL column and a Clicks column; found {rows[0]}")
    out = {}
    for r in rows[1:]:
        if len(r) <= cu:
            continue
        u = r[cu].split("#")[0].strip()
        u = redirect.get(u, u)
        d = {"clicks": _num(r[cc]), "impressions": _num(r[ci]) if ci is not None else 0.0,
             "position": _num(r[cp]) if cp is not None else 0.0}
        for v in (u, u.rstrip("/"), u.rstrip("/") + "/"):
            out.setdefault(v, d)
    return out


def load_backlinks(path, redirect):
    """Backlink export: Search Console Links > Top linked pages (externally), or
    an Ahrefs / Semrush best-by-links export. Uses linking sites (referring
    domains) if present, else incoming links."""
    rows = list(csv.reader(open(path, encoding="utf-8-sig")))
    head = [h.strip().lower() for h in rows[0]]
    cu = next((head.index(x) for x in head if x in GSC_URL or "page" in x or "url" in x), None)
    cn = next((head.index(x) for x in head if "linking sites" in x or "referring domains" in x or "linking domains" in x), None)
    if cn is None:
        cn = next((head.index(x) for x in head if "incoming links" in x or "backlinks" in x or x == "links"), None)
    if cu is None or cn is None:
        raise SystemExit(f"{path}: need a page column and a linking sites / incoming links column; found {rows[0]}")
    out = {}
    for r in rows[1:]:
        if len(r) > max(cu, cn):
            u = redirect.get(r[cu].strip(), r[cu].strip())
            for v in (u, u.rstrip("/"), u.rstrip("/") + "/"):
                out.setdefault(v, _num(r[cn]))
    return out


import time as _time
import urllib.error
import urllib.request

_UA = "Mozilla/5.0 (compatible; sitefocus/1.0" + (f"; +mailto:{os.environ['SITEFOCUS_CONTACT']}" if os.environ.get("SITEFOCUS_CONTACT") else "") + ")"


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None


def recheck(urls, delay=0.5, limit=200):
    """Fetch each URL once more, without following redirects. Returns
    {url: (status, location)}; status 0 means the request failed."""
    opener = urllib.request.build_opener(_NoRedirect)
    out = {}
    for u in list(urls)[:limit]:
        try:
            r = opener.open(urllib.request.Request(u, headers={"User-Agent": _UA}), timeout=20)
            out[u] = (r.status, "")
            r.close()
        except urllib.error.HTTPError as ex:
            out[u] = (ex.code, ex.headers.get("Location", "") if ex.headers else "")
            ex.close()
        except Exception:
            out[u] = (0, "")
        _time.sleep(delay)
    return out


STOP = set("the and for with that this from your you are was were have has had not but can will what when how why who which into about over more than they them their there then also its our out all any may per via".split())
WORD = re.compile(r"[a-z0-9][a-z0-9'’-]*")


def _tokens(text):
    return [w for w in WORD.findall(text.lower()) if len(w) > 2 and w not in STOP]


def clean_title(t):
    """'Married Filing Jointly vs Separately: How Should You File? - Brand Tax Tips' -> the useful part."""
    t = (t or "").replace("®", "").replace("™", "").strip()
    for sep in (" | ", " - ", " – ", " · "):
        if sep in t:
            head, tail = t.split(sep, 1)
            # Drop a trailing brand or section suffix; keep the page's own name.
            if len(head.strip()) >= 4 and len(tail.strip()) <= 45:
                t = head.strip()
    if len(t) > 60 and ":" in t:
        t = t.split(":")[0].strip()
    return t[:80]


def _is_prose(b):
    return 40 <= len(b) <= 1500 and len(b.split()) >= 8 and " | " not in b and (re.search(r"[.?!]", b) or len(b.split()) >= 15)


def place_link(blocks, target_terms, idf, distinctive):
    """Pick the source paragraph that best matches the target and a phrase in
    it to use as anchor. The anchor must contain at least one distinctive word
    (rare on the site), so generic phrases like "your taxes" never qualify.
    Returns (paragraph, anchor or '', score)."""
    best, bscore = "", 0.0
    for b in blocks:
        if not _is_prose(b):
            continue
        toks = set(_tokens(b))
        shared = toks & target_terms
        if not shared & distinctive:
            continue  # only generic overlap: not a natural place for this link
        sc = sum(idf.get(t, 0.0) for t in shared)
        if sc > bscore:
            best, bscore = b, sc
    if not best:
        return "", "", 0.0
    words = re.findall(r"\S+", best)
    norm = [re.sub(r"[^a-z0-9'’-]", "", w.lower()) for w in words]
    pick, pscore = "", 0.0
    for i in range(len(words)):
        for j in range(i + 2, min(i + 7, len(words) + 1)):
            seg = norm[i:j]
            content = [w for w in seg if len(w) > 2 and w not in STOP]
            if not content or seg[0] in STOP or seg[-1] in STOP or any(not w for w in seg):
                continue
            short = lambda k: len(norm[k]) < 3 and not re.fullmatch(r"[A-Z0-9][A-Z0-9-]*", words[k].strip(".,;:!?()\"'"))
            if short(i) or short(j - 1):
                continue  # no "to our system"; "S corporation" and "W-2 form" are fine
            if any(re.search(r"[.?!:;,)]$", x) for x in words[i:j - 1]) or "(" in words[j - 1]:
                continue  # do not span a sentence or clause break
            if not all(w in target_terms for w in content) or not any(w in distinctive for w in content):
                continue
            sc = sum(idf.get(w, 0.0) for w in content)
            if (len(content) >= 2 or sum(w in distinctive for w in content) >= 1 and len(seg) >= 2) and sc > pscore:
                pick, pscore = " ".join(words[i:j]).strip(".,;:!?()\"'“”"), sc
    return best, pick, bscore


def llm_anchor(base, model, paragraph, target_title):
    """Ask a local Ollama model for a short anchor phrase taken from the paragraph."""
    prompt = ("Task: choose anchor text for an internal link.\n"
              f"Target page title: {target_title}\n"
              f"Paragraph: {paragraph[:1200]}\n\n"
              "Rules: the anchor must be copied word for word from the Paragraph (2 to 6 consecutive words), must not be the "
              "target title unless those exact words appear in the Paragraph, and must describe what the target page is about.\n"
              "Example: title 'Roth IRA Calculator', paragraph 'You can open a Roth IRA at most banks.' -> a Roth IRA\n"
              "Reply with only the copied words, or NONE if no words in the Paragraph fit.")
    body = json.dumps({"model": model, "prompt": prompt, "stream": False, "think": False,
                       "options": {"temperature": 0}}).encode()
    try:
        with urllib.request.urlopen(urllib.request.Request(f"{base}/api/generate", data=body,
                                    headers={"Content-Type": "application/json"}), timeout=60) as r:
            ans = json.load(r).get("response", "").strip().strip('"').strip()
    except Exception:
        return ""
    ans = re.sub(r"<think>.*?</think>", "", ans, flags=re.S).strip().strip(".")
    if not ans or ans.upper() == "NONE":
        return ""
    if ans.lower() in paragraph.lower() and 2 <= len(ans.split()) <= 6:
        return ans
    # Small models often paraphrase. Keep the longest run of their words that
    # really is in the paragraph, if it is at least two words long.
    w_ = ans.split()
    low = paragraph.lower()
    for size in range(min(6, len(w_)), 1, -1):
        for i in range(len(w_) - size + 1):
            cand = " ".join(w_[i:i + size])
            k = low.find(cand.lower())
            if k >= 0:
                return paragraph[k:k + len(cand)]
    return ""


GENERIC_ANCHOR = re.compile(r"^(learn more|read more|more|click here|here|see more|details|get started|start for free|start free|this|link|view|see details|find out more|continue|go)$", re.I)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--crawl", required=True)
    ap.add_argument("--focus", required=True)
    ap.add_argument("--priority", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--adds-per-page", type=int, default=3)
    ap.add_argument("--max-new-inlinks", type=int, default=150)
    ap.add_argument("--template-share", type=float, default=0.5)
    ap.add_argument("--min-sim", type=float, default=0.72)
    ap.add_argument("--near-dup", type=float, default=0.97)
    ap.add_argument("--year", type=int, default=0)
    ap.add_argument("--gsc", help="Search Console Pages export (CSV): protects pages that earn clicks, targets near wins")
    ap.add_argument("--protect-clicks", type=float, default=0, help="pages with at least this many clicks are never swapped out (default: top 10%% of pages with clicks)")
    ap.add_argument("--slots", type=int, default=10, help="sitewide content slots to reassign in the slot scenario")
    ap.add_argument("--module-section", help="section for the related-products module scenario (default: largest editorial section)")
    ap.add_argument("--backlinks", help="backlink export (Search Console Top linked pages, or Ahrefs/Semrush): external equity enters the model where it really lands")
    ap.add_argument("--template-weight", type=float, default=0.5, help="weight of navigation, footer and sitewide links relative to links in body copy (1 = all equal)")
    ap.add_argument("--no-recheck", action="store_true", help="skip the live re-check of redirect, broken and rate-limited link targets")
    ap.add_argument("--anchor-model", help="optional local Ollama model (e.g. qwen3:8b) to suggest anchors where no phrase in the paragraph matches")
    ap.add_argument("--ollama", default=os.environ.get("SITEFOCUS_OLLAMA") or os.environ.get("CCRA_OLLAMA") or "http://localhost:11434")
    a = ap.parse_args()
    os.makedirs(a.out, exist_ok=True)

    fp = json.load(open(os.path.join(a.focus, "pages.json")))
    keys = json.load(open(os.path.join(a.focus, "vectors.keys.json")))
    V = np.load(os.path.join(a.focus, "vectors.npy"))
    V = V / np.linalg.norm(V, axis=1, keepdims=True)
    vec = dict(zip(keys, V))
    F = json.load(open(os.path.join(a.focus, "focus.json")))

    redirect, host = {}, None
    for line in open(os.path.join(a.crawl, "pages.jsonl")):
        r = json.loads(line)
        host = host or urlsplit(r["url"]).hostname
        if r.get("final"):
            f_ = r["final"].split("#")[0]
            redirect[r["url"]] = f_ + "/" if f_.count("/") == 2 else f_
    for u in list(redirect):
        if u.count("/") == 2:  # https://example.com -> https://example.com/
            redirect[u] = redirect[u] if redirect[u].count("/") > 2 else redirect[u] + "/"
    for u in [u for u in redirect if u.count("/") == 3 and u.endswith("/")]:
        redirect.setdefault(u[:-1], redirect[u])
    nodes = [p["url"] for p in fp if p["indexable"] and urlsplit(p["url"]).hostname == host]
    ix = {u: i for i, u in enumerate(nodes)}
    info = {p["url"]: p for p in fp}
    n = len(nodes)
    Vn = np.array([vec[u] for u in nodes])
    secs = [section_of(u) for u in nodes]

    status, finals, crawled = {}, {}, set()
    for line in open(os.path.join(a.crawl, "pages.jsonl")):
        r = json.loads(line)
        crawled.add(r["url"])
        status[r["url"]] = r.get("status") or ("error" if r.get("error") else None)
        finals[r["url"]] = (r.get("final") or r["url"]).split("#")[0]
    nonindex = {p["url"] for p in fp if not p["indexable"]}

    pairs, body_pairs, leaked = set(), set(), 0
    anchors = defaultdict(Counter)
    hyg = {"to_redirect": Counter(), "to_broken": Counter(), "to_nonindexable": Counter(), "to_uncrawled": Counter(),
           "rate_limited": Counter(), "nofollow": Counter(), "account": Counter(), "page_nofollow": Counter()}
    hyg_src = defaultdict(Counter)
    acct = re.compile(r"(sign-?in|log-?in|sign-?up|/register|create-?account|/account(/|$)|/my-?account|/cart(/|$)|/checkout|^my[a-z]*\.)", re.I)
    for r in csv.DictReader(open(os.path.join(a.crawl, "edges.csv"))):
        s = redirect.get(r["source"], r["source"])
        raw_t = r["target"]
        t = redirect.get(raw_t, raw_t)
        if s not in ix:
            continue
        if info.get(s, {}).get("nofollow_page"):
            hyg["page_nofollow"][s] += 1
            continue
        if r.get("nofollow") == "1":
            hyg["nofollow"][raw_t] += 1
        if acct.search(raw_t.split("://", 1)[-1]):
            hyg["account"][raw_t] += 1
        if raw_t in crawled and finals.get(raw_t, raw_t).rstrip("/") != raw_t.rstrip("/"):
            hyg["to_redirect"][raw_t] += 1
            hyg_src["to_redirect"][r["source"]] += 1
        st = status.get(raw_t)
        if st in (429, 503):
            hyg["rate_limited"][raw_t] += 1
        elif st == "error" or (isinstance(st, int) and st >= 400):
            hyg["to_broken"][raw_t] += 1
            hyg_src["to_broken"][r["source"]] += 1
        elif raw_t not in crawled and urlsplit(raw_t).hostname == host:
            hyg["to_uncrawled"][raw_t] += 1
        if t in nonindex and t != s:
            hyg["to_nonindexable"][t] += 1
        if t in ix:
            if s != t:
                pairs.add((ix[s], ix[t]))
                if r["zone"] == "body":
                    body_pairs.add((ix[s], ix[t]))
                if r.get("anchor"):
                    anchors[ix[t]][" ".join(r["anchor"].split()).lower()[:80]] += 1
        else:
            leaked += 1

    live = {}
    if not a.no_recheck:
        todo = set(hyg["to_redirect"]) | set(hyg["to_broken"]) | set(hyg["rate_limited"])
        if todo:
            print(f"rechecking {len(todo)} link targets live", flush=True)
            live = recheck(sorted(todo, key=lambda u: -(hyg["to_redirect"][u] + hyg["to_broken"][u] + hyg["rate_limited"][u])))
        for u, (st_, loc) in live.items():
            c = hyg["to_redirect"].pop(u, 0) + hyg["to_broken"].pop(u, 0) + hyg["rate_limited"].pop(u, 0)
            if 300 <= st_ < 400:
                hyg["to_redirect"][u] = c
                if loc:
                    finals[u] = urljoin(u, loc)
            elif st_ in (429, 503) or st_ == 0:
                hyg["rate_limited"][u] = c
            elif st_ >= 400:
                hyg["to_broken"][u] = c
            # 2xx: fine on recheck, dropped
            status[u] = st_ or status.get(u)

    indeg_all = Counter(t for _, t in pairs)
    sitewide_set = {t for t, k in indeg_all.items() if k >= a.template_share * n}
    template_pairs = {p_ for p_ in pairs if p_ not in body_pairs or p_[1] in sitewide_set}

    backlinks = load_backlinks(a.backlinks, redirect) if a.backlinks else {}
    ext = np.array([backlinks.get(u) or backlinks.get(u.rstrip("/")) or 0.0 for u in nodes])
    if backlinks and ext.sum() > 0:
        tele = 1 + np.sqrt(ext)  # every page keeps a floor; equity grows sublinearly with linking sites
        tele = tele / tele.sum()
    else:
        tele = None

    def pr_of(e, tpl=None):
        tpl = template_pairs if tpl is None else tpl
        e = list(e)
        w = [a.template_weight if p_ in tpl else 1.0 for p_ in e]
        return pagerank(n, np.array([p_[0] for p_ in e]), np.array([p_[1] for p_ in e]), w=w, tele=tele)

    pr = pr_of(pairs)
    linking, existing = defaultdict(set), defaultdict(set)
    for s_, t_ in pairs:
        linking[t_].add(s_)
        existing[s_].add(t_)
    existing_orig = {k: set(v) for k, v in existing.items()}
    existing_orig = defaultdict(set, existing_orig)

    priority = []
    for u in open(a.priority):
        u = redirect.get(u.strip(), u.strip())
        if u in ix and ix[u] not in priority:
            priority.append(ix[u])
    if not priority:
        raise SystemExit("none of the priority URLs were crawled as indexable pages; check priority.txt")
    pset = set(priority)
    anchor = Vn[priority].mean(0)
    anchor /= np.linalg.norm(anchor)
    sim = Vn @ anchor
    topic = pctile(sim)

    gsc = load_gsc(a.gsc, redirect) if a.gsc else {}
    g = [gsc.get(u) or gsc.get(u.rstrip("/")) or {} for u in nodes]
    clicks = np.array([x.get("clicks", 0.0) for x in g])
    impr = np.array([x.get("impressions", 0.0) for x in g])
    pos = np.array([x.get("position", 0.0) for x in g])
    has_gsc = bool(gsc)
    if has_gsc:
        thr = a.protect_clicks or (float(np.percentile(clicks[clicks > 0], 90)) if (clicks > 0).any() else 0)
        protected = (clicks >= thr) & (clicks > 0)
        imed = float(np.median(impr[impr > 0])) if (impr > 0).any() else 0
        near_win = (pos >= 4) & (pos <= 20) & (impr >= imed) & (topic >= 50)
    else:
        thr, protected, near_win = 0, np.zeros(n, bool), np.zeros(n, bool)

    def lfs(p):
        return float((p * topic).sum() / p.sum())

    def quadrants(p):
        lk = pctile(p)
        on, hi = topic >= 50, lk >= 50
        return {"star": on & hi, "underlinked": on & ~hi, "leak": ~on & hi, "long tail": ~on & ~hi}

    q0 = quadrants(pr)
    links0 = pctile(pr)

    near_dups = {}
    for s in set(secs):
        idx_s = [i for i, x in enumerate(secs) if x == s]
        if len(idx_s) < 20:
            continue
        M = Vn[idx_s] @ Vn[idx_s].T
        np.fill_diagonal(M, 0)
        has = (M >= a.near_dup).any(1)
        near_dups[s] = {"pages": len(idx_s), "with_near_duplicate": int(has.sum()), "share": round(float(has.mean()), 3)}

    # Plan.
    orphans = [p for p in priority if len(linking[p]) <= 2]
    adds, new_in, per_src = [], Counter(), Counter()

    def add(s_, t_, sm, why, cap=True):
        if s_ == t_ or t_ in existing[s_] or new_in[t_] >= a.max_new_inlinks:
            return False
        if cap and per_src[s_] >= a.adds_per_page:
            return False
        per_src[s_] += 1
        existing[s_].add(t_)
        new_in[t_] += 1
        adds.append((s_, t_, sm, why))
        return True

    for t_ in orphans:
        par = parent_of(nodes[t_])
        while par and par not in ix:
            nxt = parent_of(par)
            par = nxt if nxt != par else None
        if par in ix:
            add(ix[par], t_, float(Vn[ix[par]] @ Vn[t_]), "orphan: parent hub", cap=False)
        sims = Vn @ Vn[t_]
        got = 0
        for s_ in np.argsort(-sims):
            if s_ == t_ or (s_ in pset and len(linking[s_]) <= 2) or sims[s_] >= a.near_dup:
                continue
            if add(int(s_), t_, float(sims[s_]), "orphan: similar page"):
                got += 1
            if got >= 8:
                break

    P = Vn[priority]
    for s_ in np.argsort(-pr):
        if s_ in pset or topic[s_] < 50:
            continue
        sims = P @ Vn[s_]
        for j in np.argsort(-sims)[:3]:
            if a.min_sim <= sims[j] < a.near_dup and add(int(s_), priority[j], float(sims[j]), "priority"):
                break

    nw = [i for i in np.where(near_win)[0] if i not in pset]
    if nw:
        T = Vn[nw]
        for s_ in np.argsort(-pr):
            if links0[s_] < 50 or topic[s_] < 50 or s_ in pset:
                continue
            sw = T @ Vn[s_]
            for j in np.argsort(-sw)[:5]:
                if a.min_sim <= sw[j] < a.near_dup and add(int(s_), nw[j], float(sw[j]), "near win"):
                    break

    targets = [i for i in range(n) if topic[i] >= 75 and links0[i] < 50 and i not in pset]
    if targets:
        T = Vn[targets]
        for s_ in np.argsort(-pr):
            if links0[s_] < 50 or topic[s_] < 50 or s_ in pset:
                continue
            sw = T @ Vn[s_]
            for j in np.argsort(-sw)[:5]:
                if a.min_sim + 0.05 <= sw[j] < a.near_dup and add(int(s_), targets[j], float(sw[j]), "underlinked"):
                    break

    # Sitewide links and the swap scenario.
    year = a.year or datetime.date.today().year
    big_secs = {x["group"] for x in F["sections"] if x["pages"] >= 300 and x["focus"] >= 0.88}

    def flag(t):
        u = nodes[t]
        ys = [int(y) for y in re.findall(r"(?<!\d)(20\d\d)(?!\d)", u)]
        ty = [int(y) for y in re.findall(r"(?<!\d)(20\d\d)(?!\d)", info[u]["title"])]
        if ys and max(ys) <= year - 2 and ty and max(ty) <= year - 2:
            return f"stale ({max(ys)})"
        if secs[t] in big_secs and len([x for x in urlsplit(u).path.split("/") if x]) >= 3:
            return "single directory entry"
        if re.search(r"commercial|super-bowl|promo|campaign", u):
            return "campaign page"
        return ""

    indeg = Counter(t for _, t in pairs)
    sitewide = sorted([t for t, k in indeg.items() if k >= a.template_share * n], key=lambda t: topic[t])
    swap_out = [t for t in sitewide if t not in pset and flag(t) and not protected[t]][: len(orphans)]
    swap_in = orphans[: len(swap_out)]

    moved_tpl = set()

    def with_swap(e):
        e = set(e)
        for old, new in zip(swap_out, swap_in):
            for s_ in [s_ for s_, t_ in e if t_ == old and (s_, t_) not in body_pairs]:
                e.discard((s_, old))
                if s_ != new:
                    e.add((s_, new))
                    moved_tpl.add((s_, new))
        return e

    e_adds = pairs | {(s_, t_) for s_, t_, _, _ in adds}
    pr_a = pr_of(e_adds)
    hc0 = harmonic(n, [p_[0] for p_ in pairs], [p_[1] for p_ in pairs])
    hca = harmonic(n, [p_[0] for p_ in e_adds], [p_[1] for p_ in e_adds])
    reach0 = pctile(hc0)
    reach_gap = reach0 - links0  # negative: PageRank runs ahead of reach (fed by a few strong links)
    e_swap = with_swap(e_adds)
    pr_b = pr_of(e_swap, template_pairs | moved_tpl)
    q_a, q_b = quadrants(pr_a), quadrants(pr_b)

    # Scenarios: what actually moves the score.
    def reassign(e, outs, ins):
        e = set(e)
        for old, new in zip(outs, ins):
            for s_ in [s_ for s_, t_ in e if t_ == old and (s_, t_) not in body_pairs]:
                e.discard((s_, old))
                if s_ != new:
                    e.add((s_, new))
                    moved_tpl.add((s_, new))
        return e

    weakest = sorted(priority, key=lambda i: pr_a[i])
    depth = lambda t: len([x for x in urlsplit(nodes[t]).path.split("/") if x])
    content_slots = [t for t in sitewide if t not in pset and t not in swap_out and depth(t) >= 2 and not protected[t]][: a.slots]
    slot_in = [p_ for p_ in weakest if p_ not in sitewide_set and p_ not in swap_in][: len(content_slots)]  # moving a slot to a page already linked sitewide changes nothing
    content_slots = content_slots[: len(slot_in)]
    e_slots = reassign(e_adds, content_slots, slot_in)
    msec = a.module_section or next((x["group"] for x in F["sections"] if x["group"] not in big_secs and x["pages"] >= 50), None)
    e_mod = set(e_adds)
    if msec:
        for i in [i for i, x in enumerate(secs) if x == msec]:
            for j in np.argsort(-(P @ Vn[i]))[:3]:
                e_mod.add((i, priority[j]))
    pr_s, pr_m = pr_of(e_slots, template_pairs | moved_tpl), pr_of(e_mod)
    pri_ = np.array(priority)
    scen = [
        {"name": "Now", "lfs": lfs(pr), "priority": float(pr[pri_].sum()), "changes": "", "caveat": ""},
        {"name": "Contextual links", "lfs": lfs(pr_a), "priority": float(pr_a[pri_].sum()),
         "changes": f"{len(adds):,} links added in body copy", "caveat": ""},
        {"name": "Links + flagged swap", "lfs": lfs(pr_b), "priority": float(pr_b[pri_].sum()),
         "changes": f"{len(swap_out)} flagged sitewide slots go to orphaned priority pages", "caveat": ""},
        {"name": f"Links + related-products module on /{msec}/" if msec else "Related-products module", "lfs": lfs(pr_m), "priority": float(pr_m[pri_].sum()),
         "changes": f"every /{msec}/ page links to its 3 closest priority pages" if msec else "no editorial section found", "caveat": ""},
        {"name": f"Links + {len(content_slots)} sitewide content slots to products", "lfs": lfs(pr_s), "priority": float(pr_s[pri_].sum()),
         "changes": (f"the {len(content_slots)} least on-topic sitewide content links point at the weakest priority pages instead" if content_slots
                     else "not applicable: every priority page is already linked sitewide, or no unprotected content slot exists"),
         "caveat": ("pages with the most clicks are excluded" if has_gsc else "check traffic first: these slots may hold pages that earn clicks (add --gsc)")},
    ]
    def traffic_scenario():
        """Clicks if the pages the plan strengthens gain 1, 2 or 3 positions,
        using this site's own CTR-by-position curve from the GSC export. A
        what-if range, not a forecast; None when the data cannot support it."""
        if not has_gsc:
            return {"available": False, "reason": "no Search Console export (--gsc)"}
        ok = (impr >= 20) & (pos > 0)
        buckets = {}
        for b in range(1, 21):
            m = ok & (np.round(pos) == b)
            if m.sum() >= 3:
                buckets[b] = float(clicks[m].sum() / impr[m].sum())
        if len(buckets) < 6 or 1 not in buckets and 2 not in buckets:
            return {"available": False, "reason": f"not enough Search Console data to build this site's CTR curve ({len(buckets)} usable positions; need 6 including position 1 or 2)"}
        xs = sorted(buckets)
        ys = [buckets[x] for x in xs]
        for i in range(1, len(ys)):  # CTR should not rise as position worsens
            ys[i] = min(ys[i], ys[i - 1])

        def ctr(p_):
            p_ = min(max(p_, xs[0]), xs[-1])
            return float(np.interp(p_, xs, ys))
        strengthened = set(t for _, t, _, _ in adds) | set(slot_in) | set(swap_in) | set(priority)
        cand = [i for i in strengthened if impr[i] > 0 and 1.5 < pos[i] <= 30]
        out = {"available": True, "pages": len(cand), "impressions": float(impr[cand].sum()) if cand else 0.0,
               "clicks_now": float(clicks[cand].sum()) if cand else 0.0, "curve": {str(x): round(y, 4) for x, y in zip(xs, ys)}}
        for g in (1, 2, 3):
            gain = 0.0
            for i in cand:
                gain += max(impr[i] * ctr(max(1.0, pos[i] - g)) - clicks[i], 0.0)
            out[f"gain_{g}"] = round(gain)
        return out

    # Every recommended change at once: contextual links, the module, the
    # flagged swap and the sitewide content slots.
    moved_tpl.clear()
    e_all = reassign(with_swap(e_mod), content_slots, slot_in)
    pr_all = pr_of(e_all, template_pairs | moved_tpl)
    hc_all = harmonic(n, [p_[0] for p_ in e_all], [p_[1] for p_ in e_all])
    link_all = defaultdict(set)
    for s_, t_ in e_all:
        link_all[t_].add(s_)
    q_all = quadrants(pr_all)
    scen.append({"name": "Every change together", "lfs": lfs(pr_all), "priority": float(pr_all[pri_].sum()),
                 "changes": "contextual links, module, flagged swap and sitewide content slots", "caveat": scen[-1]["caveat"]})
    for x in scen:
        x["lfs"] = round(x["lfs"], 1)
        x["priority"] = round(x["priority"], 4)
    impact = {
        "lfs": [round(lfs(pr), 1), round(lfs(pr_all), 1)],
        "priority_share": [round(float(pr[pri_].sum()), 4), round(float(pr_all[pri_].sum()), 4)],
        "orphans": [len(orphans), sum(1 for t in priority if len(link_all[t]) <= 2)],
        "priority_reach_median": [round(float(np.median(hc0[pri_])), 3), round(float(np.median(hc_all[pri_])), 3)],
        "priority_reach_min": [round(float(hc0[pri_].min()), 3), round(float(hc_all[pri_].min()), 3)],
        "stars": [int(q0["star"].sum()), int(q_all["star"].sum())],
        "underlinked": [int(q0["underlinked"].sum()), int(q_all["underlinked"].sum())],
        "links_added": len(adds) + (len(e_mod) - len(e_adds)),
        "slots_moved": len(swap_out) + len(content_slots),
        "slots_need_traffic_check": (not has_gsc) and bool(content_slots),
        "hygiene_links": int(sum(hyg[k] [u] for k in ("to_redirect", "to_broken", "to_nonindexable") for u in hyg[k])),
        "near_wins_linked": int(sum(1 for i in np.where(near_win)[0] if new_in[i] > 0)) if has_gsc else None,
        "traffic": traffic_scenario(),
    }
    pri = np.array(priority)

    def share(p, m):
        return round(float(p[m].sum()), 4)

    # Click depth from the home page (shortest link path).
    home = next((i for i, u in enumerate(nodes) if urlsplit(u).path in ("", "/")), None)
    depth_of = np.full(n, -1)
    if home is not None:
        from collections import deque
        depth_of[home] = 0
        dq = deque([home])
        while dq:
            x = dq.popleft()
            for y in existing_orig[x]:
                if depth_of[y] < 0:
                    depth_of[y] = depth_of[x] + 1
                    dq.append(y)

    def anchor_review(t):
        c = anchors.get(t, Counter())
        total = sum(c.values())
        generic = sum(v for k, v in c.items() if GENERIC_ANCHOR.match(k))
        return {"url": nodes[t], "linking_pages": len(linking[t]), "anchor_links": total, "distinct_anchors": len(c),
                "generic_share": round(generic / total, 3) if total else 0.0,
                "top_anchors": [[k, v] for k, v in c.most_common(5)]}

    cannibal = []
    edit_secs = [x for x in set(secs) if x not in big_secs]
    for sname in edit_secs:
        idx_s = [i for i, x in enumerate(secs) if x == sname]
        if len(idx_s) < 2:
            continue
        M = Vn[idx_s] @ Vn[idx_s].T
        iu = np.triu_indices(len(idx_s), 1)
        hit = np.where(M[iu] >= 0.95)[0]
        for h in hit[np.argsort(-M[iu][hit])][:200]:
            i, j = idx_s[iu[0][h]], idx_s[iu[1][h]]
            cannibal.append((float(M[iu][h]), i, j))
    cannibal.sort(key=lambda x: -x[0])

    qnames = ["star", "underlinked", "leak", "long tail"]
    res = {
        "host": host, "pages": n, "internal_links": len(pairs), "leaked_links": leaked,
        "priority_pages": len(priority),
        "link_focus_score": {"now": round(lfs(pr), 1), "after_links": round(lfs(pr_a), 1), "after_links_and_swap": round(lfs(pr_b), 1)},
        "pagerank_share": {
            "priority": [share(pr, pri), share(pr_a, pri), share(pr_b, pri)],
            "on_topic_half": [share(v, topic >= 50) for v in (pr, pr_a, pr_b)],
            "off_topic_quarter": [share(v, topic < 25) for v in (pr, pr_a, pr_b)],
        },
        "quadrants": {k: {"pages": [int(q[k].sum()) for q in (q0, q_a, q_b)],
                          "pagerank": [share(v, q0[k]) for v in (pr, pr_a, pr_b)]} for k in qnames},
        "plain_focus": F["site"],
        "orphaned_priority": [{"url": nodes[t], "linking_pages": len(linking[t]), "pr": float(pr[t]),
                               "pr_after_links": float(pr_a[t]), "pr_after_swap": float(pr_b[t])} for t in orphans],
        "adds": dict(Counter(w for *_, w in adds)), "adds_total": len(adds),
        "swap": [{"out": nodes[o], "why": flag(o), "in": nodes[i]} for o, i in zip(swap_out, swap_in)],
        "sitewide_targets": len(sitewide),
        "sitewide_leaks": [{"url": nodes[t], "topic": round(float(topic[t]), 1), "title": info[nodes[t]]["title"]} for t in sitewide if topic[t] < 25 and t not in pset],
        "near_duplicates": near_dups,
        "reach": {
            "spearman_links": round(float(np.corrcoef(pctile(pr), reach0)[0, 1]), 3),
            "priority_reach_median": [round(float(np.median(hc0[pri])), 3), round(float(np.median(hca[pri])), 3)],
            "site_reach_median": [round(float(np.median(hc0)), 3), round(float(np.median(hca)), 3)],
            "priority": [{"url": nodes[i], "reach": round(float(reach0[i]), 1), "links": round(float(links0[i]), 1),
                          "hc_now": round(float(hc0[i]), 4), "hc_after": round(float(hca[i]), 4)} for i in priority],
            "links_ahead": [{"url": nodes[i], "title": info[nodes[i]]["title"], "reach": round(float(reach0[i]), 1), "links": round(float(links0[i]), 1),
                             "linking_pages": len(linking[i])} for i in np.argsort(reach_gap)[:20] if reach_gap[i] <= -25],
            "reach_ahead": [{"url": nodes[i], "title": info[nodes[i]]["title"], "reach": round(float(reach0[i]), 1), "links": round(float(links0[i]), 1),
                             "linking_pages": len(linking[i])} for i in np.argsort(-reach_gap)[:20] if reach_gap[i] >= 25],
        },
        "scenarios": scen,
        "impact": impact,
        "model": {"template_weight": a.template_weight, "template_links": len(template_pairs), "backlinks": bool(backlinks),
                  "backlink_pages": int((ext > 0).sum()) if backlinks else 0},
        "hygiene": {k: {"links": int(sum(v.values())), "targets": len(v), "top": [[u, c] for u, c in v.most_common(15)]} for k, v in hyg.items()},
        "rechecked": {"targets": len(live), "fine_now": sum(1 for st_, _ in live.values() if 200 <= st_ < 300)},
        "hygiene_sources": {k: [[u, c] for u, c in v.most_common(10)] for k, v in hyg_src.items()},
        "depth": {"distribution": dict(Counter("not reached" if d < 0 else (str(d) if d <= 5 else "6+") for d in depth_of)),
                  "priority_deep": [{"url": nodes[i], "depth": int(depth_of[i])} for i in priority if depth_of[i] < 0 or depth_of[i] > 3]},
        "anchors": [anchor_review(t) for t in priority],
        "cannibalization": [{"similarity": round(sm, 3), "a": nodes[i], "a_title": info[nodes[i]]["title"], "b": nodes[j], "b_title": info[nodes[j]]["title"],
                             "a_clicks": float(clicks[i]) if has_gsc else None, "b_clicks": float(clicks[j]) if has_gsc else None}
                            for sm, i, j in cannibal[:40]],
        "slot_scenario": [{"out": nodes[o], "title": info[nodes[o]]["title"], "clicks": float(clicks[o]) if has_gsc else None,
                           "in": nodes[i]} for o, i in zip(content_slots, slot_in)],
        "module_section": msec,
        "gsc": ({"matched_pages": int((clicks > 0).sum() + ((clicks == 0) & (impr > 0)).sum()), "total_clicks": float(clicks.sum()),
                 "protect_threshold": thr, "protected_pages": int(protected.sum()),
                 "protected_sitewide": [{"url": nodes[t], "clicks": float(clicks[t])} for t in sitewide if protected[t]],
                 "near_wins": [{"url": nodes[i], "title": info[nodes[i]]["title"], "position": float(pos[i]), "impressions": float(impr[i]),
                                "clicks": float(clicks[i]), "topic": round(float(topic[i]), 1), "new_links": int(new_in[i])}
                               for i in sorted(np.where(near_win)[0], key=lambda i: -impr[i])[:40]]}
                if has_gsc else None),
        "sections": [{"section": s, "pages": secs.count(s),
                      "topic_median": round(float(np.median(topic[[i for i, x in enumerate(secs) if x == s]])), 1),
                      "pr": [round(float(sum(p for x, p in zip(secs, v) if x == s)), 4) for v in (pr, pr_a, pr_b)],
                      "lfs_contribution": round(float(sum(p * t for x, p, t in zip(secs, pr, topic) if x == s)), 2)}
                     for s in sorted(set(secs), key=lambda s: -secs.count(s))],
    }
    with open(os.path.join(a.out, "pages.csv"), "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["url", "section", "topic", "links", "links_after", "reach", "reach_gap", "group", "priority", "linking_pages", "depth", "pagerank", "pagerank_after",
                    "external_linking_sites", "clicks", "impressions", "position", "protected", "near_win", "title"])
        la = pctile(pr_a)
        for i in np.argsort(-topic):
            g = next(k for k in qnames if q0[k][i])
            w.writerow([nodes[i], secs[i], round(float(topic[i]), 1), round(float(links0[i]), 1), round(float(la[i]), 1),
                        round(float(reach0[i]), 1), round(float(reach_gap[i]), 1), g, int(i in pset), len(linking[i]),
                        int(depth_of[i]) if depth_of[i] >= 0 else "", f"{pr[i]:.3e}", f"{pr_a[i]:.3e}", int(ext[i]) if backlinks else "", int(clicks[i]), int(impr[i]), round(float(pos[i]), 1) if pos[i] else "",
                        int(protected[i]), int(near_win[i]), info[nodes[i]]["title"]])
    # Where each new link goes: the paragraph on the source page that best
    # matches the target, and an anchor phrase already in that paragraph.
    need = {nodes[s_] for s_, _, _, _ in adds}
    texts, df = {}, Counter()
    for line in open(os.path.join(a.crawl, "pages.jsonl")):
        r = json.loads(line)
        if not r.get("text"):
            continue
        f_ = redirect.get(r["url"], r["url"])
        blocks_ = [b.strip() for b in r["text"].split("\n") if b.strip()]
        df.update(set(blocks_))
        if f_ in need:
            texts[f_] = blocks_
    npages = max(len(fp), 1)
    cutoff = max(20, 0.15 * npages)
    gdf, ndocs = Counter(), 0
    for line in open(os.path.join(a.crawl, "pages.jsonl")):
        r = json.loads(line)
        if r.get("text"):
            ndocs += 1
            gdf.update(set(t for b in r["text"].split("\n") if b.strip() and df[b.strip()] <= cutoff for t in _tokens(b)))
    import math as _math
    idf = {t: _math.log(ndocs / (1 + c)) for t, c in gdf.items()}
    distinctive = {t for t, c in gdf.items() if c / max(ndocs, 1) < 0.10}
    llm_left = 150 if a.anchor_model else 0
    instr = {}
    for s_, t_, sm, why in adds:
        tu = nodes[t_]
        h1 = info[tu]["h1"] or ""
        title = clean_title(h1 if 8 <= len(h1) <= 70 and "*" not in h1 else info[tu]["title"])
        terms = set(_tokens(title + " " + (info[tu]["title"] or "")))
        body = [b for b in texts.get(nodes[s_], []) if df[b] <= cutoff]
        para, anchor, sc = place_link(body, terms, idf, distinctive)
        in_text = bool(anchor)
        if not anchor and para and llm_left and why.startswith(("orphan", "priority")):
            llm_left -= 1
            anchor = llm_anchor(a.ollama, a.anchor_model, para, title)
            in_text = bool(anchor)
        if not anchor:
            anchor = title
        if para and in_text:
            how = f'In the paragraph starting "{para[:90]}", link the words "{anchor}" to {tu}'
        elif para:
            how = f'After the paragraph starting "{para[:90]}", add a sentence that links "{anchor}" to {tu}'
        else:
            how = f'Add a link with the text "{anchor}" to {tu} where the topic comes up'
        instr[(s_, t_)] = (anchor, int(in_text), para[:300], how)

    res["anchors_in_text"] = [sum(x[1] for x in instr.values()), len(instr)]
    with open(os.path.join(a.out, "link_adds.csv"), "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["source", "target", "similarity", "reason", "suggested_anchor", "anchor_in_text", "paragraph", "instruction"])
        order = {"orphan: parent hub": 0, "orphan: similar page": 1, "priority": 2, "near win": 3, "underlinked": 4}
        for s_, t_, sm, why in sorted(adds, key=lambda x: (order[x[3]], -x[2])):
            an, it, pa, how = instr[(s_, t_)]
            w.writerow([nodes[s_], nodes[t_], round(sm, 3), why, an, it, pa, how])
    with open(os.path.join(a.out, "sitewide.csv"), "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["target", "section", "linked_from_pages", "topic", "pagerank_share", "flag", "swap", "slot_scenario", "clicks", "protected", "title"])
        for t in sitewide:
            w.writerow([nodes[t], secs[t], indeg[t], round(float(topic[t]), 1), f"{pr[t]:.4f}",
                        flag(t) or ("leak" if topic[t] < 25 else ""), int(t in swap_out), int(t in content_slots),
                        int(clicks[t]) if has_gsc else "", int(protected[t]) if has_gsc else "", info[nodes[t]]["title"]])
    with open(os.path.join(a.out, "hygiene.csv"), "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["issue", "target", "links_to_it", "points_to", "status", "rechecked_live"])
        names = {"to_redirect": "links to a redirect", "to_broken": "links to a broken page", "to_nonindexable": "links to a noindexed or canonicalised page",
                 "rate_limited": "not checked (rate limited)", "to_uncrawled": "links to a blocked or unfetched page", "nofollow": "internal nofollow",
                 "account": "sign-in, sign-up, account or cart link", "page_nofollow": "page with meta robots nofollow (its links pass nothing)"}
        for k, v in hyg.items():
            for u, c in v.most_common():
                w.writerow([names[k], u, c, finals.get(u, "") if k == "to_redirect" else "", status.get(u, ""), int(u in live)])
    json.dump(res, open(os.path.join(a.out, "plan.json"), "w"), indent=1)
    print(json.dumps({k: res[k] for k in ("pages", "link_focus_score", "adds")}, indent=1))
    for x in scen:
        print(f"  {x['name']}: score {x['lfs']}, priority {100 * x['priority']:.1f}%")
    print("orphaned priority pages:", len(orphans))


if __name__ == "__main__":
    main()
