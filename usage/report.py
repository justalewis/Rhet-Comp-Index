"""usage/report.py — the numbers behind /admin/usage and the grant report.

The unit throughout is the visitor-day: one browser on one network on one
day. Visitor ids rotate daily by design, so the same person on Monday and
Tuesday counts twice and nobody can be followed from one day to the next.
That makes "visitor-days" honest where "unique visitors" would overclaim.

Who counts as a person is a parameter. The default is everyone classed human
(the page's script ran) or likely-human (a normal browser on a normal network
that never ran the script, e.g. with JavaScript blocked). Bots, feed readers
and datacenter clients are reported separately, never mixed in.

`public=True` applies the small-count rule for anything shared outside the
project: an institution, search term, referring site or article appears only
with at least `min_count` visitor-days, and the rest are summarised as counts.
"""

from __future__ import annotations

import csv
import datetime as _dt
import io
import json
import os
import sqlite3
import statistics
import zipfile

from . import store
from .geo import ATTRIBUTION
from .names import country_name, language_name
from .survey import summarise as summarise_responses
from .resolve import raw_ip_hours
from .tools import is_auto, label_for

# Browser-confirmed only by default. Likely-human is an opt-in: a crawler
# that rotates its address and never runs the script can still look like an
# ordinary browser, and on 2026-10-17 one did, 73,000 times a day.
PEOPLE = ("human",)
CLASS_LABELS = {
    "human": "People (browser confirmed)",
    "likely-human": "People (likely)",
    "unverified": "Unverified",
    "bot": "Bots and scripts",
    "feed-reader": "Feed readers",
    "datacenter": "Datacenter clients",
}
NET_LABELS = {
    "education": "Education", "government": "Government", "organization": "Other organisations",
    "isp": "Home and business internet", "mobile": "Mobile networks",
    "hosting": "Hosting and cloud", "unknown": "Unknown", "local": "Local / unresolved",
}
KIND_LABELS = {
    "university": "Universities", "college": "Colleges",
    "community-college": "Community and technical colleges", "k12": "K-12 schools and districts",
    "government": "Government", "library": "Libraries", "healthcare": "Healthcare",
    "research": "Research facilities", "nonprofit": "Nonprofits", "company": "Companies",
    "other": "Other",
}

_REF_GROUPS = [
    ("Learning management systems", ("instructure.com", "canvas", "blackboard", "brightspace",
                                     "d2l", "moodle", "schoology", "sakai", "elearning.", "lms.",
                                     "learn.", "classroom.google")),
    ("Library guides and discovery", ("libguides", "lib.", "library", "primo", "ebsco",
                                      "proquest", "worldcat", "summon", "jstor")),
    ("AI assistants", ("chatgpt.com", "chat.openai", "perplexity.ai", "claude.ai",
                       "gemini.google", "copilot.microsoft", "you.com", "phind")),
    ("Search engines", ("google.", "bing.", "duckduckgo", "yahoo.", "baidu", "yandex",
                        "ecosia", "brave.com", "qwant", "startpage", "scholar.google")),
    ("Social and newsletters", ("facebook", "t.co", "twitter", "x.com", "linkedin", "bsky",
                                "reddit", "mastodon", "substack", "threads.net", "instagram")),
    ("Academic sites", (".edu", ".ac.", "wac.colostate", "wacclearinghouse")),
]


def ref_group(host: str | None) -> str:
    if not host:
        return "Direct or unknown"
    h = host.lower()
    for label, needles in _REF_GROUPS:
        if any(n in h for n in needles):
            return label
    return "Other sites"


def _corpus_conn():
    import db as _db
    path = os.path.abspath(_db.DB_PATH)
    if not os.path.exists(path):
        return None
    try:
        return sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=5)
    except sqlite3.Error:
        return None


def _article_titles(ids: list[str]) -> dict[str, dict]:
    conn = _corpus_conn()
    if conn is None or not ids:
        return {}
    out = {}
    try:
        ints = [int(i) for i in ids if str(i).isdigit()]
        for start in range(0, len(ints), 500):
            chunk = ints[start:start + 500]
            q = f"SELECT id, title, journal, substr(pub_date, 1, 4) FROM articles WHERE id IN ({','.join('?' * len(chunk))})"
            for aid, title, journal, year in conn.execute(q, chunk):
                out[str(aid)] = {"title": title, "journal": journal, "year": year}
    except sqlite3.Error:
        pass
    finally:
        conn.close()
    return out


def _doi_titles(dois: list[str]) -> dict[str, dict]:
    conn = _corpus_conn()
    if conn is None or not dois:
        return {}
    out = {}
    try:
        for d in dois:
            row = conn.execute("SELECT title, journal FROM articles WHERE lower(doi) = ? LIMIT 1",
                               (d,)).fetchone()
            if row:
                out[d] = {"title": row[0], "journal": row[1]}
    except sqlite3.Error:
        pass
    finally:
        conn.close()
    return out


def default_range(days: int = 30, today: _dt.date | None = None) -> tuple[str, str]:
    today = today or store.utc_now().date()
    return (today - _dt.timedelta(days=days - 1)).isoformat(), today.isoformat()


def build_summary(start: str, end: str, *, classes: tuple[str, ...] = PEOPLE,
                  public: bool = False, min_count: int = 3) -> dict:
    """Everything the dashboard and report show for [start, end] inclusive."""
    _dt.date.fromisoformat(start)
    _dt.date.fromisoformat(end)
    if start > end:
        start, end = end, start
    classes = tuple(c for c in classes if c in CLASS_LABELS) or PEOPLE
    floor = min_count if public else 1
    cls_sql = ",".join("?" * len(classes))
    conn = store.connect()
    try:
        P = (start, end, *classes)                    # params for vd-filtered queries
        VD = f"v.day BETWEEN ? AND ? AND v.class IN ({cls_sql})"
        JOIN_H = "JOIN visitor_days v ON v.day = h.day AND v.visitor = h.visitor"
        JOIN_E = "JOIN visitor_days v ON v.day = e.day AND v.visitor = e.visitor"

        by_class = {r[0]: r[1] for r in conn.execute(
            "SELECT class, COUNT(*) FROM visitor_days WHERE day BETWEEN ? AND ? GROUP BY class",
            (start, end))}
        people = sum(by_class.get(c, 0) for c in classes)

        daily = {}
        for day, cls, n in conn.execute(
                "SELECT day, class, COUNT(*) FROM visitor_days WHERE day BETWEEN ? AND ? "
                "GROUP BY day, class ORDER BY day", (start, end)):
            daily.setdefault(day, {})[cls] = n
        d0, d1 = _dt.date.fromisoformat(start), _dt.date.fromisoformat(end)
        series = []
        for i in range((d1 - d0).days + 1):
            day = (d0 + _dt.timedelta(days=i)).isoformat()
            row = daily.get(day, {})
            series.append({"day": day, **{c: row.get(c, 0) for c in CLASS_LABELS}})

        pageviews = conn.execute(
            f"SELECT COUNT(*) FROM hits h {JOIN_H} WHERE {VD} AND h.is_api = 0 "
            "AND COALESCE(h.tool, '') != 'feed'", P).fetchone()[0]

        # Tools: a visitor-day counts once per tool, whether it reached the tool
        # by running it (an API call) or by opening its tab (a browser event).
        tool_rows = conn.execute(f"""
            SELECT tool, COUNT(*) AS vd FROM (
                SELECT DISTINCT h.day, h.visitor, h.tool FROM hits h {JOIN_H}
                 WHERE {VD} AND h.tool IS NOT NULL AND h.tool NOT IN ('typeahead', 'feed')
                UNION
                SELECT DISTINCT e.day, e.visitor, e.tool FROM events e {JOIN_E}
                 WHERE {VD} AND e.tool IS NOT NULL AND e.type IN ('view', 'nav')
            ) GROUP BY tool ORDER BY vd DESC""", P + P).fetchall()
        runs = {r[0]: r[1] for r in conn.execute(
            f"SELECT h.tool, COUNT(*) FROM hits h {JOIN_H} WHERE {VD} AND h.tool IS NOT NULL "
            "AND (h.is_api = 1 OR h.tool LIKE 'export%') GROUP BY h.tool", P)}
        areas_of = {r[0]: r[1] for r in conn.execute(
            "SELECT tool, MIN(area) FROM hits WHERE tool IS NOT NULL GROUP BY tool")}
        tools = [{"tool": t, "label": label_for(t), "area": areas_of.get(t, ""),
                  "visitor_days": n, "runs": runs.get(t, 0), "auto": is_auto(t)}
                 for t, n in tool_rows]

        areas = [{"area": a, "visitor_days": n, "pageviews": pv} for a, n, pv in conn.execute(
            f"SELECT h.area, COUNT(DISTINCT h.day || h.visitor), SUM(h.is_api = 0) "
            f"FROM hits h {JOIN_H} WHERE {VD} GROUP BY h.area ORDER BY 2 DESC", P)]

        tool_users = conn.execute(
            f"SELECT COUNT(*) FROM visitor_days v WHERE {VD} AND v.tools > 0", P).fetchone()[0]

        # Institutions.
        inst_rows = conn.execute(f"""
            SELECT i.*, COUNT(*) AS vd, COUNT(DISTINCT v.day) AS days
              FROM visitor_days v JOIN institutions i ON i.id = v.institution_id
             WHERE {VD} GROUP BY i.id ORDER BY vd DESC, i.name""", P).fetchall()
        inst_tools: dict[int, dict[str, int]] = {}
        for iid, tool, n in conn.execute(f"""
                SELECT v.institution_id, h.tool, COUNT(DISTINCT h.day || h.visitor)
                  FROM hits h {JOIN_H}
                 WHERE {VD} AND v.institution_id IS NOT NULL AND h.tool IS NOT NULL
                   AND h.tool NOT IN ('typeahead', 'feed')
                 GROUP BY v.institution_id, h.tool""", P):
            inst_tools.setdefault(iid, {})[tool] = n
        institutions, hidden = [], 0
        breakdown = {"kind": {}, "carnegie": {}, "sector": {}, "country": {}}
        breakdown_skip_unknown = {"carnegie", "sector"}      # US-only fields
        flags = {"hbcu": 0, "tribal": 0, "in_corpus": 0}
        for r in inst_rows:
            for key in breakdown:
                if key in breakdown_skip_unknown and not r[key]:
                    continue
                val = r[key] or "Unknown"
                if key == "kind":
                    val = KIND_LABELS.get(r["kind"] or "other", r["kind"] or "Other")
                elif key == "country":
                    val = country_name(r["country"])
                breakdown[key][val] = breakdown[key].get(val, 0) + 1
            for f in flags:
                flags[f] += 1 if r[f] else 0
            if r["vd"] < floor:
                hidden += 1
                continue
            top = sorted(((t, n) for t, n in inst_tools.get(r["id"], {}).items() if not is_auto(t)),
                         key=lambda kv: -kv[1])[:3]
            institutions.append({
                "name": r["name"], "kind": KIND_LABELS.get(r["kind"] or "other", r["kind"]),
                "country": r["country"], "country_name": country_name(r["country"]),
                "region": r["region"], "city": r["city"],
                "carnegie": r["carnegie"], "sector": r["sector"],
                "hbcu": bool(r["hbcu"]), "tribal": bool(r["tribal"]),
                "in_corpus": bool(r["in_corpus"]), "ror_id": r["ror_id"],
                "visitor_days": r["vd"], "days": r["days"],
                "top_tools": [label_for(t) for t, _ in top],
            })
        identified_vd = sum(r["vd"] for r in inst_rows)

        net = {NET_LABELS.get(k or "unknown", k): n for k, n in conn.execute(
            f"SELECT v.net_class, COUNT(*) FROM visitor_days v WHERE {VD} "
            "GROUP BY v.net_class ORDER BY 2 DESC", P)}
        countries = [{"country": c or "Unknown", "name": country_name(c), "visitor_days": n}
                     for c, n in conn.execute(
            f"SELECT v.country, COUNT(*) FROM visitor_days v WHERE {VD} "
            "GROUP BY v.country ORDER BY 2 DESC", P)]
        us_regions = [{"region": r or "Unknown", "visitor_days": n} for r, n in conn.execute(
            f"SELECT v.region, COUNT(*) FROM visitor_days v WHERE {VD} AND v.country = 'US' "
            "GROUP BY v.region ORDER BY 2 DESC", P)]

        ref_rows = conn.execute(
            f"SELECT v.ref_host, COUNT(*) FROM visitor_days v WHERE {VD} "
            "GROUP BY v.ref_host ORDER BY 2 DESC", P).fetchall()
        ref_groups: dict[str, int] = {}
        referrers, ref_hidden = [], 0
        for host, n in ref_rows:
            g = ref_group(host)
            ref_groups[g] = ref_groups.get(g, 0) + n
            if not host:
                continue
            if n < floor:
                ref_hidden += 1
                continue
            referrers.append({"host": host, "group": g, "visitor_days": n})

        langs = [{"lang": language_name(l), "visitor_days": n} for l, n in conn.execute(
            f"SELECT v.lang, COUNT(*) FROM visitor_days v WHERE {VD} GROUP BY v.lang", P)]
        lang_totals: dict[str, int] = {}
        for row in langs:
            lang_totals[row["lang"]] = lang_totals.get(row["lang"], 0) + row["visitor_days"]
        devices = {d or "unknown": n for d, n in conn.execute(
            f"SELECT v.device, COUNT(*) FROM visitor_days v WHERE {VD} GROUP BY v.device", P)}

        search_floor = max(floor, 2)
        searches = [{"query": q, "visitor_days": n} for q, n in conn.execute(f"""
            SELECT lower(trim(h.query)), COUNT(DISTINCT h.day || h.visitor) FROM hits h {JOIN_H}
             WHERE {VD} AND h.query IS NOT NULL AND h.endpoint IN ('main.index', 'articles.api_articles')
             GROUP BY lower(trim(h.query)) HAVING COUNT(DISTINCT h.day || h.visitor) >= ?
             ORDER BY 2 DESC LIMIT 40""", P + (search_floor,))]
        search_vd = conn.execute(
            f"SELECT COUNT(DISTINCT h.day || h.visitor) FROM hits h {JOIN_H} WHERE {VD} "
            "AND h.query IS NOT NULL AND h.endpoint IN ('main.index', 'articles.api_articles')",
            P).fetchone()[0]

        art_rows = conn.execute(f"""
            SELECT h.object, COUNT(DISTINCT h.day || h.visitor) FROM hits h {JOIN_H}
             WHERE {VD} AND h.endpoint = 'articles.article_detail' AND h.object IS NOT NULL
             GROUP BY h.object HAVING COUNT(DISTINCT h.day || h.visitor) >= ?
             ORDER BY 2 DESC LIMIT 25""", P + (floor,)).fetchall()
        titles = _article_titles([r[0] for r in art_rows])
        articles = [{"id": a, "visitor_days": n, **titles.get(str(a), {"title": f"Article {a}"})}
                    for a, n in art_rows]
        article_views = conn.execute(
            f"SELECT COUNT(*) FROM hits h {JOIN_H} WHERE {VD} AND h.endpoint = 'articles.article_detail'",
            P).fetchone()[0]

        out_rows = conn.execute(f"""
            SELECT e.target, COUNT(DISTINCT e.day || e.visitor) FROM events e {JOIN_E}
             WHERE {VD} AND e.type = 'out' GROUP BY e.target ORDER BY 2 DESC""", P).fetchall()
        doi_clicks = sum(n for t, n in out_rows if t and t.startswith("doi:"))
        top_dois = [(t[4:], n) for t, n in out_rows if t and t.startswith("doi:") and n >= floor][:20]
        doi_meta = _doi_titles([d for d, _ in top_dois])
        outbound = {
            "doi_clicks": doi_clicks,
            "other_clicks": sum(n for t, n in out_rows if t and not t.startswith("doi:")),
            "top_dois": [{"doi": d, "visitor_days": n, **doi_meta.get(d, {})} for d, n in top_dois],
        }
        downloads = {k or "file": n for k, n in conn.execute(
            f"SELECT e.target, COUNT(*) FROM events e {JOIN_E} WHERE {VD} AND e.type = 'dl' "
            "GROUP BY e.target ORDER BY 2 DESC", P)}
        for tool, n in runs.items():
            if tool.startswith("export"):
                key = "ris" if tool.endswith("ris") else "bibtex"
                downloads[key] = max(downloads.get(key, 0), n)

        engaged = [r[0] for r in conn.execute(
            f"SELECT v.engaged_ms FROM visitor_days v WHERE {VD} AND v.engaged_ms > 0", P)]
        pages_per = conn.execute(
            f"SELECT AVG(v.pages) FROM visitor_days v WHERE {VD} AND v.pages > 0", P).fetchone()[0]

        feeds = {}
        for bot, obj, subs in conn.execute(
                "SELECT bot, COALESCE(object, rule), MAX(feed_subs) FROM hits "
                "WHERE day BETWEEN ? AND ? AND bot_kind = 'feed' GROUP BY bot, COALESCE(object, rule)",
                (start, end)):
            f = feeds.setdefault(bot, {"reader": bot, "feeds": 0, "subscriptions": 0})
            f["feeds"] += 1
            f["subscriptions"] += subs or 0
        bots = [{"bot": b or "Unidentified", "visitor_days": n, "hits": h} for b, n, h in conn.execute(
            "SELECT bot, COUNT(*), SUM(hits) FROM visitor_days WHERE day BETWEEN ? AND ? "
            "AND class IN ('bot', 'datacenter') GROUP BY bot ORDER BY 3 DESC LIMIT 25", (start, end))]

        last_run = conn.execute(
            "SELECT started_at, finished_at, ok, summary FROM runs WHERE job = 'resolve' "
            "ORDER BY id DESC LIMIT 1").fetchone()
        geo_sources = []
        if last_run and last_run["summary"]:
            try:
                geo_sources = json.loads(last_run["summary"]).get("geo_sources") or []
            except ValueError:
                pass
        unresolved = conn.execute(
            "SELECT COUNT(*) FROM hits WHERE resolved = 0 AND day BETWEEN ? AND ?",
            (start, end)).fetchone()[0]
        responses = summarise_responses(conn, start, end, kind_labels=KIND_LABELS)
    finally:
        conn.close()

    return {
        "period": {"start": start, "end": end, "days": (d1 - d0).days + 1},
        "generated_at": store.utc_now().isoformat(timespec="seconds") + "Z",
        "public": public, "min_count": floor, "classes": list(classes),
        "class_labels": CLASS_LABELS,
        "totals": {
            "people_visitor_days": people,
            "by_class": {c: by_class.get(c, 0) for c in CLASS_LABELS},
            "pageviews": pageviews,
            "tool_users": tool_users,
            "tool_runs": sum(t["runs"] for t in tools if not t["auto"]),
            "tools_used": len([t for t in tools if not t["auto"]]),
            "institutions": len(inst_rows),
            "institution_visitor_days": identified_vd,
            "identified_share": (identified_vd / people) if people else 0,
            "countries": len([c for c in countries if c["country"] != "Unknown"]),
            "us_regions": len([r for r in us_regions if r["region"] != "Unknown"]),
            "article_views": article_views,
            "doi_clicks": doi_clicks,
            "searches": search_vd,
            "median_engaged_s": round(statistics.median(engaged) / 1000) if engaged else None,
            "pages_per_visitor_day": round(pages_per, 1) if pages_per else None,
            "feed_subscriptions": sum(f["subscriptions"] for f in feeds.values()),
        },
        "daily": series,
        "tools": tools,
        "areas": areas,
        "institutions": institutions,
        "institutions_hidden": hidden,
        "institution_breakdown": breakdown,
        "institution_flags": flags,
        "networks": net,
        "countries": countries,
        "us_regions": us_regions,
        "referrer_groups": dict(sorted(ref_groups.items(), key=lambda kv: -kv[1])),
        "referrers": referrers[:40],
        "referrers_hidden": ref_hidden,
        "languages": dict(sorted(lang_totals.items(), key=lambda kv: -kv[1])),
        "devices": devices,
        "searches": searches,
        "articles": articles,
        "outbound": outbound,
        "downloads": downloads,
        "feeds": sorted(feeds.values(), key=lambda f: -f["subscriptions"]),
        "bots": bots,
        "responses": responses,
        "data": {
            "last_resolve": dict(last_run) if last_run else None,
            "unresolved_hits": unresolved,
            "raw_ip_hours": raw_ip_hours(),
            "attribution_html": [ATTRIBUTION[s][0] for s in geo_sources if s in ATTRIBUTION],
            "attribution_text": [ATTRIBUTION[s][1] for s in geo_sources if s in ATTRIBUTION],
        },
    }


# ── Exports ──────────────────────────────────────────────────────────────────

def _csv(rows: list[dict], columns: list[str]) -> str:
    buf = io.StringIO()
    w = csv.DictWriter(buf, fieldnames=columns, extrasaction="ignore")
    w.writeheader()
    for r in rows:
        w.writerow({k: (", ".join(v) if isinstance(v, list) else v) for k, v in r.items()})
    return buf.getvalue()


def to_zip(summary: dict) -> bytes:
    """One CSV per table, plus the full summary as JSON."""
    files = {
        "daily.csv": _csv(summary["daily"], ["day", *CLASS_LABELS]),
        "tools.csv": _csv(summary["tools"], ["label", "tool", "area", "visitor_days", "runs", "auto"]),
        "institutions.csv": _csv(summary["institutions"], [
            "name", "kind", "country", "region", "city", "carnegie", "sector", "hbcu",
            "tribal", "in_corpus", "ror_id", "visitor_days", "days", "top_tools"]),
        "countries.csv": _csv(summary["countries"], ["country", "visitor_days"]),
        "us_states.csv": _csv(summary["us_regions"], ["region", "visitor_days"]),
        "referrers.csv": _csv(summary["referrers"], ["host", "group", "visitor_days"]),
        "searches.csv": _csv(summary["searches"], ["query", "visitor_days"]),
        "articles.csv": _csv(summary["articles"], ["id", "title", "journal", "year", "visitor_days"]),
        "form_institutions.csv": _csv(summary["responses"]["institutions"],
                                      ["name", "kind", "carnegie", "country", "responses", "matched"]),
        "form_quotes.csv": _csv(summary["responses"]["quotes"], ["date", "role", "institution", "note"]),
        "summary.json": json.dumps(summary, indent=2, default=str),
    }
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for name, text in files.items():
            zf.writestr(name, text)
    return buf.getvalue()


def render_html(summary: dict) -> str:
    """The printable grant report, standalone (its own CSS, no site chrome)."""
    from jinja2 import Environment, FileSystemLoader, select_autoescape
    here = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    env = Environment(loader=FileSystemLoader(os.path.join(here, "templates")),
                      autoescape=select_autoescape(["html"]))
    env.filters["num"] = lambda n: f"{n:,}" if isinstance(n, (int, float)) else (n or "—")
    env.filters["pct"] = lambda x: f"{x * 100:.0f}%" if isinstance(x, (int, float)) else "—"
    return env.get_template("usage_report.html").render(s=summary, max=max)
