#!/usr/bin/env python3
"""Site focus and site radius from page embeddings, modelled on the
siteFocusScore / siteRadius / siteEmbedding / pageEmbedding attributes named
in Google's Content Warehouse API documentation (leaked May 2024).

The leak names these attributes and says what they describe. It gives no
formula, model, threshold or weight. This script computes a transparent proxy
so you can see which pages and sections pull a site off its topic:

  pageEmbedding   nomic-embed-text vector of title + H1 + main text, with
                  site-wide boilerplate blocks removed
  siteEmbedding   mean of the unit page vectors (optionally PageRank-weighted)
  page distance   1 - cosine(page, siteEmbedding)
  siteRadius      median and 90th percentile page distance
  siteFocus       length of the mean unit vector, 0 to 1. Equals the square
                  root of the average pairwise cosine similarity: 1 means every
                  page says the same thing, near 0 means no shared topic.

Usage:
  site_focus.py --pages work/crawl/pages.jsonl --out work/focus \
      [--ollama http://localhost:11434] [--model nomic-embed-text] [--k 12]
  The Ollama URL defaults to $SITEFOCUS_OLLAMA, else http://localhost:11434.

Writes out/vectors.npy, out/pages.json (per page), out/focus.json (site,
section, cluster summaries and drop-one-section what-ifs).
Needs numpy. Embeddings are cached; rerun is cheap.
"""
import argparse
import json
import os
import re
import urllib.request
from collections import Counter, defaultdict
from urllib.parse import urlsplit

import hashlib

import numpy as np

try:
    from .common import read_pages
except ImportError:
    from common import read_pages


def section_of(url):
    parts = [p for p in urlsplit(url).path.split("/") if p]
    return parts[0] if parts else "(home)"


def _post(base, model, texts, timeout):
    body = json.dumps({"model": model, "input": texts, "truncate": True}).encode()
    req = urllib.request.Request(f"{base}/api/embed", data=body, headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.load(r)["embeddings"]


def _robust(base, model, texts, timeout=20, tries=3):
    for attempt in range(tries):
        try:
            return _post(base, model, texts, timeout)
        except Exception as ex:
            if len(texts) > 1:
                h = len(texts) // 2
                return _robust(base, model, texts[:h], timeout) + _robust(base, model, texts[h:], timeout)
            if attempt == tries - 1:
                raise
            print(f"  retry single text after {ex}", flush=True)


def embed(texts, base, model, batch=16, cache=None, ids=None):
    """Embed with retries. Progress is checkpointed to cache as {"ids", "vecs"};
    a restart reuses every vector whose id (page URL + chunk number) still
    matches, so a stalled or killed run never loses finished work."""
    ids = ids or [str(i) for i in range(len(texts))]
    out = []
    if cache and os.path.exists(cache):
        try:
            saved = json.load(open(cache))
            if isinstance(saved, dict):
                k = 0
                while k < len(saved["ids"]) and k < len(ids) and saved["ids"][k] == ids[k]:
                    k += 1
                out = saved["vecs"][:k]
        except Exception:
            out = []
        print(f"  resuming at {len(out)} of {len(texts)}", flush=True)
    import time as _t
    t0 = _t.time()
    for i in range(len(out), len(texts), batch):
        tb = _t.time()
        out += _robust(base, model, texts[i:i + batch])
        dt = _t.time() - tb
        if dt > 20:
            print(f"  slow batch at {i}: {dt:.0f}s", flush=True)
        if cache and ((i // batch) % 10 == 0 or i + batch >= len(texts)):
            json.dump({"ids": ids[:len(out)], "vecs": out}, open(cache, "w"))
            rate = (len(out)) / max(_t.time() - t0, 1) * 60
            print(f"  embedded {len(out)}/{len(texts)} ({rate:.0f}/min)", flush=True)
    if cache and os.path.exists(cache):
        os.remove(cache)
    return np.array(out, dtype=np.float32)


def kmeans(X, k, iters=40, seed=7):
    """Cosine k-means++ on unit vectors. k is capped at the number of distinct
    vectors; seeding stops early when every point already has a centre."""
    rng = np.random.default_rng(seed)
    k = max(1, min(k, len(np.unique(np.round(X, 6), axis=0))))
    C = [X[rng.integers(len(X))]]
    for _ in range(1, k):
        d = 1 - np.max(X @ np.array(C).T, axis=1)
        p = np.clip(d, 0, None) ** 2
        if p.sum() <= 1e-12:
            break
        C.append(X[rng.choice(len(X), p=p / p.sum())])
    C = np.array(C)
    k = len(C)
    for _ in range(iters):
        lab = np.argmax(X @ C.T, axis=1)
        newC = np.array([X[lab == j].mean(0) if np.any(lab == j) else C[j] for j in range(k)])
        nn = np.linalg.norm(newC, axis=1, keepdims=True)
        newC = newC / np.where(nn == 0, 1, nn)
        if np.allclose(newC, C, atol=1e-6):
            break
        C = newC
    return lab, C


def focus_stats(V, w=None):
    w = np.ones(len(V)) if w is None else np.asarray(w, dtype=np.float64)
    m = (V * w[:, None]).sum(0) / w.sum()
    focus = float(np.linalg.norm(m))
    c = m / focus if focus > 0 else m
    dist = 1 - V @ c
    return focus, c, dist


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--pages", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--ollama", default=os.environ.get("SITEFOCUS_OLLAMA") or os.environ.get("CCRA_OLLAMA") or "http://localhost:11434")
    ap.add_argument("--model", default="nomic-embed-text")
    ap.add_argument("--k", type=int, default=12)
    ap.add_argument("--chunks", type=int, default=3, help="embed up to this many ~5,500-character windows per page and average them")
    ap.add_argument("--boilerplate", type=float, default=0.15, help="drop text blocks on more than this share of pages")
    a = ap.parse_args()
    os.makedirs(a.out, exist_ok=True)

    recs = []
    for r in read_pages(a.pages):
        if r.get("status") != 200 or "text" not in r:
            continue
        final = (r.get("final") or r["url"]).split("#")[0]
        if final.count("/") == 2:
            final += "/"
        canon = (r.get("canonical") or final).split("#")[0]
        robots = (r.get("robots") or "") + " " + (r.get("x_robots") or "")
        r["indexable"] = "noindex" not in robots and "none" not in robots.split() and canon.rstrip("/") == final.rstrip("/")
        r["nofollow_page"] = "nofollow" in robots or "none" in robots.split()
        r["final"] = final
        recs.append(r)
    seen, pages = set(), []
    for r in recs:
        if r["final"] in seen:
            continue
        seen.add(r["final"])
        pages.append(r)

    if not pages:
        raise SystemExit("no crawled pages with status 200 and HTML text; check the crawl output and robots.txt")
    blocks = [set(b.strip() for b in r["text"].split("\n") if len(b.strip()) > 2) for r in pages]
    freq = Counter(b for bs in blocks for b in bs)
    cut = max(20, a.boilerplate * len(pages))
    # Long pages are embedded in up to --chunks windows of ~5,500 characters
    # and averaged, so an article is scored on more than its opening.
    docs, owner, main_words = [], [], []
    for pi, (r, bs) in enumerate(zip(pages, blocks)):
        body = [b for b in r["text"].split("\n") if b.strip() and freq[b.strip()] <= cut]
        main = " ".join(body)
        main_words.append(len(main.split()))
        head = f"{r.get('title', '')}\n{r.get('h1', '')}\n"
        step = 5500
        parts = [main[i:i + step] for i in range(0, max(len(main), 1), step)][: max(a.chunks, 1)]
        for part in parts:
            docs.append(f"search_document: {head}{part}"[:6000])
            owner.append(pi)

    # Embeddings are cached per chunk, keyed by a hash of the model and the
    # exact text sent. Changed pages are re-embedded; unchanged ones are not.
    hashes = [hashlib.sha1(f"{a.model}\x00{d}".encode("utf-8", "replace")).hexdigest() for d in docs]
    ck_path, cv_path = os.path.join(a.out, "chunks.keys.json"), os.path.join(a.out, "chunks.npy")
    cache = {}
    if os.path.exists(ck_path) and os.path.exists(cv_path):
        try:
            cache = dict(zip(json.load(open(ck_path)), np.load(cv_path)))
        except Exception:
            cache = {}
    missing = [i_ for i_, h in enumerate(hashes) if h not in cache]
    if missing:
        print(f"embedding {len(missing)} of {len(docs)} chunks ({len(pages)} pages) with {a.model}", flush=True)
        new = embed([docs[i_] for i_ in missing], a.ollama, a.model,
                    cache=os.path.join(a.out, "vectors.partial.json"), ids=[hashes[i_] for i_ in missing])
        for i_, v in zip(missing, new):
            cache[hashes[i_]] = v
    else:
        print(f"all {len(docs)} chunks already embedded; reusing", flush=True)
    C = np.array([cache[h] for h in hashes], dtype=np.float32)
    cn = np.linalg.norm(C, axis=1, keepdims=True)
    C = C / np.where(cn == 0, 1, cn)
    V = np.zeros((len(pages), C.shape[1]), dtype=np.float32)
    np.add.at(V, np.array(owner), C)

    def _atomic(path, writer):
        tmp = path + ".tmp"
        writer(tmp)
        os.replace(tmp, path)
    # Commit everything only after embedding succeeded.
    _atomic(cv_path, lambda t: np.save(open(t, "wb"), C))
    _atomic(ck_path, lambda t: json.dump(hashes, open(t, "w")))
    _atomic(os.path.join(a.out, "vectors.npy"), lambda t: np.save(open(t, "wb"), V))
    _atomic(os.path.join(a.out, "vectors.keys.json"), lambda t: json.dump([r["final"] for r in pages], open(t, "w")))
    _atomic(os.path.join(a.out, "vectors.meta.json"), lambda t: json.dump(
        {"model": a.model, "chunks": a.chunks, "boilerplate": a.boilerplate, "boilerplate_min_pages": 20, "docs": len(docs)}, open(t, "w")))
    vn = np.linalg.norm(V, axis=1, keepdims=True)
    V = V / np.where(vn == 0, 1, vn)

    idx = np.array([r["indexable"] for r in pages])
    if not idx.any():
        raise SystemExit("none of the crawled pages are indexable (all noindex, canonicalised elsewhere or non-200); nothing to analyse")
    Vi = V[idx]
    focus, c, dist_i = focus_stats(Vi)
    dist = 1 - V @ c
    lab, C = kmeans(Vi, a.k)
    k_used = len(C)
    lab_all = np.argmax(V @ C.T, axis=1)

    secs = [section_of(r["final"]) for r in pages]
    sec_i = [s for s, ok in zip(secs, idx) if ok]

    def summarise(groups, labels):
        out = []
        for g in sorted(set(labels), key=lambda g: -labels.count(g)):
            m = np.array([l == g for l in labels])
            f, gc, _ = focus_stats(Vi[m])
            out.append({"group": str(g), "pages": int(m.sum()), "share": round(float(m.mean()), 3),
                        "focus": round(f, 3), "median_distance": round(float(np.median(dist_i[m])), 3),
                        "centroid_similarity": round(float(gc @ c), 3)})
        return out

    whatif = []
    for s in sorted(set(sec_i), key=lambda s: -sec_i.count(s)):
        keep = np.array([x != s for x in sec_i])
        if keep.sum() < 10 or sec_i.count(s) < 5:
            continue
        f2, _, d2 = focus_stats(Vi[keep])
        whatif.append({"without": s, "pages_removed": sec_i.count(s), "focus": round(f2, 3),
                       "focus_change": round(f2 - focus, 3), "radius_median": round(float(np.median(d2)), 3)})

    clusters = []
    for j in range(k_used):
        m = lab == j
        if not m.any():
            continue
        members = [p for p, ok in zip(pages, idx) if ok]
        ms = [members[i] for i in np.where(m)[0]]
        near = sorted(range(len(ms)), key=lambda i: -float(Vi[np.where(m)[0][i]] @ C[j]))[:8]
        clusters.append({"cluster": j, "pages": int(m.sum()), "share": round(float(m.mean()), 3),
                         "similarity_to_site": round(float(C[j] @ c), 3),
                         "sections": dict(Counter(section_of(x["final"]) for x in ms).most_common(4)),
                         "examples": [ms[i].get("title", "")[:90] for i in near]})
    clusters.sort(key=lambda x: -x["similarity_to_site"])

    rows = [{"url": r["final"], "section": s, "indexable": bool(ok), "nofollow_page": bool(r.get("nofollow_page")), "title": r.get("title", "")[:140],
             "h1": r.get("h1", "")[:140], "main_words": w, "distance": round(float(d), 4), "cluster": int(l)}
            for r, s, ok, w, d, l in zip(pages, secs, idx, main_words, dist, lab_all)]
    json.dump(rows, open(os.path.join(a.out, "pages.json"), "w"))
    res = {
        "model": a.model, "chunks": a.chunks, "boilerplate_share": a.boilerplate, "boilerplate_cut": int(cut), "clusters_used": k_used,
        "pages_fetched_ok": len(pages), "pages_indexable": int(idx.sum()),
        "boilerplate_blocks_removed": sum(1 for b, n in freq.items() if n > cut),
        "site": {"focus": round(focus, 3), "radius_median": round(float(np.median(dist_i)), 3),
                 "radius_p90": round(float(np.percentile(dist_i, 90)), 3)},
        "sections": summarise(None, sec_i),
        "drop_one_section": sorted(whatif, key=lambda x: -x["focus_change"]),
        "clusters": clusters,
        "most_off_focus": sorted([x for x in rows if x["indexable"]], key=lambda x: -x["distance"])[:40],
        "most_on_focus": sorted([x for x in rows if x["indexable"]], key=lambda x: x["distance"])[:25],
        "caveat": "Proxy for leaked attribute names; Google's actual formula, model and use are not public.",
    }
    json.dump(res, open(os.path.join(a.out, "focus.json"), "w"), indent=1)
    np.save(os.path.join(a.out, "site_centroid.npy"), c)
    print(json.dumps(res["site"]), f"{len(pages)} pages, {int(idx.sum())} indexable")


if __name__ == "__main__":
    main()
