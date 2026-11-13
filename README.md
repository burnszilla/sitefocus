# sitefocus

Site focus and internal linking in one tool. It answers one question: **is your site's link importance going to the pages that matter, and what links would fix it?**

It crawls a site, reads every page with a local AI embedding model, scores each page on topic and on internal PageRank, and builds a linking plan that it simulates before you ship anything. Everything runs on your machine; page text only goes to a local Ollama model.

## The idea in one paragraph

Every page gets two scores from 0 to 100. **Topic**: how close the page is to your priority (product or money) pages. **Links**: how much internal PageRank the page holds. The **Link Focus Score** is the average topic score weighted by PageRank: 100 means all link importance sits on the most on-topic pages, 50 means it is spread with no regard to topic. Splitting both scores at the middle gives a **page map**:

| | weakly linked | well linked |
| --- | --- | --- |
| **on topic** | Underlinked: send links here | Star: keep |
| **off topic** | Long tail: leave, consolidate or noindex | Leak: review sitewide links pointing here |

The plan fixes orphaned priority pages first, then sends links to near wins (with Search Console data) and to underlinked pages, and reruns PageRank to show the change. It also simulates bigger, template-level changes, because sitewide navigation and footer links usually carry most of a site's internal PageRank.

## Install

Python 3.9 or later. From GitHub:

```
pip install "git+https://github.com/burnszilla/sitefocus"
```

Or from a release wheel (no network to GitHub needed): download it from the Releases page, then

```
pip install sitefocus-1.1.0-py3-none-any.whl
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

The crawl obeys robots.txt and runs at about 1.5 pages a second (2 workers, half a second apart). It is resumable: run the same command again to continue. A 5,000-page site takes about an hour to crawl and 10 to 20 minutes to embed.

## Better inputs, better decisions

Both are optional and both are plain CSV exports, so nothing needs API access:

- `--gsc pages.csv`: Search Console, Performance, Pages tab, last 3 months, Export. Pages that earn the most clicks are protected (never swapped out of sitewide slots); on-topic pages ranking 4 to 20 with above-median impressions become "near wins" that get links first; clicks, impressions and position are added to `pages.csv` and to the near-duplicate review.
- `--backlinks links.csv`: Search Console, Links, Top linked pages (externally), Export; or an Ahrefs or Semrush "best by links" export. External links put PageRank into specific pages; with this file the model starts each page from its real external strength instead of treating every page equally.

```
sitefocus run https://www.example.com/ --sitemap https://www.example.com/sitemap.xml \
  --priority priority.txt --gsc pages.csv --backlinks links.csv --out work/example
```

## What you get

`work/example/report/`:

- `index.html`:
  - Link Focus Score now and after the plan, and **what moves the needle**: contextual links, the flagged sitewide swap, a related-products module, and reassigning sitewide content slots, each simulated
  - the page map (topic against link score) with a **before and after overlay**, PageRank by group, section and orphaned page
  - Search Console near wins and protected pages (with `--gsc`)
  - **link hygiene**: internal links to redirects, broken pages and noindexed pages, existing internal nofollow, sign-in and account links
  - **where nofollow belongs** (and why it cannot steer PageRank)
  - **click depth**, **anchor text** on priority pages, **possible cannibalization** (near-identical editorial pages)
  - links to add, sitewide links, results by section, topic clusters, method and limits
- `pages.csv`: every page with its topic score, link score and group
- `link_adds.csv`: every recommended link with similarity, reason and a suggested anchor
- `sitewide.csv`: every page linked from at least half the site, with flags

Add your own findings to the top of the report:

```
sitefocus report --focus work/example/embed --plan work/example/plan --site example.com \
  --notes notes.txt --out work/example/report/index.html
```

## How to read it

- **Orphaned priority pages** (two or fewer internal links in) are the biggest, cheapest win. Before you report one, open the home page and the parent hub in a real browser; some menus are built in JavaScript and the crawler reads server HTML.
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

## Settings

| Variable | Default | Purpose |
| --- | --- | --- |
| SITEFOCUS_CONTACT | none | your email, sent in the crawler's User-Agent |
| SITEFOCUS_OLLAMA | http://localhost:11434 | Ollama server for embeddings |

## Claude Code

The `skill/` folder (in this repo and in the release zip) is a Claude Code skill. Copy it to `~/.claude/skills/site-focus/` and Claude will run sitefocus for you.
