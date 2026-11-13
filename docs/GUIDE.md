# sitefocus user guide

For technical SEOs who want to know where a site's internal link equity goes, whether it lands on the pages that matter, and exactly which links to change.

By [Stephen Burns](https://stephenburns.ai).

---

## Contents

1. [What sitefocus does](#1-what-sitefocus-does)
2. [Install](#2-install)
3. [First run in 10 minutes](#3-first-run-in-10-minutes)
4. [Choose your priority pages](#4-choose-your-priority-pages)
5. [Connect Search Console and add backlinks](#5-connect-search-console-and-add-backlinks)
6. [Run a real audit](#6-run-a-real-audit)
7. [Read the report](#7-read-the-report)
8. [The CSV files](#8-the-csv-files)
9. [Ship the changes and measure them](#9-ship-the-changes-and-measure-them)
10. [How the numbers are calculated](#10-how-the-numbers-are-calculated)
11. [Options reference](#11-options-reference)
12. [Troubleshooting](#12-troubleshooting)
13. [FAQ](#13-faq)
14. [Using it with Claude Code](#14-using-it-with-claude-code)

---

## 1. What sitefocus does

sitefocus crawls one site, reads every page with a local embedding model, and builds two scores per page:

| Score | Meaning | How |
| --- | --- | --- |
| **Topic** (0 to 100) | How close the page is to the pages you most need to rank | Cosine similarity to the centre of your priority pages, as a percentile |
| **Links** (0 to 100) | How much internal link equity the page holds | Internal PageRank on the crawled link graph, as a percentile |

A third measure, **reach**, is internal harmonic centrality: how few clicks every other page needs to get to this one.

From those it answers:

- **Is link equity going to on-topic pages?** The Link Focus Score (0 to 100).
- **Which priority pages are orphaned?** Pages with two or fewer internal links pointing at them.
- **Which sitewide links are wasted?** Navigation and footer slots spent on single directory entries, campaign pages, redirects or noindexed URLs.
- **What exact links should be added?** Source page, target page, the paragraph to put it in, and the anchor.
- **What would change if you did all of it?** Every change is simulated on the real link graph before anyone touches the site.

It is built on ideas named in Google's Content Warehouse API documentation (leaked in May 2024), which lists attributes such as `siteFocusScore`, `siteRadius`, `siteEmbeddings` and `pageEmbedding`. That documentation names attributes; it does not publish formulas, models, weights or how they are used in ranking. sitefocus computes transparent, documented proxies. Treat them as diagnostics, not as Google's numbers.

**Good fits:** sites with hundreds to tens of thousands of pages where internal linking has grown by accident: SaaS and product sites with a content library, ecommerce category and product structures, publishers, directories with an editorial layer.

**Not a fit:** sites whose navigation only exists after JavaScript runs (sitefocus reads server HTML; see [section 12](#12-troubleshooting)), and sites behind a login.

---

## 2. Install

You need Python 3.9 or newer, and Ollama for the embedding model. Everything runs on your machine.

### Python package

```
pip install "git+https://github.com/burnszilla/sitefocus"
```

No access to GitHub from your machine? Download the wheel from the [Releases page](https://github.com/burnszilla/sitefocus/releases) and run `pip install sitefocus-<version>-py3-none-any.whl`.

Use a virtual environment if your system Python is managed:

```
python3 -m venv ~/.venvs/sitefocus
~/.venvs/sitefocus/bin/pip install "git+https://github.com/burnszilla/sitefocus"
export PATH="$HOME/.venvs/sitefocus/bin:$PATH"
```

### Ollama and the embedding model

- **macOS:** `brew install ollama`, or the app from https://ollama.com
- **Windows:** the installer from https://ollama.com
- **Linux:** `curl -fsSL https://ollama.com/install.sh | sh`

Then:

```
ollama serve        # skip if the Ollama app is already running
ollama pull nomic-embed-text
```

`nomic-embed-text` is about 270 MB. If Ollama runs on another machine on your network, point sitefocus at it with `export SITEFOCUS_OLLAMA=http://that-machine:11434`.

### Identify your crawler

Site owners should be able to reach whoever is crawling them:

```
export SITEFOCUS_CONTACT=you@yourcompany.com
```

This goes into the crawler's User-Agent: `Mozilla/5.0 (compatible; sitefocus/1.0; +mailto:you@yourcompany.com)`.

### Check everything

```
sitefocus doctor
```

It reports Python, numpy, Ollama, the embedding model, your contact address and whether Search Console is connected, and tells you what to fix.

---

## 3. First run in 10 minutes

Pick a small site you know (under a few hundred pages) so the whole loop finishes quickly.

```
mkdir -p work
printf "https://www.example.com/pricing/\nhttps://www.example.com/product/\n" > work/priority.txt

sitefocus run https://www.example.com/ \
  --sitemap https://www.example.com/sitemap.xml \
  --priority work/priority.txt \
  --no-gsc \
  --out work/example

open work/example/report/index.html        # macOS; use your browser elsewhere
```

What happens:

1. **Crawl.** robots.txt is read first and obeyed, including `Crawl-delay`. The crawler seeds from the home page and every URL in the sitemap (sitemap indexes are followed), then follows internal links. Requests to the site are spaced at least half a second apart across all workers (at most 2 a second), or the robots.txt `Crawl-delay` if that is longer.
2. **Embed.** Each page's title, H1 and main text go to the local model. Text that repeats on more than 15% of pages (navigation, footers, cookie banners) is removed first. Long pages are read in up to three windows and averaged.
3. **Plan.** Internal PageRank, harmonic centrality, the page map, link hygiene (rechecked live), and the linking plan with its simulations.
4. **Report.** `index.html` plus CSV files.

Everything is resumable. Run the same command again after an interruption and finished work is reused; pages that failed are retried. To measure changes you have shipped, run with `--fresh`: the old crawl is kept beside the new one in `crawl/previous-<time>/`.

---

## 4. Choose your priority pages

`priority.txt` is the most important input. It defines what "on topic" means for the whole analysis. One absolute URL per line.

**Good priority lists:**

- Product, pricing, plan and feature pages (a product sitemap is often exactly this)
- Category pages on an ecommerce site
- The 20 to 100 pages that drive conversions in your analytics
- Service and location pages that sell

**Avoid:**

- Every page on the site (then nothing is "off topic")
- Only the home page (too narrow; everything looks off topic)
- Pages that are noindexed, canonicalised elsewhere or redirect (they are dropped; the tool tells you if none remain)

**Why this matters.** Without an anchor, a "site centre" computed from all pages is set by whatever the site has most of. On a site where most pages are templated directory listings, that centre is "directory listing", and the editorial library looks like the least on-topic content. Anchoring on priority pages measures focus against what the site is for. The report still shows the plain measure for comparison.

Tips:

- 20 to 60 pages is a good size. Fewer than about 10 makes the centre noisy.
- You can run twice with different priority lists (for example consumer products, then business products) to see how each topic is supported.
- Keep the same list between runs so before-and-after comparisons are fair.

---

## 5. Connect Search Console and add backlinks

Both are optional. Both make the plan much better.

### Search Console (recommended)

With Search Console data sitefocus:

- **protects** pages that earn the most clicks (they are never taken out of sitewide slots),
- **targets near wins:** on-topic pages ranking 4 to 20 with above-median impressions get links first,
- **estimates traffic:** a what-if range of extra clicks if strengthened pages gain 1 to 3 positions,
- adds clicks, impressions and position to `pages.csv`.

`sitefocus run` asks you to connect if you have not. Two ways:

**Option 1: your own OAuth desktop client (works anywhere)**

1. Go to https://console.cloud.google.com/ and create or choose a project.
2. APIs & Services, Library: enable **Google Search Console API**.
3. APIs & Services, OAuth consent screen: configure it (Internal if you are on Google Workspace, otherwise External and add yourself as a test user).
4. Credentials, Create credentials, **OAuth client ID**, application type **Desktop app**. Download the JSON.
5. Run `sitefocus gsc-login --client-secret client_secret.json`. Your browser opens; approve read-only access once.

**Option 2: an existing gcloud login**

```
gcloud auth application-default login --scopes=https://www.googleapis.com/auth/webmasters.readonly,https://www.googleapis.com/auth/cloud-platform
```

Check the connection:

```
sitefocus gsc-status
sitefocus gsc-sites
```

During `run`, sitefocus picks the property that covers your start URL (the longest matching URL-prefix property, otherwise the domain property) and pulls the last 90 days ending 3 days ago (Search Console data lags). Override with `--gsc-site` and `--gsc-days`. On a domain property, rows are filtered to the crawled host.

**Privacy:** sitefocus ships with no credentials. The refresh token is stored only on your machine in `~/.config/sitefocus/gsc.json`, readable only by you, and used only to call Google's API. Disconnect with `rm ~/.config/sitefocus/gsc.json` and https://myaccount.google.com/permissions.

**Prefer a file?** Export Performance, Pages, last 3 months from Search Console and pass `--gsc pages.csv`. The columns are matched by name (`Top pages` or `Page`, `Clicks`, `Impressions`, `Position`).

### Backlinks

The internal PageRank model starts every page equal unless you tell it where external links land. Pass a backlink export with `--backlinks links.csv`:

- Search Console, Links, **Top linked pages** (externally), Export. Columns `Target page`, `Incoming links`, `Linking sites`.
- Or an Ahrefs "Best by links" or Semrush backlink export with a page URL column and `Referring domains`.

sitefocus uses linking sites (referring domains) when present, else incoming links. Each page's starting share grows with the square root of its linking sites, so a page with 100 referring domains starts about 11 times stronger than one with none, not 100 times.

---

## 6. Run a real audit

```
sitefocus run https://www.example.com/ \
  --sitemap https://www.example.com/sitemap_index.xml \
  --priority priority.txt \
  --backlinks links.csv \
  --notes notes.txt \
  --out work/example
```

**Time and load.** Requests are spaced at least `--delay` seconds apart (default 0.5) across all workers, so expect up to 2 pages a second, about 45 to 60 minutes for 5,000 pages. Embedding runs at roughly 150 to 300 page windows a minute on an Apple Silicon Mac. Run it in the background (`nohup ... &` or a terminal multiplexer) for large sites.

**Be a good guest.** robots.txt rules and `Crawl-delay` are obeyed, and robots.txt is read before any page is requested. A 429 or 503 pauses every worker and retries (honouring `Retry-After`, else 30 seconds, then 60). If robots.txt returns 401 or 403 the crawl stops (the site is blocking it); if it returns 429 or a server error three times, the crawl also stops rather than guessing what is allowed. Redirects are followed only while they stay on the site and are allowed by robots.txt. Responses larger than 5 MB are cut off. Tell the site owner or your client before crawling large sites, and crawl staging if production is fragile.

**Scope.** The crawl stays on the host and port the start URL resolves to (if `example.com` redirects to `www.example.com`, it crawls `www.example.com`). Query strings are dropped by default, so `?sort=` and `?page=` variants collapse into one URL and faceted navigation cannot trap the crawler; use `--keep-query` (on the `crawl` step) for sites where query URLs are real pages. Pages are decoded with their declared charset and `<base href>` is honoured. `--max-pages` caps the crawl (default 8,000).

**Steps one at a time.** Useful for reruns with different settings, since only the step you change has to run again:

```
sitefocus crawl  --start https://www.example.com/ --sitemap https://www.example.com/sitemap.xml --out work/crawl
sitefocus embed  --pages work/crawl/pages.jsonl --out work/embed
sitefocus plan   --crawl work/crawl --focus work/embed --priority priority.txt --out work/plan
sitefocus report --focus work/embed --plan work/plan --site example.com --notes notes.txt --out work/report/index.html
```

Rerunning `plan` and `report` takes seconds, so experiment with `--template-weight`, `--slots` or a different priority list freely.

**Notes.** `--notes notes.txt` puts your own findings, one per line, at the top of the report. Use it for things the tool cannot know: "the footer is managed by the brand team", "product pages are being migrated in Q3".

---

## 7. Read the report

The report is one HTML file with no external dependencies. Sections in order:

### Estimated impact if every change ships

The headline box. It applies every recommended change at once (contextual links, the related-products module, the flagged sitewide swap, the sitewide content slots) and compares:

- **Link Focus Score** now and after
- **Priority pages' share of internal PageRank** now and after, with the multiplier
- **Orphaned priority pages** now and after
- **Harmonic centrality (reach) of the weakest priority page**
- **Pages in the Stars group**
- **Traffic:** with Search Console, a gross opportunity: extra clicks if the pages whose internal PageRank rises at least 5% gain 1 to 3 positions, from the change in this site's own click-through rate between the current and improved position. It does not net off pages that lose PageRank. Without enough data it says so instead of guessing.

Below it, **the work**: how many links to add, slots to move, and hygiene items to fix or review.

How to read it: the PageRank figures are simulation outputs on the crawled graph. They show direction and relative size. They are not rankings, and the traffic figure is a what-if, not a forecast.

### How to execute, in order

A numbered plan, safest and highest value first, each step with its scope, the CSV to work from, what to check before shipping, and how to measure. See [section 9](#9-ship-the-changes-and-measure-them).

### Link Focus Score

The average topic score of all pages, weighted by each page's internal PageRank.

- **Above 75:** most link equity sits on on-topic pages.
- **60 to 75:** typical for sites with large templated sections or a big editorial library.
- **Around 50:** link equity is spread with no regard to topic.
- **Below 50:** link equity favours off-topic pages.

These bands are guidance from the method, not industry benchmarks. Compare a site with itself over time, not with other sites.

### What moves the needle

Each scenario simulated on the full link graph, with charts and a table:

| Scenario | What changes |
| --- | --- |
| Contextual links | The recommended links added in body copy |
| Links + flagged swap | Plus flagged sitewide slots reassigned to orphaned priority pages |
| Links + related-products module | Plus every page in the largest editorial section linking to its three closest priority pages |
| Links + sitewide content slots | Plus the least on-topic sitewide content links pointed at the weakest priority pages |
| Every change together | All of the above |

The usual lesson: sitewide navigation and footer links carry most internal PageRank, so changing a handful of sitewide slots moves more than thousands of contextual links. The table's "Check first" column says when a scenario needs traffic data before you act on it.

### Search Console (if connected)

Near wins (on-topic pages ranking 4 to 20, above-median impressions) with how many new links each receives, and the protected sitewide pages.

### The page map

Every page plotted by topic (x) and links (y), split into four groups at 50:

| Group | Topic | Links | What to do |
| --- | --- | --- | --- |
| **Star** | high | high | Keep |
| **Underlinked** | high | low | Send links here |
| **Leak** | low | high | Review sitewide links pointing here |
| **Long tail** | low | low | Leave, consolidate or noindex |

Buttons switch between **Now**, **After the links**, and **Before and after**, which overlays both with a line for every page that moves. Hover any dot for the page. A flat row of dots is a set of pages with identical PageRank, usually templated pages linked only from their directory.

Below the map: PageRank share by group, then the orphaned priority pages on a log scale.

### Priority pages nothing links to

Priority pages with two or fewer internal links in, their PageRank now and after, and the proposed links (parent hub first, then the eight most similar pages). Confirm in a real browser that the links are missing before reporting; menus can be built in JavaScript.

### Harmonic centrality (reach)

The average of 1 divided by clicks from every other page. 1.0 means one click from every page; 0.5 about two clicks; 0 unreachable. It is the same measure Common Crawl uses to rank whole hosts and domains on the web, computed here inside the site, page by page, exactly.

PageRank says how much link weight flows in; harmonic centrality says how easily the rest of the site gets there. Two tables matter:

- **PageRank ahead of harmonic centrality:** fed by a few strong links rather than woven into the site. Fragile; give them ordinary contextual links from related pages.
- **Harmonic centrality ahead of PageRank:** easy to reach but linked from weak pages. Links from stronger pages help most.

### Link hygiene

Internal links to redirects, broken pages and noindexed or canonicalised pages; internal nofollow; pages with meta robots nofollow (their links pass nothing); and sign-in, account or cart links. Every redirect, broken and rate-limited target is fetched again live before it is reported, so a crawl hiccup does not become a false finding.

### Where nofollow belongs

Short version: **nofollow does not steer PageRank.** Since 2009, a nofollowed link still uses up its share of the page's PageRank; that share is discarded, not redistributed. Nofollowing links to "concentrate" equity loses equity. To stop sending importance somewhere, remove or move the link.

Nofollow is still right for sign-in and account links, `rel="sponsored"` for paid links, and `rel="ugc"` for user content. Internal search and faceted URLs belong in robots.txt or behind noindex, not nofollow.

### Click depth, anchor text, cannibalization

- **Click depth:** shortest link path from the home page. Priority pages deeper than 3 clicks, or unreachable, are listed.
- **Anchor text on priority pages:** linking pages, distinct anchors, share of generic anchors ("learn more", "click here") and the most common anchors. A priority page whose only anchor is a navigation label is a content-linking opportunity.
- **Possible cannibalization:** pairs of indexable pages in the same editorial section with near-identical content (cosine 0.95 or more). Check their queries in Search Console before consolidating.

### By section and topic clusters

PageRank share per URL section now and after, the section's median topic score, its contribution to the Link Focus Score, and the share of near-duplicate pages. Topic clusters group pages by content with example titles; they show what the site is actually about.

### Method and limits

Exactly what was modelled and what was not. Read it before you present numbers.

---

## 8. The CSV files

All in `<out>/report/` (and `<out>/plan/`). Open them in a spreadsheet and filter.

### pages.csv (one row per indexable page)

| Column | Meaning |
| --- | --- |
| url, section, title | The page and its first path segment |
| topic | 0 to 100, closeness to priority pages (percentile) |
| links, links_after | 0 to 100, internal PageRank percentile now and after the contextual links |
| reach, reach_gap | Harmonic centrality percentile, and reach minus links (negative: PageRank ahead of reach) |
| group | star, underlinked, leak, long tail |
| priority | 1 if in priority.txt |
| linking_pages | Distinct internal pages linking to it |
| depth | Clicks from the home page (blank: unreachable) |
| pagerank, pagerank_after | Raw internal PageRank (sums to 1 across the site) |
| external_linking_sites | From the backlink export |
| clicks, impressions, position | From Search Console |
| protected, near_win | 1 if protected from swaps, or a near win |

### link_adds.csv (every recommended link)

| Column | Meaning |
| --- | --- |
| source, target | Where the link goes from and to |
| similarity | Cosine similarity of the two pages |
| reason | orphan: parent hub, orphan: similar page, priority, near win, underlinked |
| suggested_anchor | The anchor to use |
| anchor_in_text | 1 if that phrase already exists in the paragraph (link it in place) |
| paragraph | The paragraph on the source page that best matches the target |
| instruction | One line a content editor can follow |

Links are suggested in this order: orphaned priority pages, then near wins, then priority pages, then underlinked pages, strongest source pages first. Pages marked nofollow are never suggested as sources. At most three new links per source page, never between near-duplicates, except parent-hub links to orphaned pages (one per orphan, so a hub can get more).

How anchors are chosen: the paragraph must share at least one distinctive word with the target (a word on fewer than 10% of pages), must be prose (8 words or more, no `|` separators), and the anchor is 2 to 6 words from that paragraph containing at least one distinctive word, never spanning a sentence break or starting or ending on a filler word. When nothing fits, the instruction says to add a sentence, and the anchor is the target's cleaned title. `--anchor-model qwen3:8b` asks a local model for anchors on orphan and priority links where no phrase fits; only words that really appear in the paragraph are kept.

### sitewide.csv

Every page linked from at least half the site, with its topic score, PageRank share, a flag (single directory entry, campaign page, stale, leak), whether it is in the swap or slot scenario, and Search Console clicks if connected.

### execution/

One CSV per step of the execution guide, numbered to match the report. Hand each file to whoever owns that change.

### hygiene.csv (plan folder)

Every hygiene issue: target, how many internal links point at it, where a redirect ends up, the HTTP status, and whether it was rechecked live.

---

## 9. Ship the changes and measure them

The execution guide in the report is the order. In practice:

1. **Baseline.** Save the report. Export Search Console Performance (Pages and Queries, last 3 months) for the priority pages. Add a dated annotation in analytics.
2. **Link hygiene first.** Usually template edits, so one change fixes thousands of links. Point redirecting links at their final URL. Keep links people need (site search, sign-in) but keep them out of body copy.
3. **Orphaned priority pages.** Parent hub link first, then similar pages. Request indexing in URL Inspection.
4. **Flagged sitewide swap.** Needs business sign-off; someone put those links there on purpose.
5. **Contextual links** to priority pages, then near wins, then underlinked pages. Batches of a few hundred, section by section. Use `link_adds.csv` instructions; skip any link that would not help a reader.
6. **Optional module test** on a tenth of the editorial section.
7. **Sitewide content slots** last, one or two at a time, only after checking the traffic of every page that would lose a slot.
8. **Near-duplicates:** consolidate, differentiate or canonicalise after checking each page's queries.
9. **Recrawl monthly** with the same priority list and compare.

**Measuring.**

- Compare the same 28-day window before and after each step, and the same window a year earlier if the site is seasonal.
- Watch impressions first, then average position, then clicks, for the pages that received links, against a set of similar pages that did not.
- Expect internal linking effects to show over 2 to 8 weeks after Google recrawls the changed pages. Search Console's URL Inspection shows when a page was last crawled.
- Rerun sitefocus after each major step; the Link Focus Score, the page map and the hygiene counts should move the way the simulation said.

---

## 10. How the numbers are calculated

**Page vectors.** nomic-embed-text over `title + H1 + main text`, in up to three 5,500-character windows averaged, after removing text blocks found on more than 15% of pages. Vectors are unit-normalised.

**Topic.** The anchor is the mean of the priority pages' vectors, normalised. Topic = percentile of `cos(page, anchor)` among indexable pages (0 to 100, ties averaged).

**Internal PageRank.** Damping 0.85, one edge per source-target pair, indexable same-host pages only. Navigation, footer and sitewide links (targets linked from at least half the pages, or links outside body copy) weigh `--template-weight` (default 0.5) of a body link, a reasonable-surfer approximation. A nofollowed link, and every link on a page with meta robots or X-Robots-Tag nofollow, still takes its share of the page's PageRank and that share is discarded, as Google has done since 2009. Harmonic centrality and click depth follow followed links only. Dangling pages redistribute through the teleport vector. With `--backlinks`, the teleport vector is `1 + sqrt(linking sites)` per page, normalised, so external equity enters where it lands.

**Links score.** Percentile of internal PageRank.

**Link Focus Score.** `sum(PageRank_i x Topic_i)` with PageRank summing to 1. Range 0 to 100.

**Harmonic centrality (reach).** `HC(v) = sum over u != v of 1 / d(u, v)`, divided by `n - 1`, where `d` is the shortest link path and unreachable pairs add 0. Computed exactly by breadth-first search from 64 sources at a time using bitsets.

**Groups.** Topic and links each split at 50.

**Orphans.** Priority pages with two or fewer distinct internal pages linking to them.

**Plain site focus** (for comparison). Length of the mean unit page vector, which equals the square root of the average pairwise cosine similarity. Radius is `1 - cos(page, site centre)`.

**Traffic what-if.** From the Search Console data, this site's click-through rate at each rounded position 1 to 20 (pages with 20 or more impressions, at least 3 pages per position, made non-increasing). For every page whose internal PageRank rises at least 5% when every change ships and that ranks between 1.5 and 30: `impressions x (CTR(position - g) - CTR(position))`, for g = 1, 2, 3. Shown only when at least 6 positions, including 1 or 2, have data. It is a gross opportunity, not a forecast and not net of pages that lose PageRank.

**What is not modelled.** Google's actual link weighting, anchor text and context effects, external links unless you supply them, content quality, and anything outside the crawled site. The simulation is a model of internal equity flow, nothing more.

---

## 11. Options reference

### sitefocus run

| Option | Default | What it does |
| --- | --- | --- |
| `--sitemap URL` | none | Sitemap or sitemap index; repeatable |
| `--priority FILE` | required | Priority URLs |
| `--out DIR` | required | Working directory |
| `--max-pages N` | 8000 | Crawl cap |
| `--gsc FILE` | auto | Search Console export; not needed when connected |
| `--no-gsc` | off | Run without Search Console and do not ask |
| `--gsc-site`, `--gsc-days` | auto, 90 | Property and period |
| `--backlinks FILE` | none | Backlink export |
| `--chunks N` | 3 | Embedding windows per long page |
| `--k N` | 12 | Topic clusters (lowered automatically on small sites) |
| `--notes FILE` | none | Your findings at the top of the report |
| `--no-recheck` | off | Skip the live recheck of flagged link targets |
| `--fresh` | off | Recrawl from scratch; the previous crawl is kept in `crawl/previous-<time>/` |
| `--anchor-model NAME` | none | Local Ollama model for anchor suggestions |

### sitefocus crawl

`--start`, `--sitemap`, `--out`, `--max-pages`, `--workers` (2), `--delay` minimum seconds between requests to the site across all workers (0.5; robots.txt `Crawl-delay` wins if larger), `--keep-query`, `--fresh`.

### sitefocus embed

`--pages`, `--out`, `--ollama`, `--model` (nomic-embed-text), `--k`, `--chunks`, `--boilerplate` (0.15, share of pages a text block can appear on before it is treated as boilerplate).

### sitefocus plan

| Option | Default | What it does |
| --- | --- | --- |
| `--adds-per-page` | 3 | Maximum new links per source page |
| `--max-new-inlinks` | 150 | Maximum new links per target page |
| `--min-sim` | 0.72 | Minimum similarity for a recommended link |
| `--near-dup` | 0.97 | Pages this similar are never linked to each other |
| `--template-share` | 0.5 | Share of pages linking to a target for it to count as sitewide |
| `--template-weight` | 0.5 | Weight of template links relative to body links |
| `--slots` | 10 | Sitewide content slots in the slot scenario |
| `--module-section` | auto | Section for the module scenario |
| `--protect-clicks` | auto | Clicks threshold for protection (default: top 10% of pages with clicks) |
| `--year` | this year | For stale-page detection |
| `--gsc`, `--backlinks`, `--no-recheck`, `--anchor-model`, `--ollama` | | As above |

### Environment

| Variable | Default | Purpose |
| --- | --- | --- |
| SITEFOCUS_CONTACT | none | Your email in the User-Agent |
| SITEFOCUS_OLLAMA | http://localhost:11434 | Ollama server |
| SITEFOCUS_BACKOFF | 30 | Seconds to wait after a 429 or 503 (doubles on the second retry) |
| SITEFOCUS_GSC_CLIENT | none | Default `--client-secret` for `gsc-login` |
| SITEFOCUS_CONFIG_DIR | ~/.config/sitefocus | Where the Search Console token is kept |

---

## 12. Troubleshooting

| Symptom | Cause and fix |
| --- | --- |
| `robots.txt returned 403` and the crawl stops | The site or its CDN blocks the crawler. Ask for an allow-list entry for your IP or User-Agent, or crawl staging. |
| Very few pages crawled | The start URL may not be the canonical host; sitefocus follows the start redirect, but check the sitemap URLs use the same host. Check robots.txt `Disallow` rules, and `--max-pages`. |
| Orphans that are clearly linked in the browser | The links are built by JavaScript after page load. Confirm in a browser's rendered DOM; the recommendation then becomes "make these links crawlable in the HTML". |
| Many 429s or a slow crawl | The site rate-limits. Lower `--workers` to 1 and raise `--delay`. |
| `Ollama ... NOT REACHABLE` | Start Ollama (`ollama serve`) or set `SITEFOCUS_OLLAMA`. |
| Embedding stalls | The model server is busy. sitefocus times out a stuck request after 20 seconds, splits the batch and retries; progress is saved, so rerun to resume. |
| `none of the priority URLs were crawled as indexable pages` | Priority URLs redirect, are noindexed, canonicalised elsewhere, blocked, or not linked and not in the sitemap. Fix the list. |
| Search Console: `do not include Search Console access` | Your gcloud login lacks the scope. Use the gcloud command in section 5 or `gsc-login`. |
| Search Console: `No property you can read covers ...` | Your Google account has no access to that property, or the property is for another host. `sitefocus gsc-sites` lists what you can read; pick one with `--gsc-site`. |
| Traffic says "not enough Search Console data" | Fewer than 6 positions have enough pages. Common on small sites; nothing to fix. |
| Report numbers look identical to the last run | A rerun resumes the previous crawl. Use `sitefocus run ... --fresh` after shipping changes; embeddings are cached by content, so only changed pages are re-embedded. |

---

## 13. FAQ

**Is this Google's site focus score?**
No. Google's leaked documentation names `siteFocusScore` and `siteRadius` but publishes no formula. sitefocus measures the same idea transparently. Use it to find problems, not to predict Google's internal numbers.

**Where should I put nofollow to control PageRank?**
Nowhere, for that purpose. Nofollow discards the share of PageRank it would have passed; it does not redirect it. Remove or move links instead. See [Where nofollow belongs](#where-nofollow-belongs).

**Why do my tax articles (or blog posts) show as "leaks"?**
Leak means well linked but not close to your priority pages. Editorial content often is exactly that, and it earns rankings and links. Leaks are a review list, not a removal list. With Search Console connected, high-click pages are protected.

**Does a higher Link Focus Score mean higher rankings?**
Not by itself. It means internal equity is concentrated on the pages you care about, which is one input among many. Measure the pages you change in Search Console.

**How big a site can it handle?**
It has been run on about 5,000 pages and 440,000 internal links, where crawl, plan and report finish comfortably. Harmonic centrality takes seconds at 20,000 pages, and duplicate checks run in blocks so memory stays bounded. Larger sites work but take longer to crawl and embed; split very large sites by section with separate priority lists.

**Does any data leave my machine?**
Crawled pages go only to your Ollama server. Search Console calls go only to Google's API with your own credentials. Nothing else is sent anywhere.

**Can I run it on a JavaScript-rendered site?**
It reads server HTML. If the site's content and links are only in the rendered DOM, results will be wrong; prerender, or make links crawlable in the HTML (good for every crawler, not just this one).

---

## 14. Using it with Claude Code

The repository includes a Claude Code skill in `skill/`. Copy it to `~/.claude/skills/site-focus/`, then ask Claude things like "run site focus on example.com with these priority pages" or "build an internal linking plan for example.com". Claude runs sitefocus, verifies orphans in a browser, writes findings into the notes file, and walks you through the report.

---

Questions, bugs or ideas: open an issue at https://github.com/burnszilla/sitefocus/issues.
