---
name: site-focus
description: Site focus and internal linking with the sitefocus tool. Scores every page on topic (closeness to the priority pages, from local embeddings), internal PageRank and reach (internal harmonic centrality), computes the Link Focus Score, maps pages into stars, underlinked, leaks and long tail, finds orphaned priority pages and wasted sitewide links, and builds a simulated internal linking plan with an HTML report. Proxies for siteFocusScore and siteRadius from the 2024 Google Content Warehouse API leak. Use for "site focus", "siteFocusScore", "siteRadius", "topical focus", "internal linking plan", "internal PageRank", "orphan pages", "link equity", "which pages should link where".
---

# site-focus

Runs through the `sitefocus` CLI. If `sitefocus doctor` is not ready, fix what it lists first (install the wheel, install Ollama, `ollama pull nomic-embed-text`, set `SITEFOCUS_CONTACT`).

## Workflow

1. Build `priority.txt`: the product or money pages, one URL per line. A product sitemap is a good source. Ask the user if unsure; the whole analysis is anchored on these pages.
2. Check `sitefocus gsc-status`. If not connected, ask the user to connect (`sitefocus gsc-login --client-secret client_secret.json`, or the gcloud command in the README); `run` pulls the data itself once connected. Never handle or store their credentials yourself. Ask for the Links "Top linked pages" export if available (the API has no links report). Then `sitefocus run <home URL> --sitemap <sitemap URL> --priority priority.txt --gsc pages.csv --backlinks links.csv --out <dir>`. Large sites take one to three hours (crawl plus embedding); run it in the background. It resumes if interrupted.
3. Read `<dir>/plan/plan.json`, then `pages.csv`, `link_adds.csv` and `sitewide.csv` in `<dir>/report/`.
4. Verify before claiming:
   - Orphaned priority pages: open the home page and the parent hub in a rendered browser (Playwright) and confirm the links are really absent. Menus can be client-side.
   - Sitewide flags: read the title before calling a page stale; old slugs often carry current content.
   - Sitewide leaks: check what the page is before suggesting any change.
5. Write 3 to 6 findings to a notes file, one per line, and rerun only the report: `sitefocus report --focus <dir>/embed --plan <dir>/plan --site <host> --notes notes.txt --out <dir>/report/index.html`.
6. Lead with: what moves the needle (the scenario table), orphaned priority pages, sitewide link hygiene (redirects, noindexed targets), near wins, then the contextual links.

## Rules

- Topic is anchored on the priority pages. The plain measure (similarity to the average page) is set by whatever the site has most of; on directory-heavy sites it calls editorial content off-topic. Report it only for comparison.
- The leak names attributes; it publishes no formula, model or weight. Call every focus number a proxy.
- Never recommend removing or noindexing content to raise a score. Leaks and long tail are for review.
- The swap scenario touches only flagged sitewide slots (single directory entries, campaign pages, stale pages). It is a business decision; present it as optional.
- Nofollow cannot steer PageRank; never recommend nofollow sculpting. Recommend removing or moving links instead, and nofollow only for sign-in and account links, rel="sponsored" for paid links and rel="ugc" for user content.
- Without --gsc, never recommend removing a sitewide link to content: it may be a top traffic page. The slot scenario says so.
- Verify every hygiene finding live before reporting it. A crawl can be rate limited; 429 and 503 responses are listed separately, never as broken.
- The simulation is a model: navigation, footer and sitewide links weigh 0.5 of a body link, and external links count only with --backlinks. Present it as direction and size. Recommend shipping orphan links and hygiene fixes first and measuring in Search Console over 4 to 8 weeks.
