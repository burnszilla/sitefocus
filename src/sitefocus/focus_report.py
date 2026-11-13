#!/usr/bin/env python3
"""HTML report for site focus + internal linking (link_plan.py output).

  focus_report.py --focus work/focus --plan work/plan --site example.com \
      --out report/index.html [--notes notes.txt] [--date "13 November 2026"]

notes.txt: optional analyst findings, one per line, shown first.
"""
import argparse
import csv
import html
import json
import os
import shutil
from collections import Counter

try:
    from . import charts
except ImportError:
    import charts

HERE = os.path.dirname(os.path.abspath(__file__))


def css_path():
    for p in (os.path.join(HERE, "assets", "report.css"), os.path.join(HERE, "..", "assets", "report.css")):
        if os.path.exists(p):
            return p
    raise FileNotFoundError("report.css not found")


def e(x):
    return html.escape(str(x))


def cell(c):
    if isinstance(c, str) and c.startswith("<"):
        return c
    if c is None:
        return ""
    if isinstance(c, float):
        return f"{c:.3g}" if abs(c) < 100 else f"{c:,.0f}"
    if isinstance(c, int):
        return f"{c:,}"
    return e(c)


def table(head, rows):
    h = "".join(f"<th>{e(x)}</th>" for x in head)
    b = "".join("<tr>" + "".join(f"<td>{cell(x)}</td>" for x in r) + "</tr>" for r in rows)
    return f"<table><thead><tr>{h}</tr></thead><tbody>{b}</tbody></table>"


def link(u, n=80):
    short = u.split("://", 1)[-1]
    short = short.split("/", 1)[1] if "/" in short else ""
    return f'<a href="{e(u)}">/{e(short[:n])}</a>'


def short_path(u, n=34):
    """Last path segments that fit in n characters, never cut mid-word."""
    segs = [p for p in u.split("://", 1)[-1].split("/")[1:] if p]
    out = ""
    for seg in reversed(segs):
        cand = seg + ("/" + out if out else "")
        if len(cand) + 2 > n and out:
            return "…/" + out
        out = cand
    return "/" + out if len(out) <= n else "…" + out[-n:]


def pct(x):
    return f"{100 * x:.1f}%"


ACTIONS = {
    "star": "On topic and well linked. Keep.",
    "underlinked": "On topic, weakly linked. Send links here.",
    "leak": "Off topic, well linked. Review sitewide links pointing here.",
    "long tail": "Off topic, weakly linked. Leave, consolidate or noindex.",
}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--focus", required=True)
    ap.add_argument("--plan", required=True)
    ap.add_argument("--site", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--notes")
    ap.add_argument("--date", default="")
    a = ap.parse_args()
    F = json.load(open(os.path.join(a.focus, "focus.json")))
    P = json.load(open(os.path.join(a.plan, "plan.json")))
    adds = list(csv.DictReader(open(os.path.join(a.plan, "link_adds.csv"))))
    sw = list(csv.DictReader(open(os.path.join(a.plan, "sitewide.csv"))))
    pages = list(csv.DictReader(open(os.path.join(a.plan, "pages.csv"))))
    L, ps, Q = P["link_focus_score"], P["pagerank_share"], P["quadrants"]
    orph = P["orphaned_priority"]

    o = [f"<h1>Site focus and internal linking: {e(a.site)}</h1>",
         f'<p class="lede">{P["pages"]:,} indexable pages and {P["internal_links"]:,} internal links{(", " + e(a.date)) if a.date else ""}. '
         f'Topic is defined by {P["priority_pages"]} priority pages.</p>']

    I = P.get("impact")
    if I:
        def arrow(a_, b_, fmt=str):
            return f'<span class="was">{fmt(a_)}</span><span class="to">{fmt(b_)}</span>'
        mult = I["priority_share"][1] / I["priority_share"][0] if I["priority_share"][0] else 0
        cells = [
            ("Link Focus Score", arrow(I["lfs"][0], I["lfs"][1]), f"{I['lfs'][1] - I['lfs'][0]:+.1f} points"),
            ("Priority pages' share of internal PageRank", arrow(*I["priority_share"], fmt=pct), f"{mult:.2f}x"),
            ("Orphaned priority pages", arrow(*I["orphans"]), "linked into the site" if I["orphans"][1] < I["orphans"][0] else ""),
            ("Reach of the weakest priority page", arrow(*I["priority_reach_min"]), "1.0 = one click from every page"),
            ("Pages in the Stars group", arrow(*I["stars"], fmt=lambda v: f"{v:,}"), "on topic and well linked"),
        ]
        T = I.get("traffic") or {"available": False, "reason": "no Search Console export (--gsc)"}
        if T["available"]:
            cells.append(("Extra clicks if strengthened pages gain 1 to 3 positions",
                          f'<span class="to range">+{T["gain_1"]:,} to +{T["gain_3"]:,}</span>',
                          f"{T['pages']} pages, {T['impressions']:,.0f} impressions in the export period; this site's own CTR curve"))
        else:
            cells.append(("Traffic", '<span class="to na">not estimated</span>', T["reason"]))
        o.append('<section class="impact" aria-labelledby="impact-h"><h2 id="impact-h">Estimated impact if every change ships</h2><div class="impact-grid">'
                 + "".join(f'<div class="impact-cell"><span class="label">{e(lab)}</span><span class="vals">{v}</span><span class="note">{e(nt)}</span></div>' for lab, v, nt in cells)
                 + "</div>")
        work = [f"{I['links_added']:,} contextual links added",
                f"{I['slots_moved']} sitewide link slots reassigned" + (" (check their traffic first: add --gsc)" if I["slots_need_traffic_check"] else ""),
                f"{I['hygiene_links']:,} internal links pointed at their final, indexable URL (crawl efficiency; not modelled as PageRank gain)"]
        if I.get("near_wins_linked") is not None:
            work.append(f"{I['near_wins_linked']} Search Console near wins receive new links")
        o.append("<p class=\"impact-work\"><strong>The work:</strong> " + "; ".join(e(w) for w in work) + ".</p>")
        o.append('<p class="impact-note">Simulated on the crawled link graph with every change from sections below applied at once. It estimates where internal PageRank and reach would move. The traffic figure is a what-if, not a forecast: it assumes the strengthened pages gain 1 to 3 positions and applies the click-through rate this site gets at each position from the Search Console export. Links do not guarantee movement; Google weighs links by context, adds external links, and ranks on far more than links. Ship in stages and measure in Search Console.</p></section>')

    # Step-by-step execution guide, with one CSV per step.
    exdir = os.path.join(os.path.dirname(os.path.abspath(a.out)), "execution")
    shutil.rmtree(exdir, ignore_errors=True)
    os.makedirs(exdir, exist_ok=True)

    def write_csv(name, head, rows_):
        with open(os.path.join(exdir, name), "w", newline="") as f_:
            w_ = csv.writer(f_)
            w_.writerow(head)
            w_.writerows(rows_)
        return f"execution/{name}"

    hyg_rows = list(csv.DictReader(open(os.path.join(a.plan, "hygiene.csv")))) if os.path.exists(os.path.join(a.plan, "hygiene.csv")) else []
    sc_by = {x["name"]: x for x in (P.get("scenarios") or [])}
    steps = []
    steps.append(("Record the starting point",
                  "Before changing anything, save this report and export Search Console Performance (Pages and Queries, last 3 months) for the priority pages. Add a dated annotation in your analytics. Every later step is measured against this.",
                  f"Link Focus Score {L['now']}; priority pages hold {pct(ps['priority'][0])} of internal PageRank", None,
                  "Nothing to check.", "This is the baseline."))
    fix = [r for r in hyg_rows if r["issue"] in ("links to a redirect", "links to a broken page", "links to a noindexed or canonicalised page")]
    if fix:
        f_ = write_csv(f"{len(steps) + 1:02d}-fix-link-hygiene.csv", ["issue", "target", "links_to_it", "change_to"],
                       [[r["issue"], r["target"], r["links_to_it"], r["points_to"] or ("remove or replace" if "broken" in r["issue"] else "link the canonical page, or move out of the template")] for r in fix])
        tot = sum(int(r["links_to_it"]) for r in fix)
        steps.append(("Fix links that point at redirects, broken or noindexed pages",
                      "Start here: low risk, and most of these sit in the sitewide template, so one template edit fixes thousands of links. Point redirecting links at their final URL. For noindexed targets, keep links people need (site search, sign-in) and simply leave them out of body copy; move the rest out of the template or link the canonical page.",
                      f"{len(fix)} targets, {tot:,} links", f_,
                      "Check each target live in a browser first; a crawl can be rate limited.",
                      "Recrawl (sitefocus run again) and confirm these rows are gone. Watch Search Console crawl stats."))
    orph_rows = [r for r in adds if r["reason"].startswith("orphan")]
    if orph_rows:
        f_ = write_csv(f"{len(steps) + 1:02d}-link-orphaned-priority-pages.csv", ["from", "to", "reason", "similarity", "suggested_anchor"],
                       [[r["source"], r["target"], r["reason"], r["similarity"], r["suggested_anchor"]] for r in orph_rows])
        steps.append(("Link the orphaned priority pages",
                      "The biggest single gain. Add each parent-hub link first (one per orphan), then the links from the most similar pages, in body copy with the suggested anchor.",
                      f"{len(P['orphaned_priority'])} pages, {len(orph_rows)} links", f_,
                      "Open the home page and each parent hub in a real browser and confirm the links really are missing (menus can be built in JavaScript).",
                      "Request indexing for each orphan in Search Console URL Inspection. Watch their impressions over 2 to 4 weeks."))
    if P.get("swap"):
        f_ = write_csv(f"{len(steps) + 1:02d}-reassign-flagged-sitewide-slots.csv", ["sitewide_link_now", "why", "give_slot_to"], [[x["out"], x["why"], x["in"]] for x in P["swap"]])
        sc = sc_by.get("Links + flagged swap", {})
        steps.append(("Reassign the flagged sitewide slots",
                      "Navigation or footer slots that point at single directory entries, campaign pages or stale pages go to orphaned priority pages instead. One template edit.",
                      f"{len(P['swap'])} slots" + (f"; simulated priority share {pct(sc['priority'])}" if sc else ""), f_,
                      "Business sign-off: someone placed these links on purpose. Check their traffic in Search Console.",
                      "Compare the receiving pages' impressions and clicks over 4 weeks."))
    for reason, title, why in (("priority", "Add contextual links to the priority pages", "Each on-topic page links to its closest priority page, in body copy where the topic comes up. Work section by section; at most three new links per page."),
                               ("near win", "Add links to Search Console near wins", "On-topic pages already ranking 4 to 20 get links from strong, similar pages. Small ranking gains here move the most clicks."),
                               ("underlinked", "Strengthen underlinked on-topic pages", "Strong pages link to similar on-topic pages that get little link weight today. This also fixes pages whose PageRank runs ahead of their reach.")):
        rr = [r for r in adds if r["reason"] == reason]
        if rr:
            f_ = write_csv(f"{len(steps) + 1:02d}-{reason.replace(' ', '-')}-links.csv", ["from", "to", "similarity", "suggested_anchor"],
                           [[r["source"], r["target"], r["similarity"], r["suggested_anchor"]] for r in rr])
            steps.append((title, why, f"{len(rr):,} links into {len(set(r['target'] for r in rr)):,} pages", f_,
                          "Read the source paragraph before placing a link; skip any that would not help a reader.",
                          "Recrawl after each batch; watch the target pages in Search Console."))
    msec = P.get("module_section")
    sm = next((x for x in (P.get("scenarios") or []) if "module" in x["name"]), None)
    if msec and sm:
        steps.append((f"Optional: a related-products module on /{msec}/",
                      f"A template module that links every /{msec}/ page to its three closest priority pages. The simulation shows a small effect (priority share {pct(sm['priority'])}), so treat it as a test, not a fix.",
                      "one template change", None,
                      "Test on a tenth of the section first.", "Compare the tested pages with the rest over 4 to 8 weeks."))
    if P.get("slot_scenario"):
        f_ = write_csv(f"{len(steps) + 1:02d}-sitewide-content-slots.csv", ["sitewide_link_now", "clicks", "would_point_at"],
                       [[x["out"], "" if x["clicks"] is None else int(x["clicks"]), x["in"]] for x in P["slot_scenario"]])
        sc = next((x for x in (P.get("scenarios") or []) if "content slots" in x["name"]), None)
        steps.append(("Only then: reassign sitewide content slots",
                      "The biggest lever in the simulation, and the riskiest: these slots point at content pages that may earn a lot of traffic. Move one or two at a time.",
                      f"{len(P['slot_scenario'])} slots" + (f"; simulated priority share {pct(sc['priority'])}" if sc else ""), f_,
                      "Search Console clicks for every page losing a slot (rerun with --gsc). Keep any page that earns real traffic.",
                      "Watch both sides: the page losing the slot and the page gaining it, over 4 to 8 weeks."))
    can = P.get("cannibalization") or []
    if can:
        f_ = write_csv(f"{len(steps) + 1:02d}-review-near-duplicates.csv", ["similarity", "page_a", "page_b"], [[x["similarity"], x["a"], x["b"]] for x in can])
        steps.append(("Review near-duplicate pages",
                      "Pairs of nearly identical pages split links and can compete for the same searches. Consolidate (301 the weaker page), differentiate the content, or canonicalise.",
                      f"{len(can)} pairs", f_,
                      "Check which queries each page ranks for in Search Console before merging.",
                      "After a merge, confirm the surviving page keeps or gains the combined impressions."))
    steps.append(("Recrawl and compare",
                  "Run sitefocus again on the live site with the same priority list and compare the Link Focus Score, the page map and the hygiene counts with this report.",
                  "one run", None, "Use the same --priority file and the same embedding model.",
                  "Repeat monthly while changes ship."))

    o.append('<section class="steps-wrap"><h2>How to execute, in order</h2><p>Safest and highest value first. Each step has its own list in <code>execution/</code>. Ship a step, check it, then move on.</p><ol class="steps">')
    for title, why, count, f_, check, measure in steps:
        o.append(f"<li><h3>{e(title)}</h3><p>{e(why)}</p><dl>"
                 f"<dt>Scope</dt><dd>{e(count)}</dd>"
                 + (f'<dt>List</dt><dd><a href="{e(f_)}"><code>{e(f_)}</code></a></dd>' if f_ else "")
                 + f"<dt>Before you ship</dt><dd>{e(check)}</dd><dt>Measure</dt><dd>{e(measure)}</dd></dl></li>")
    o.append("</ol></section>")

    o.append('<div class="score"><div><span class="big">' + f'{L["now"]}</span><span>Link Focus Score now</span></div>'
             f'<div><span class="big">{L["after_links"]}</span><span>after the links</span></div>'
             f'<div><span class="big">{L["after_links_and_swap"]}</span><span>with the sitewide swap</span></div></div>')
    o.append("<p>The Link Focus Score is the average topic score of the site's pages, weighted by how much internal PageRank each one holds. "
             "100 means all link importance sits on the most on-topic pages; 50 means it is spread with no regard to topic.</p>")

    o.append("<h2>What we found</h2><ul>")
    if a.notes:
        for line in open(a.notes, encoding="utf-8"):
            if line.strip():
                o.append(f"<li>{e(line.strip())}</li>")
    if orph:
        g = [x["pr_after_links"] / x["pr"] for x in orph]
        o.append(f"<li>{len(orph)} of {P['priority_pages']} priority pages have two or fewer internal links pointing at them. Linking them raises their internal PageRank {min(g):.0f} to {max(g):.0f} times, the biggest single gain in the plan.</li>")
    else:
        o.append(f"<li>Every priority page has at least three internal links pointing at it.</li>")
    o.append(f"<li>Underlinked pages (on topic, weakly linked): {Q['underlinked']['pages'][0]:,} pages holding {pct(Q['underlinked']['pagerank'][0])} of internal PageRank, {pct(Q['underlinked']['pagerank'][1])} after the plan.</li>")
    o.append(f"<li>Leaks (off topic, well linked): {Q['leak']['pages'][0]:,} pages holding {pct(Q['leak']['pagerank'][0])}. {len(P['sitewide_leaks'])} of them are linked from at least half the site.</li>")
    o.append(f"<li>Priority pages hold {pct(ps['priority'][0])} of internal PageRank now, {pct(ps['priority'][1])} after the links and {pct(ps['priority'][2])} with the swap.</li></ul>")

    sc = P.get("scenarios") or []
    if sc:
        o.append("<h2>What moves the needle</h2>")
        o.append("<p>Each scenario is simulated on the full link graph. Sitewide links (navigation, footer, sitewide modules) carry most of a site's internal PageRank, so changing a few sitewide slots usually moves more than thousands of contextual links.</p>")
        o.append(charts.bars([(x["name"], x["lfs"], x["changes"]) for x in sc], "Link Focus Score by scenario",
                             lambda v: f"{v:.1f}", "Link Focus Score"))
        o.append(charts.bars([(x["name"], 100 * x["priority"], x["changes"]) for x in sc], "Priority pages' share of internal PageRank by scenario",
                             lambda v: f"{v:.1f}%", "share of internal PageRank on priority pages"))
        o.append(table(["Scenario", "Link Focus Score", "Priority share", "What changes", "Check first"],
                       [[x["name"], x["lfs"], pct(x["priority"]), x["changes"], x["caveat"]] for x in sc]))
        if P.get("slot_scenario"):
            o.append("<h3>Sitewide content slots in the slot scenario</h3>")
            o.append(table(["Sitewide link now", "Clicks (GSC)", "Would point at"],
                           [[link(x["out"]), "" if x["clicks"] is None else int(x["clicks"]), link(x["in"])] for x in P["slot_scenario"]]))
            if not P.get("gsc"):
                o.append("<p>Without Search Console data the tool cannot tell whether these pages earn clicks. Rerun with <code>--gsc</code> before acting on this scenario.</p>")

    G = P.get("gsc")
    if G:
        o.append("<h2>Search Console</h2>")
        o.append(f"<p>{G['matched_pages']:,} crawled pages matched the export, {G['total_clicks']:,.0f} clicks in total. Pages with {G['protect_threshold']:,.0f} or more clicks ({G['protected_pages']:,} pages) are protected: they are never swapped out of sitewide slots.</p>")
        if G["near_wins"]:
            o.append("<h3>Near wins</h3><p>On-topic pages ranking in positions 4 to 20 with above-median impressions. The plan sends them links from strong, similar pages first.</p>")
            o.append(table(["Page", "Position", "Impressions", "Clicks", "Topic", "New links"],
                           [[link(x["url"]), round(x["position"], 1), int(x["impressions"]), int(x["clicks"]), x["topic"], x["new_links"]] for x in G["near_wins"][:25]]))
        if G["protected_sitewide"]:
            o.append("<h3>Protected sitewide links</h3>")
            o.append(table(["Page", "Clicks"], [[link(x["url"]), int(x["clicks"])] for x in G["protected_sitewide"]]))

    o.append("<h2>1. The page map</h2>")
    o.append("<p>Every page gets a topic score (0 to 100, how close it is to the priority pages) and a link score (0 to 100, how much internal PageRank it holds). Splitting both at the middle gives four groups.</p>")
    o.append(table(["Group", "What to do", "Pages", "Share of PageRank now", "After the links", "With the swap"],
                   [[k.capitalize(), ACTIONS[k], Q[k]["pages"][0], pct(Q[k]["pagerank"][0]), pct(Q[k]["pagerank"][1]), pct(Q[k]["pagerank"][2])]
                    for k in ("star", "underlinked", "leak", "long tail")]))
    top3 = [x["section"] for x in P["sections"][:3]]
    o.append(charts.page_map(pages, top3))
    o.append(charts.dumbbell([(k.capitalize(), Q[k]["pagerank"][0], Q[k]["pagerank"][1], Q[k]["pagerank"][2]) for k in ("star", "underlinked", "leak", "long tail")],
                             "Share of internal PageRank by group"))
    o.append("<p>Every page with its scores and group: <code>pages.csv</code>.</p>")

    o.append("<h2>2. Priority pages nothing links to</h2>")
    if orph:
        o.append(table(["Page", "Pages linking to it", "Internal PageRank now", "After the links", "With the swap"],
                       [[link(x["url"]), x["linking_pages"], f"{x['pr']:.1e}", f"{x['pr_after_links']:.1e}", f"{x['pr_after_swap']:.1e}"] for x in orph]))
        o.append(charts.dumbbell([(short_path(x["url"]), x["pr"], x["pr_after_links"], x["pr_after_swap"]) for x in orph],
                                 "Internal PageRank of each orphaned priority page", log=True))
        oa = [r for r in adds if r["reason"].startswith("orphan")]
        o.append("<p>Links into them: the parent hub first, then the eight most similar pages. Check a few in a real browser first; some menus are built in JavaScript.</p>")
        o.append(table(["From", "To", "Similarity", "Type"], [[link(r["source"]), link(r["target"]), float(r["similarity"]), r["reason"]] for r in oa[:40]]))
    else:
        o.append("<p>None.</p>")

    o.append("<h2>3. Links to add</h2>")
    c = P["adds"]
    o.append(f"<p>{P['adds_total']:,} links: {c.get('orphan: parent hub', 0) + c.get('orphan: similar page', 0)} into orphaned priority pages, "
             f"{c.get('priority', 0):,} from on-topic pages to their closest priority page, {c.get('near win', 0):,} to near wins from Search Console, {c.get('underlinked', 0):,} from strong pages to underlinked ones. "
             "Strongest source pages go first. At most three per page, never between near-duplicates. Place each in body copy where the topic comes up. Full list with suggested anchors: <code>link_adds.csv</code>.</p>")
    for reason, label in (("priority", "To priority pages"), ("near win", "To near wins"), ("underlinked", "To underlinked pages")):
        rows = [r for r in adds if r["reason"] == reason]
        if rows:
            o.append(f"<h3>{label}</h3>")
            o.append(table(["From", "To", "Similarity", "Suggested anchor"], [[link(r["source"]), link(r["target"]), float(r["similarity"]), r["suggested_anchor"][:60]] for r in rows[:20]]))
            o.append(table(["Page receiving links", "New links"], [[link(u), k] for u, k in Counter(r["target"] for r in rows).most_common(10)]))

    o.append("<h2>4. Sitewide links</h2>")
    o.append(f"<p>{P['sitewide_targets']} pages are linked from at least half the site (navigation, footer, sitewide modules). Full list: <code>sitewide.csv</code>.</p>")
    flagged = [r for r in sw if r["flag"] and r["flag"] != "leak"]
    if flagged:
        o.append("<h3>Slots that could go to products</h3>")
        o.append(table(["Page", "Linked from", "Topic", "Why"], [[link(r["target"]), int(r["linked_from_pages"]), float(r["topic"]), r["flag"]] for r in flagged]))
    if P["swap"]:
        o.append("<p>Swap scenario: give those slots to orphaned priority pages. The pairing is illustrative; the slot is what matters.</p>")
        o.append(table(["Sitewide link now", "Give the slot to"], [[link(x["out"]), link(x["in"])] for x in P["swap"]]))
    if P["sitewide_leaks"]:
        o.append("<h3>Sitewide leaks to review</h3><p>Low topic score, linked from most pages. Often these are valuable editorial pages that earn their place for users; review them, do not remove them on this score alone.</p>")
        o.append(table(["Page", "Topic", "Title"], [[link(x["url"]), x["topic"], x["title"][:70]] for x in P["sitewide_leaks"]]))

    R = P.get("reach")
    if R:
        o.append("<h2>Reach: how woven into the site each page is</h2>")
        o.append("<p>Reach is internal harmonic centrality: the average of 1 ÷ clicks from every other page. 1.0 means one click from every page, 0.5 about two clicks, 0 unreachable by links. "
                 "PageRank says how much link weight flows in; reach says how easily the rest of the site gets there. "
                 f"On this site the two agree only moderately (rank correlation {R['spearman_links']}), so reach adds information.</p>")
        o.append(table(["", "Now", "After the links"], [
            ["Median reach, priority pages", R["priority_reach_median"][0], R["priority_reach_median"][1]],
            ["Median reach, all pages", R["site_reach_median"][0], R["site_reach_median"][1]]]))
        low = sorted(R["priority"], key=lambda x: x["hc_now"])[:12]
        o.append("<h3>Priority pages with the least reach</h3>")
        o.append(table(["Priority page", "Reach now", "After the links", "Link score"], [[link(x["url"]), x["hc_now"], x["hc_after"], x["links"]] for x in low]))
        if R["links_ahead"]:
            o.append("<h3>PageRank ahead of reach</h3><p>High link score, low reach: these pages are fed by a few strong links rather than woven into the site (the page-level version of Geraci's gap). One strong source changing would drop them. Give them more, ordinary contextual links from related pages.</p>")
            o.append(table(["Page", "Link score", "Reach score", "Pages linking to it"], [[link(x["url"]), x["links"], x["reach"], x["linking_pages"]] for x in R["links_ahead"][:12]]))
        if R["reach_ahead"]:
            o.append("<h3>Reach ahead of PageRank</h3><p>Easy to reach but little link weight: linked from many weak pages. Links from stronger pages would help most.</p>")
            o.append(table(["Page", "Link score", "Reach score", "Pages linking to it"], [[link(x["url"]), x["links"], x["reach"], x["linking_pages"]] for x in R["reach_ahead"][:12]]))

    H = P.get("hygiene") or {}
    if H:
        o.append("<h2>Link hygiene</h2>")
        o.append("<p>Internal links that waste PageRank or crawl budget. Fixing a sitewide one fixes it on every page at once.</p>")
        rows_h = []
        labels = {"to_redirect": ("Links to redirects", "Point the link at the final URL."),
                  "to_broken": ("Links to broken pages", "Fix or remove the link."),
                  "to_nonindexable": ("Links to noindexed or canonicalised pages", "Link to the canonical page, or move the link out of the template."),
                  "rate_limited": ("Links the crawl could not check (rate limited)", "Recheck these URLs; they are not known to be broken."),
                  "to_uncrawled": ("Links to pages blocked or never fetched", "Usually robots.txt; check they are meant to be blocked."),
                  "nofollow": ("Internal nofollow links", "Remove nofollow, or remove the link; nofollow does not redirect PageRank."),
                  "account": ("Links to sign-in, sign-up, account or cart pages", "Fine for users. Keep them out of body copy; nofollow is acceptable here for crawl hygiene.")}
        for k, (lab, fix) in labels.items():
            v = H.get(k)
            if v and v["links"]:
                rows_h.append([lab, v["links"], v["targets"], fix])
        o.append(table(["Issue", "Links", "Distinct targets", "Fix"], rows_h or [["No issues found", 0, 0, ""]]))
        for k in ("to_redirect", "to_broken", "to_nonindexable"):
            v = H.get(k)
            if v and v["top"]:
                o.append(f"<h3>{labels[k][0]}: most linked</h3>")
                o.append(table(["Target", "Links to it"], [[link(u), c] for u, c in v["top"][:10]]))

    o.append("<h2>Where nofollow belongs</h2>")
    o.append("<p>Nofollow cannot steer PageRank. Since 2009 a nofollowed internal link still spends its share of the page's PageRank; that share is simply discarded. Nofollowing links to concentrate PageRank on other pages loses PageRank instead. What to do instead:</p><ul>"
             "<li><strong>Remove or move the link</strong> when a page should stop receiving importance, especially from the sitewide template (see the scenarios above).</li>"
             "<li><strong>Fix links to redirects and noindexed pages</strong> (Link hygiene above): they pass PageRank to pages that cannot rank.</li>"
             "<li><strong>Consolidate near-duplicates</strong> (below) so PageRank is not split between pages competing for the same query.</li></ul>"
             "<p>Where nofollow is appropriate, for reasons other than PageRank:</p><ul>"
             "<li>Sign-in, sign-up, account and cart links: no search value; nofollow or keep them out of body copy.</li>"
             "<li>Paid, affiliate or partner links: <code>rel=\"sponsored\"</code> is required.</li>"
             "<li>User-generated links (comments, forums, profiles): <code>rel=\"ugc\"</code>.</li>"
             "<li>Internal search results and filtered or faceted URLs: block them in robots.txt or noindex them; do not rely on nofollow.</li></ul>")
    acc = (H.get("account") or {}).get("top") or []
    if acc:
        o.append(table(["Sign-in / account link found", "Links"], [[link(u), c] for u, c in acc[:8]]))

    D = P.get("depth")
    if D:
        o.append("<h2>Click depth</h2>")
        order = ["0", "1", "2", "3", "4", "5", "6+", "not reached"]
        o.append(table(["Clicks from the home page", "Pages"], [[k, D["distribution"][k]] for k in order if k in D["distribution"]]))
        if D["priority_deep"]:
            o.append("<p>Priority pages more than three clicks from the home page, or not reachable by links at all:</p>")
            o.append(table(["Priority page", "Depth"], [[link(x["url"]), "not reached" if x["depth"] < 0 else x["depth"]] for x in D["priority_deep"]]))

    A = P.get("anchors")
    if A:
        o.append("<h2>Anchor text pointing at priority pages</h2>")
        o.append("<p>Descriptive, varied anchors tell Google what a page is about. Watch for generic anchors (learn more, click here) and pages whose only anchor is a navigation label.</p>")
        o.append(table(["Priority page", "Linking pages", "Distinct anchors", "Generic share", "Most common anchors"],
                       [[link(x["url"]), x["linking_pages"], x["distinct_anchors"], pct(x["generic_share"]),
                         "; ".join(f"{k} ({v:,})" for k, v in x["top_anchors"][:3])] for x in sorted(A, key=lambda x: (x["linking_pages"], x["distinct_anchors"]))]))

    C = P.get("cannibalization")
    if C:
        o.append("<h2>Possible cannibalization</h2>")
        o.append("<p>Pairs of indexable pages in the same editorial section whose content is nearly identical (cosine 0.95 or more). They split links and can compete for the same queries. Consolidate, differentiate, or canonicalise; check Search Console queries for each pair first.</p>")
        o.append(table(["Similarity", "Page A", "Page B"] + (["Clicks A / B"] if C[0]["a_clicks"] is not None else []),
                       [[x["similarity"], link(x["a"]), link(x["b"])] + ([f"{int(x['a_clicks'])} / {int(x['b_clicks'])}"] if x["a_clicks"] is not None else []) for x in C[:20]]))

    o.append("<h2>5. By section</h2>")
    nd = P["near_duplicates"]
    o.append(table(["Section", "Pages", "Median topic", "PageRank now", "After the links", "With the swap", "Points of score", "Pages with a near-duplicate"],
                   [[f"/{s['section']}/", s["pages"], s["topic_median"], pct(s["pr"][0]), pct(s["pr"][1]), pct(s["pr"][2]), s["lfs_contribution"],
                     pct(nd[s["section"]]["share"]) if s["section"] in nd else ""] for s in P["sections"][:15]]))
    o.append("<p>Points of score: how much each section contributes to the Link Focus Score today. They add up to the score.</p>")
    o.append(charts.dumbbell([("/" + x["section"] + "/", x["pr"][0], x["pr"][1], x["pr"][2]) for x in sorted(P["sections"], key=lambda x: -x["pr"][0])[:8]],
                             "Share of internal PageRank by section, top 8"))

    o.append("<h2>6. Topic clusters</h2>")
    o.append(table(["Cluster", "Pages", "Main sections", "Example pages"],
                   [[k["cluster"], k["pages"], ", ".join(f"/{s}/ {v}" for s, v in k["sections"].items()), " · ".join(k["examples"][:3])] for k in F["clusters"]]))

    o.append("<h2>Method and limits</h2><ul>"
             "<li>Google's leaked Content Warehouse API documentation (May 2024) names site focus and radius attributes (siteFocusScore, siteRadius, siteEmbeddings). It does not publish formulas, models or weights. Everything here is a transparent proxy.</li>"
             "<li>Topic: cosine similarity of each page to the centre of the priority pages, as a percentile. Page vectors come from nomic-embed-text (run locally) over title, H1 and main text, with text repeated on more than 15% of pages removed.</li>"
             f"<li>The topic is anchored on the priority pages on purpose. The plain proxy (similarity to the average page: focus {P['plain_focus']['focus']}, median radius {P['plain_focus']['radius_median']}) is set by whatever the site has most of, and on directory-heavy sites it calls editorial content off-topic.</li>"
             f"<li>Links: internal PageRank, damping 0.85, one edge per page pair, indexable same-host pages. Navigation, footer and sitewide links count {P.get('model', {}).get('template_weight', 1)} of a link in body copy (a reasonable-surfer approximation). "
             + ("External links are included: each page's starting share grows with the square root of its linking sites from the backlink export. " if P.get("model", {}).get("backlinks") else "External links are not included (add --backlinks). ")
             + "Read the simulation as direction and size, not a forecast.</li>"
             "<li>Crawl: same-host from the home page and every sitemap, robots.txt obeyed, server HTML only.</li>"
             "<li>Do not use nofollow on internal links to steer PageRank. Since 2009 a nofollowed link still uses its share of the page's PageRank; the share is simply not passed on, so nothing is redirected to other pages. To stop sending importance somewhere, remove or move the link (for example out of the sitewide template).</li>"
             "<li>Long pages are embedded in up to three windows of about 5,500 characters each and averaged, so articles are scored on more than their opening.</li>"
             "<li>Ship orphan links first, measure those pages in Search Console over 4 to 8 weeks, then roll out the rest.</li></ul>")
    os.makedirs(os.path.dirname(os.path.abspath(a.out)), exist_ok=True)
    d = os.path.dirname(os.path.abspath(a.out))
    shutil.copy(css_path(), os.path.join(d, "report.css"))
    for f in ("pages.csv", "link_adds.csv", "sitewide.csv"):
        shutil.copy(os.path.join(a.plan, f), os.path.join(d, f))
    open(a.out, "w", encoding="utf-8").write(
        '<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">'
        f"<title>Site focus and internal linking: {e(a.site)}</title><link rel=\"stylesheet\" href=\"report.css\"></head><body><main>" + "\n".join(o) + "</main>" + charts.SCRIPT + "</body></html>")
    print(f"wrote {a.out}")


if __name__ == "__main__":
    main()
