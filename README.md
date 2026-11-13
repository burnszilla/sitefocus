# sitefocus

By [Stephen Burns](https://stephenburns.ai), technical SEO and AI visibility consultant.

**New here? Read the [user guide](docs/GUIDE.md):** install, a first run, choosing priority pages, connecting Search Console, reading every section of the report, shipping the changes and measuring them, the formulas, and troubleshooting.

Site focus and internal linking in one tool. It answers one question: **is your site's link importance going to the pages that matter, and what links would fix it?**

It crawls a site, reads every page with a local AI embedding model, scores each page on topic and on internal PageRank, and builds a linking plan that it simulates before you ship anything. By default everything runs on your machine: page text goes only to the Ollama server you point it at (localhost unless you set `SITEFOCUS_OLLAMA`).

## The idea in one paragraph

Every page gets two scores from 0 to 100. **Topic**: how close the page is to your priority (product or money) pages. **Links**: how much internal PageRank the page holds. The **Link Focus Score** is the average topic score weighted by PageRank: 100 means all link importance sits on the most on-topic pages, 50 means it is spread with no regard to topic. Splitting both scores at the middle gives a **page map**:

| | weakly linked | well linked |
| --- | --- | --- |
| **on topic** | Underlinked: send links here | Star: keep |
| **off topic** | Long tail: leave, consolidate or noindex | Leak: review sitewide links pointing here |

A third score, **reach**, is internal harmonic centrality: the average of 1 ÷ clicks from every other page (1.0 = one click from everywhere, 0 = unreachable). PageRank says how much link weight flows into a page; reach says how easily the rest of the site gets there. Pages whose PageRank runs far ahead of their reach are fed by a few strong links instead of being woven into the site.

The plan fixes orphaned priority pages first, then sends links to near wins (with Search Console data) and to underlinked pages, and reruns PageRank to show the change. It also simulates bigger, template-level changes, because sitewide navigation and footer links usually carry most of a site's internal PageRank.

## Install

Python 3.9 or later. From GitHub:

```
pip install "git+https://github.com/burnszilla/sitefocus"
```

Or from a release wheel (no network to GitHub needed): download it from the Releases page, then

```
pip install sitefocus-1.5.2-py3-none-any.whl
```

Ollama and one embedding model (about 270 MB):

```
brew install ollama            # or the installer from https://ollama.com
ollama serve &                 # if it is not already running
ollama pull nomic-embed-text
```

Your contact email goes in the crawler's User-Agent so site owners can reach you:

```
export SITEFOCUS_CONTACT=you@example.com
sitefocus doctor
```

## Run

1. Put the priority pages in `priority.txt`, one URL per line. A product sitemap is a good start.
2. Run:

```
sitefocus run https://www.example.com/ \
  --sitemap https://www.example.com/sitemap.xml \
  --priority priority.txt \
  --out work/example
open work/example/report/index.html
```

The crawl reads robots.txt before any page and obeys it (as `User-agent: sitefocus`, including Crawl-delay) for every request: pages, every redirect hop, the start URL's redirects to another host (that host's robots.txt is read first) and sitemaps. It stays on the start URL's final host and port (http and https count as one site). Requests are spaced at least half a second apart across all workers (`--delay`), and every 429 or 503 pauses all of them, honouring Retry-After. Crawl and embedding are both resumable: run the same command again to continue; pages that failed with an error, 429 or 5xx are tried again, and finished work is reused. After shipping changes, rerun with `--fresh` to recrawl; embeddings are cached by content, so only changed pages are re-embedded. A 5,000-page site takes about an hour to crawl and 10 to 20 minutes to embed.

## Connect Search Console (recommended)

`sitefocus run` asks you to connect if you have not. Once connected it finds the right property for the site and pulls clicks, impressions and position for every page on its own. Access is read-only.

Option 1, an OAuth desktop client (works anywhere):

1. Google Cloud Console, APIs & Services: enable **Google Search Console API**.
2. Credentials, Create credentials, **OAuth client ID**, application type **Desktop app**, Download JSON.
3. `sitefocus gsc-login --client-secret client_secret.json`. Your browser opens; approve read-only access once.

Option 2, if you already use gcloud:

```
gcloud auth application-default login --scopes=https://www.googleapis.com/auth/webmasters.readonly,https://www.googleapis.com/auth/cloud-platform
```

Then `sitefocus gsc-status` and `sitefocus gsc-sites` to check. The Google account needs access to the site's Search Console property.

**Privacy:** sitefocus ships with no credentials. You use your own Google OAuth client or gcloud login. With `gsc-login` the refresh token is stored only on your machine in `~/.config/sitefocus/gsc.json`, readable only by you; with gcloud, sitefocus reads gcloud's own login and stores nothing. Search Console requests go only to Google's API (the endpoints can be overridden with environment variables, for testing). `rm ~/.config/sitefocus/gsc.json` and https://myaccount.google.com/permissions disconnect it.

## Better inputs, better decisions

Both are optional. Search Console data comes from the connection above, or from a CSV export if you prefer:

- `--gsc pages.csv`: Search Console, Performance, Pages tab, last 3 months, Export. Pages that earn the most clicks are protected (never swapped out of sitewide slots); on-topic pages ranking 4 to 20 with above-median impressions become "near wins" that get links right after the orphaned priority pages; clicks, impressions and position are added to `pages.csv` and to the near-duplicate review.
- `--backlinks links.csv`: Search Console, Links, Top linked pages (externally), Export; or an Ahrefs or Semrush "best by links" export. External links put PageRank into specific pages; with this file the model starts each page from its real external strength instead of treating every page equally.

```
sitefocus run https://www.example.com/ --sitemap https://www.example.com/sitemap.xml \
  --priority priority.txt --gsc pages.csv --backlinks links.csv --out work/example
```

## What you get

`work/example/report/`:

- `index.html`:
  - an **estimated impact** box (every change at once: Link Focus Score, priority pages' PageRank share, orphans, reach, Stars; with `--gsc`, a what-if range of extra clicks if strengthened pages gain 1 to 3 positions, from the site's own click-through curve)
  - **how to execute, in order**: a numbered guide with scope, the list to work from, what to check before shipping, and how to measure
  - Link Focus Score now and after the plan, and **what moves the needle**: contextual links, the flagged sitewide swap, a related-products module, and reassigning sitewide content slots, each simulated
  - the page map (topic against link score) with a **before and after overlay**, PageRank by group, section and orphaned page
  - Search Console near wins and protected pages (with `--gsc`)
  - **link hygiene**: internal links to redirects, broken pages and noindexed pages, existing internal nofollow, sign-in and account links
  - **where nofollow belongs** (and why it cannot steer PageRank)
  - **reach** (internal harmonic centrality) for priority pages, and pages whose PageRank runs ahead of their reach
  - **click depth**, **anchor text** on priority pages, **possible cannibalization** (near-identical editorial pages)
  - links to add, sitewide links, results by section, topic clusters, method and limits
- `execution/`: a CSV for each step of the guide at the top of the report that has rows to work from (link hygiene, orphaned priority pages, flagged sitewide slots, near wins, contextual links, underlinked pages, sitewide content slots, near-duplicates). The baseline, module test and recrawl steps have no list. Regenerating the report replaces only the files it wrote before (listed in `execution/.sitefocus-manifest.json`); your own files there are never touched.
- `pages.csv`: every page with its topic score, link score, reach and group
- `link_adds.csv`: every recommended link with similarity, reason, the anchor, whether that anchor is already in the text, the paragraph to put it in, and a one-line instruction ("In the paragraph starting '…', link the words '…' to …"). `--anchor-model qwen3:8b` lets a local model suggest anchors where no phrase fits.
- `hygiene.csv` (in the plan folder): every link-hygiene issue. Links to redirects, broken and rate-limited targets are rechecked live first (up to 200 targets, robots.txt obeyed, skip with `--no-recheck`); `rechecked_live` says which were
- `sitewide.csv`: every page linked from at least half the site (`--template-share`, default 0.5), with flags

Add your own findings to the top of the report:

```
sitefocus report --focus work/example/embed --plan work/example/plan --site example.com \
  --notes notes.txt --out work/example/report/index.html
```

## How to read it

- **Orphaned priority pages** (two or fewer internal links in) are usually the cheapest win; the report's simulation shows how big it is on your site. Before you report one, open the home page and the parent hub in a real browser; some menus are built in JavaScript and the crawler reads server HTML.
- **Sitewide swap** reassigns navigation or footer slots that point at single directory entries, campaign pages or stale pages to orphaned priority pages. It is a separate scenario because it is a business decision.
- **Sitewide leaks** are well-linked pages with a low topic score. They are often valuable editorial pages. Review them; never remove content to raise a score.
- **Why topic is anchored on priority pages:** a centre computed from the average page is set by whatever the site has most of. On a site where most pages are templated directory listings, that centre calls the editorial library off-topic. The report shows that plain measure too, for comparison.
- **The simulation** weights navigation, footer and sitewide links at half a link in body copy (`--template-weight`), and includes external links when you pass `--backlinks`. It is still a model: read it as direction and relative size, ship in stages, and measure in Search Console.
- **Nofollow** does not steer PageRank. Since 2009 a nofollowed link still spends its share of the page's PageRank and the share is discarded. To stop sending importance to a page, remove or move the link. Use nofollow only where it belongs: sign-in and account links, `rel="sponsored"` for paid links, `rel="ugc"` for user content.
- **Long pages** are embedded in up to three windows of about 5,500 characters and averaged (`--chunks`), so long articles are judged on more than their opening.

## Where the idea comes from

Google's Content Warehouse API documentation, leaked in May 2024, names attributes such as siteFocusScore, siteRadius, siteEmbeddings and pageEmbedding. It publishes no formulas, models or weights, and does not say how they are used in ranking. sitefocus computes transparent proxies; treat them as diagnostics.

## Individual steps

```
sitefocus crawl  --start URL --sitemap URL --out work/crawl
sitefocus embed  --pages work/crawl/pages.jsonl --out work/embed
sitefocus plan   --crawl work/crawl --focus work/embed --priority priority.txt --out work/plan
sitefocus report --focus work/embed --plan work/plan --site example.com --out work/report/index.html
```

## Tests

```
python -m unittest discover -s tests -v
```

105 tests, offline: PageRank against an independent dense implementation, exact harmonic centrality against brute force, Search Console and backlink export parsing, link placement rules, and an end-to-end run against a local fixture site (robots.txt, sitemap-only orphan, redirects, 404, a page that rate-limits once, meta noindex, X-Robots-Tag, page-level nofollow, a JavaScript-only link) with a fake embedding server, and the Search Console connection against a fake Google API (credentials, property choice, pagination past 25,000 rows, scope checks), plus regression tests from three independent code reviews: hostile page titles, redirects off the site or into robots-blocked paths (page, start, robots.txt and sitemap redirects), robots.txt server errors, rate limits, oversized pages, charsets, `<base href>`, malformed links, damaged crawl files, interrupted and reordered embedding caches, empty embeddings, nofollow modelling per scenario, normalised shares, report cleanup and file collisions, robots.txt rules addressed to sitefocus, robots.txt errors after redirects, per-scheme rules, combined robots directives, short embedding replies, credential redirects, temporary-file safety, malformed Crawl-delay values and CSV formula injection. GitHub Actions runs them on Python 3.9 and 3.13, on Linux and macOS.

## Settings

| Variable | Default | Purpose |
| --- | --- | --- |
| SITEFOCUS_CONTACT | none | your email, sent in the crawler's User-Agent |
| SITEFOCUS_OLLAMA | http://localhost:11434 | Ollama server for embeddings |
| SITEFOCUS_BACKOFF | 30 | seconds to wait after a 429 or 503 without Retry-After (30, then 60, then 90) |
| SITEFOCUS_GSC_CLIENT | none | path to your OAuth client_secret.json for `gsc-login` |
| SITEFOCUS_CONFIG_DIR | ~/.config/sitefocus | where the Search Console token is stored |

## Claude Code

The `skill/` folder (in this repo and in the release zip) is a Claude Code skill. Copy it to `~/.claude/skills/site-focus/` and Claude will run sitefocus for you.

## License

MIT, see [LICENSE](LICENSE). Use it, change it and share it, commercially too; keep the copyright notice. No warranty.
