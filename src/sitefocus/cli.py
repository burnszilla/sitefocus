"""sitefocus: site focus and internal linking.

  sitefocus run <home URL> --sitemap URL --priority priority.txt --out DIR
  sitefocus <step> [options]

Commands
  run       crawl, embed, plan and report in one go (resumable)
  crawl     polite same-host crawl
  embed     page embeddings, topic clusters, plain site focus
  plan      Link Focus Score, page map and linking plan, simulated
  report    HTML report from embed + plan output
  doctor    check Python, numpy, Ollama and the embedding model

Run `sitefocus <command> -h` for options.
Settings (environment): SITEFOCUS_CONTACT (your email, sent in the crawler's
User-Agent), SITEFOCUS_OLLAMA (default http://localhost:11434).
"""
import argparse
import json
import os
import shutil
import sys
import urllib.request

COMMANDS = {"crawl": "site_crawl", "embed": "site_focus", "plan": "link_plan", "report": "focus_report"}


def _env():
    # Accept the older CCRA_* names too.
    for new, old in (("SITEFOCUS_CONTACT", "CCRA_CONTACT"), ("SITEFOCUS_OLLAMA", "CCRA_OLLAMA")):
        if os.environ.get(new) and not os.environ.get(old):
            os.environ[old] = os.environ[new]


def _run(module, argv):
    _env()
    mod = __import__(f"sitefocus.{module}", fromlist=["main"])
    old = sys.argv
    sys.argv = [f"sitefocus {module}"] + list(argv)
    try:
        return mod.main()
    finally:
        sys.argv = old


def doctor():
    _env()
    ok = True
    print(f"python {sys.version.split()[0]}")
    try:
        import numpy
        print(f"numpy {numpy.__version__}")
    except ImportError:
        ok = False
        print("numpy MISSING: pip install numpy")
    base = os.environ.get("CCRA_OLLAMA", "http://localhost:11434")
    try:
        with urllib.request.urlopen(f"{base}/api/tags", timeout=5) as r:
            names = [m["name"] for m in json.load(r)["models"]]
        print(f"ollama {base}: {len(names)} models")
        if not any(n.startswith("nomic-embed-text") for n in names):
            ok = False
            print("  nomic-embed-text MISSING: ollama pull nomic-embed-text")
    except Exception as ex:
        ok = False
        print(f"ollama {base} NOT REACHABLE ({ex}). Install from https://ollama.com, run `ollama serve`, then `ollama pull nomic-embed-text`")
    contact = os.environ.get("CCRA_CONTACT")
    print(f"contact {contact or 'NOT SET (export SITEFOCUS_CONTACT=you@example.com)'}")
    ok = ok and bool(contact)
    print("ready" if ok else "not ready")
    return 0 if ok else 1


def run(argv):
    ap = argparse.ArgumentParser(prog="sitefocus run", description="Crawl, embed, plan and report for one site.")
    ap.add_argument("start", help="home page URL, e.g. https://www.example.com/")
    ap.add_argument("--sitemap", action="append", default=[], help="sitemap or sitemap index URL (repeatable)")
    ap.add_argument("--priority", required=True, help="file with the priority (product or money) URLs, one per line")
    ap.add_argument("--out", required=True, help="working directory")
    ap.add_argument("--max-pages", type=int, default=8000)
    ap.add_argument("--notes", help="optional findings, one per line, shown first in the report")
    ap.add_argument("--k", type=int, default=12, help="number of topic clusters")
    ap.add_argument("--gsc", help="Search Console Pages export (CSV): protects pages that earn clicks, targets near wins")
    ap.add_argument("--backlinks", help="backlink export (Search Console Top linked pages, Ahrefs or Semrush CSV)")
    ap.add_argument("--chunks", type=int, default=3, help="embedding windows per long page")
    ap.add_argument("--no-recheck", action="store_true", help="skip the live re-check of flagged link targets")
    ap.add_argument("--anchor-model", help="optional local Ollama model for anchor suggestions, e.g. qwen3:8b")
    a = ap.parse_args(argv)
    w = lambda *p: os.path.join(a.out, *p)
    os.makedirs(a.out, exist_ok=True)
    crawl = ["--start", a.start, "--out", w("crawl"), "--max-pages", str(a.max_pages)]
    for s in a.sitemap:
        crawl += ["--sitemap", s]
    _run("site_crawl", crawl)
    _run("site_focus", ["--pages", w("crawl", "pages.jsonl"), "--out", w("embed"), "--k", str(a.k), "--chunks", str(a.chunks)])
    plan = ["--crawl", w("crawl"), "--focus", w("embed"), "--priority", a.priority, "--out", w("plan")]
    if a.gsc:
        plan += ["--gsc", a.gsc]
    if a.backlinks:
        plan += ["--backlinks", a.backlinks]
    if a.no_recheck:
        plan += ["--no-recheck"]
    if a.anchor_model:
        plan += ["--anchor-model", a.anchor_model]
    _run("link_plan", plan)
    host = a.start.split("://", 1)[-1].split("/")[0]
    rep = ["--focus", w("embed"), "--plan", w("plan"), "--site", host, "--out", w("report", "index.html")]
    if a.notes:
        rep += ["--notes", a.notes]
    _run("focus_report", rep)
    print(f"report: {w('report', 'index.html')}")
    return 0


def main():
    if len(sys.argv) < 2 or sys.argv[1] in ("-h", "--help", "help"):
        print(__doc__)
        return 0
    cmd, argv = sys.argv[1], sys.argv[2:]
    if cmd == "doctor":
        return doctor()
    if cmd == "run":
        return run(argv)
    if cmd in COMMANDS:
        return _run(COMMANDS[cmd], argv) or 0
    print(f"unknown command: {cmd}\n\n{__doc__}")
    return 2


if __name__ == "__main__":
    sys.exit(main())
