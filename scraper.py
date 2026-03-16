"""
scraper.py — Web scrapers for journals without RSS feeds.

Covered journals and strategies:
  kairos  — Kairos (technorhetoric.net) — custom static HTML, vol/issue URL pattern
  praxis  — Praxis: A Writing Center Journal (Squarespace) — sitemap + issue pages
  jmr     — Journal of Multimodal Rhetorics — custom Ruby/Rack app, nav-based discovery
  bwe     — Basic Writing e-Journal (CUNY) — static HTML, Archives.html index
  woe     — Writing on the Edge (UC Davis) — Drupal 10, RSS blocked, /issues page

Metadata quality note: scraped articles often lack author lists and abstracts.
The source field is set to 'scrape' so the UI can flag these entries.

Usage:
    python scraper.py
"""

import re
import logging
import requests
from bs4 import BeautifulSoup

from db import init_db, upsert_article, update_fetch_log
from journals import SCRAPE_JOURNALS, RSS_JOURNALS
from tagger import auto_tag

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)s  %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger(__name__)

HEADERS = {"User-Agent": "RhetCompIndex/1.0 (mailto:your-email@example.com)"}
TIMEOUT = 20

# Anchor-only URLs, mailto links, static assets, etc.
SKIP_PATTERNS = re.compile(
    r"(^#|mailto:|javascript:|\.css|\.js|\.png|\.jpg|\.gif|\.pdf)", re.I
)

# Generic nav/UI text to ignore when extracting titles from links
NAV_WORDS = {
    "home", "about", "contact", "submit", "archive", "archives", "index",
    "editorial board", "masthead", "subscribe", "search", "back issues",
    "back-issues", "current issue", "past issues", "table of contents",
    "toc", "login", "register", "announcements", "sitemap",
}


# ── Utilities ─────────────────────────────────────────────────────────────────

def _get(url, **kwargs):
    """GET url, return (response, BeautifulSoup) or (None, None) on failure."""
    try:
        resp = requests.get(url, headers=HEADERS, timeout=TIMEOUT, **kwargs)
        resp.raise_for_status()
        return resp, BeautifulSoup(resp.text, "lxml")
    except Exception as e:
        log.debug("  GET failed: %s — %s", url, e)
        return None, None


def _is_nav_text(text):
    return text.strip().lower() in NAV_WORDS or len(text.strip()) < 8


def _abs_url(href, base):
    if not href:
        return None
    href = href.strip()
    if href.startswith("http"):
        return href
    if href.startswith("/"):
        # strip trailing slash from base before joining
        return base.rstrip("/") + href
    return None


# ── Kairos ────────────────────────────────────────────────────────────────────
# Custom static site. Issues live at https://www.technorhetoric.net/{vol}.{issue}/
# Current issue as of March 2026: 30.2 (Spring 2026).
# Volume numbering: Vol 1 = 1996.  Issue 1 = Fall (year = 1995+vol),
#                                   Issue 2 = Spring (year = 1996+vol).

KAIROS_BASE = "https://www.technorhetoric.net"
KAIROS_CURRENT_VOL = 30
KAIROS_CURRENT_ISS = 2


def _kairos_year(vol, issue):
    return 1995 + vol if issue == 1 else 1996 + vol


def _scrape_kairos_issue(vol, issue, journal_name):
    year = _kairos_year(vol, issue)
    issue_url = f"{KAIROS_BASE}/{vol}.{issue}/index.html"
    _, soup = _get(issue_url)
    if soup is None:
        # Some older issues use a different index filename
        _, soup = _get(f"{KAIROS_BASE}/{vol}.{issue}/")
    if soup is None:
        log.debug("  Kairos %d.%d not found", vol, issue)
        return 0

    seen, new_count = set(), 0
    for a in soup.find_all("a", href=True):
        href = a["href"]
        if SKIP_PATTERNS.search(href):
            continue

        title = a.get_text(separator=" ", strip=True)
        if _is_nav_text(title):
            continue

        url = _abs_url(href, f"{KAIROS_BASE}/{vol}.{issue}")
        if not url or url in seen:
            continue

        # Skip links that are clearly section/navigation pages
        if url.endswith("/") or url.endswith("index.html"):
            continue

        seen.add(url)
        new = upsert_article(
            url=url, doi=None, title=title, authors=None,
            abstract=None, pub_date=str(year),
            journal=journal_name, source="scrape",
            tags=auto_tag(title, None),
        )
        new_count += new

    return new_count


def scrape_kairos():
    name = "Kairos: A Journal of Rhetoric, Technology, and Pedagogy"
    log.info("Scraping: %s", name)
    total = 0

    # Check a window of recent issues — current issue back 4 volumes
    for vol in range(KAIROS_CURRENT_VOL, KAIROS_CURRENT_VOL - 5, -1):
        for iss in (2, 1):
            if vol == KAIROS_CURRENT_VOL and iss > KAIROS_CURRENT_ISS:
                continue
            total += _scrape_kairos_issue(vol, iss, name)

    update_fetch_log(name)
    log.info("  %s — %d new articles", name, total)
    return total


# ── Praxis ────────────────────────────────────────────────────────────────────
# Squarespace. No RSS. Issues listed on /back-issues-1 as "{vol}.{iss} ({year})"
# with an "Articles" link pointing to a "links-page" (e.g. /223-links-page or
# /links-page-132). Article links on each links-page contain the issue number
# as either a URL prefix (/223-slug) or suffix (/slug-132).

PRAXIS_BASE = "https://www.praxisuwc.com"

# Persistent nav hrefs that appear on every Praxis page — never articles.
PRAXIS_NAV_HREFS = {
    "/", "/praxis", "/full-issue", "/back-issues-1", "/vintage-praxis-2",
    "/special-issue-2026", "/policies-1", "/praxis-search", "/new-page-10",
    "/editorial-team", "/review-board", "/axis-blog", "/axis-about",
    "/blog-guidelines",
}


def _praxis_issue_num(links_page_href):
    """Extract the 2–4 digit issue number from a links-page href."""
    # Handles both /NNN-links-page and /links-page-NNN (and variants with -1/-2 suffix)
    m = re.search(r"/(\d{2,4})[_-]links|links[_-]page[_-](\d{2,4})", links_page_href)
    if m:
        return m.group(1) or m.group(2)
    return None


def _scrape_praxis_links_page(links_page_href, pub_year, journal_name):
    """Scrape one Praxis links-page and return count of new articles inserted."""
    url = PRAXIS_BASE + links_page_href
    _, soup = _get(url)
    if soup is None:
        return 0

    issue_num = _praxis_issue_num(links_page_href)
    seen, new_count = set(), 0

    for a in soup.find_all("a", href=True):
        href = a["href"].strip()
        if not href.startswith("/"):
            continue
        # Skip global nav and About-the-Authors pages
        if href in PRAXIS_NAV_HREFS:
            continue
        if "about-the-authors" in href or "about-authors" in href:
            continue
        # If we have an issue number, only accept URLs that contain it
        if issue_num and issue_num not in href:
            continue

        title = a.get_text(separator=" ", strip=True)
        if not title or len(title) < 10:
            continue

        full_url = PRAXIS_BASE + href
        if full_url in seen:
            continue
        seen.add(full_url)

        new = upsert_article(
            url=full_url, doi=None, title=title, authors=None,
            abstract=None, pub_date=pub_year,
            journal=journal_name, source="scrape",
            tags=auto_tag(title, None),
        )
        new_count += new

    return new_count


def scrape_praxis():
    """
    Scrape the full Praxis archive by reading /back-issues-1 for the
    year–links_page mapping, plus the current and special issues.
    """
    name = "Praxis: A Writing Center Journal"
    log.info("Scraping: %s", name)
    total = 0
    issue_pages = []    # [(year_str, href), ...]

    # ── Back issues: walk the DOM tracking the current year next to each link ─
    _, soup = _get(PRAXIS_BASE + "/back-issues-1")
    if soup:
        current_year = None
        for el in soup.find_all(["p", "div", "span", "h1", "h2", "h3", "h4", "li", "a"]):
            text = el.get_text(separator=" ", strip=True)
            m = re.search(r"\b(20\d{2})\b", text)
            if m and re.search(r"\d+\.\d+\s*\(" + m.group(1) + r"\)", text):
                current_year = m.group(1)
            if el.name == "a" and "links-page" in el.get("href", "") and current_year:
                href = el["href"].strip()
                if not href.startswith("/"):
                    href = "/" + href
                issue_pages.append((current_year, href))
                current_year = None

    # ── Current and special issues (not listed on back-issues page) ───────────
    for path in ("/full-issue", "/special-issue-2026"):
        _, soup2 = _get(PRAXIS_BASE + path)
        if soup2:
            # Best-guess year from first 4-digit year in page text
            m_yr = re.search(r"\b(20\d{2})\b", soup2.get_text())
            curr_year = m_yr.group(1) if m_yr else "2026"
            for a in soup2.find_all("a", href=True):
                if "links-page" in a.get("href", ""):
                    href = a["href"].strip()
                    if not href.startswith("/"):
                        href = "/" + href
                    issue_pages.append((curr_year, href))

    # Deduplicate, preserving first-seen order
    seen_hrefs: set = set()
    unique_pages = []
    for yr, href in issue_pages:
        if href not in seen_hrefs:
            seen_hrefs.add(href)
            unique_pages.append((yr, href))

    log.info("  Praxis: found %d issue links-pages", len(unique_pages))

    for year, href in unique_pages:
        total += _scrape_praxis_links_page(href, year, name)

    update_fetch_log(name)
    log.info("  %s — %d new articles", name, total)
    return total


# ── Journal of Multimodal Rhetorics ───────────────────────────────────────────
# Custom Ruby/Rack app. Issue TOC pages at /{vol}-{issue}-issue.
# Article pages at /{vol}-{issue}-authorslug.
# Vol 1 = 2017.

JMR_BASE = "https://journalofmultimodalrhetorics.com"
JMR_CURRENT_VOL = 10   # update as needed


def _jmr_year(vol):
    return 2016 + vol


def _scrape_jmr_issue(issue_url, journal_name):
    _, soup = _get(issue_url)
    if soup is None:
        return 0

    m = re.search(r"/(\d+)-\d+-issue", issue_url)
    pub_date = str(_jmr_year(int(m.group(1)))) if m else None

    seen, new_count = set(), 0

    # Article links are <a class="navigation_page_link"> per the research,
    # but we'll cast a wider net and filter by URL pattern.
    for a in soup.find_all("a", href=True):
        href = a["href"]
        full = _abs_url(href, JMR_BASE)
        if not full:
            continue
        if SKIP_PATTERNS.search(href):
            continue
        # Article URLs look like /{vol}-{issue}-slug — not ending in "-issue"
        if not re.search(r"/\d+-\d+-\w", full):
            continue
        if full.endswith("-issue"):
            continue
        if full in seen:
            continue

        title = a.get_text(separator=" ", strip=True)
        if _is_nav_text(title):
            continue

        seen.add(full)
        new = upsert_article(
            url=full, doi=None, title=title, authors=None,
            abstract=None, pub_date=pub_date,
            journal=journal_name, source="scrape",
            tags=auto_tag(title, None),
        )
        new_count += new

    return new_count


def scrape_jmr():
    name = "Journal of Multimodal Rhetorics"
    log.info("Scraping: %s", name)
    total = 0
    issue_urls = set()

    # Discover issues from the homepage nav
    _, soup = _get(JMR_BASE + "/")
    if soup:
        for a in soup.find_all("a", href=True):
            full = _abs_url(a["href"], JMR_BASE)
            if full and re.search(r"/\d+-\d+-issue$", full):
                issue_urls.add(full)

    # Also probe known recent issues directly
    for vol in range(JMR_CURRENT_VOL, JMR_CURRENT_VOL - 4, -1):
        for iss in (1, 2):
            issue_urls.add(f"{JMR_BASE}/{vol}-{iss}-issue")

    for url in sorted(issue_urls, reverse=True):
        total += _scrape_jmr_issue(url, name)

    update_fetch_log(name)
    log.info("  %s — %d new articles", name, total)
    return total


# ── Basic Writing e-Journal ───────────────────────────────────────────────────
# Static HTML (CUNY). Archive index at /Archives.html.
# Largely dormant since Vol 16.1 (2020) but included for completeness.

BWE_BASE = "https://bwe.ccny.cuny.edu"


def _scrape_bwe_issue(issue_url, journal_name):
    _, soup = _get(issue_url)
    if soup is None:
        return 0

    # Year from URL or page <title>
    pub_date = None
    m = re.search(r"(\d{4})", issue_url)
    if m:
        pub_date = m.group(1)
    elif soup.title:
        m2 = re.search(r"(\d{4})", soup.title.get_text())
        pub_date = m2.group(1) if m2 else None

    seen, new_count = set(), 0

    for tag in soup.find_all(["h2", "h3", "h4", "b", "strong"]):
        text = tag.get_text(separator=" ", strip=True)
        if not text or _is_nav_text(text):
            continue

        a = (tag.find("a")
             or tag.find_next_sibling("a")
             or tag.find_parent("a"))
        if a:
            href = a.get("href", "")
            url = _abs_url(href, BWE_BASE) or (BWE_BASE + "/" + href.lstrip("/"))
        else:
            url = issue_url + "#" + re.sub(r"\W+", "-", text[:30])

        if url in seen:
            continue
        seen.add(url)

        new = upsert_article(
            url=url, doi=None, title=text, authors=None,
            abstract=None, pub_date=pub_date,
            journal=journal_name, source="scrape",
            tags=auto_tag(text, None),
        )
        new_count += new

    return new_count


def scrape_bwe():
    name = "Basic Writing e-Journal"
    log.info("Scraping: %s", name)
    total = 0

    _, soup = _get(BWE_BASE + "/Archives.html")
    if soup is None:
        update_fetch_log(name)
        return 0

    issue_links = []
    for a in soup.find_all("a", href=True):
        href = a["href"]
        if SKIP_PATTERNS.search(href):
            continue
        if re.search(r"(BWe|issue|vol)", href, re.I) and not href.endswith("Archives.html"):
            full = _abs_url(href, BWE_BASE) or (BWE_BASE + "/" + href.lstrip("/"))
            issue_links.append(full)

    for url in issue_links:
        total += _scrape_bwe_issue(url, name)

    update_fetch_log(name)
    log.info("  %s — %d new articles", name, total)
    return total


# ── Writing on the Edge ───────────────────────────────────────────────────────
# Drupal 10 (UC Davis). RSS returns 403. Issues listed at /issues.
# Subscription journal — titles/metadata are public but full text is paywalled.

WOE_BASE = "https://woejournal.ucdavis.edu"


def _scrape_woe_issue(issue_url, journal_name):
    _, soup = _get(issue_url)
    if soup is None:
        return 0

    m = re.search(r"(\d{4})", issue_url)
    pub_date = m.group(1) if m else None

    seen, new_count = set(), 0

    # Drupal article nodes typically live under headings or .views-row containers
    for a in soup.find_all("a", href=True):
        href = a["href"]
        full = _abs_url(href, WOE_BASE)
        if not full:
            continue
        if SKIP_PATTERNS.search(href):
            continue
        # Drupal article nodes: /node/\d+ or /articles/slug
        if not re.search(r"/(node/\d+|articles?/|content/)", full):
            continue
        if full in seen:
            continue

        title = a.get_text(separator=" ", strip=True)
        if _is_nav_text(title):
            continue

        seen.add(full)
        new = upsert_article(
            url=full, doi=None, title=title, authors=None,
            abstract=None, pub_date=pub_date,
            journal=journal_name, source="scrape",
            tags=auto_tag(title, None),
        )
        new_count += new

    return new_count


def scrape_woe():
    name = "Writing on the Edge"
    log.info("Scraping: %s", name)
    total = 0
    issue_urls = set()

    for path in ("/issues", "/back-issues", "/archive"):
        _, soup = _get(WOE_BASE + path)
        if soup is None:
            continue
        for a in soup.find_all("a", href=True):
            full = _abs_url(a["href"], WOE_BASE)
            if not full:
                continue
            if re.search(r"/\d{2,3}[-\.]\d|/volume|/vol-\d|/issue", full, re.I):
                if full not in (WOE_BASE + path,):
                    issue_urls.add(full)
        if issue_urls:
            break

    for url in sorted(issue_urls, reverse=True)[:12]:  # limit to recent issues
        total += _scrape_woe_issue(url, name)

    update_fetch_log(name)
    log.info("  %s — %d new articles", name, total)
    return total


# ── Enculturation ─────────────────────────────────────────────────────────────
# Drupal 7. No OAI, no REST API. Issues listed at /issues as /1, /2, ... /NN.
# Each issue page mixes article links with author profile links (/user/NNNNN).
# Article slugs are short readable paths; author links match /user/\d+.
# Approximate year: journal launched 1996; ~2 issues/year.
# We fetch the issue index to get the true issue list rather than guessing.

ENCULTURATION_BASE = "https://enculturation.net"

# Nav/admin text that is never an article title
ENCULTURATION_SKIP = re.compile(
    r"^(enculturation|submissions?|editorial|about|contact|issue|open issue|"
    r"home|search|log in|register|sitemap|toc|table of contents)\s*$",
    re.I,
)


def _scrape_enculturation_issue(issue_path, pub_date, journal_name):
    """Scrape one Enculturation issue page. Returns count of new articles."""
    url = ENCULTURATION_BASE + issue_path if issue_path.startswith("/") else issue_path
    _, soup = _get(url)
    if soup is None:
        return 0

    seen, new_count = set(), 0
    for a in soup.find_all("a", href=True):
        href = a["href"].strip()
        # Skip author profile links, external links, and nav anchors
        if re.search(r"/user/\d+", href):
            continue
        if href.startswith("#") or "mailto:" in href:
            continue
        if SKIP_PATTERNS.search(href):
            continue

        title = a.get_text(separator=" ", strip=True)
        if not title or _is_nav_text(title) or ENCULTURATION_SKIP.match(title):
            continue
        if len(title) < 12:
            continue

        full = _abs_url(href, ENCULTURATION_BASE)
        if not full or not full.startswith(ENCULTURATION_BASE):
            continue
        # Skip if the URL is just an issue index or root
        path = full.replace(ENCULTURATION_BASE, "")
        if re.fullmatch(r"/\d{1,2}(/index\.html)?/?", path):
            continue

        if full in seen:
            continue
        seen.add(full)

        new = upsert_article(
            url=full, doi=None, title=title, authors=None,
            abstract=None, pub_date=pub_date,
            journal=journal_name, source="scrape",
            tags=auto_tag(title, None),
        )
        new_count += new

    return new_count


def scrape_enculturation():
    name = "Enculturation"
    log.info("Scraping: %s", name)

    # Fetch issue listing to get all issue paths and approximate years
    _, soup = _get(ENCULTURATION_BASE + "/issues")
    if soup is None:
        update_fetch_log(name)
        return 0

    # Collect issue links — paths like /1, /2, /32, /5_1, etc.
    issue_entries = []
    for a in soup.find_all("a", href=True):
        href = a["href"].strip()
        # Match /NN or /NN_NN style paths
        if re.fullmatch(r"/\d{1,2}(_\d)?(/index\.html)?", href):
            issue_entries.append(href)
        elif re.fullmatch(r"https?://enculturation\.net/\d{1,2}(_\d)?(/index\.html)?", href):
            path = re.sub(r"https?://enculturation\.net", "", href)
            issue_entries.append(path)

    # Deduplicate while preserving order
    seen_paths = set()
    unique_issues = []
    for p in issue_entries:
        norm = p.rstrip("/").split("/index.html")[0]
        if norm not in seen_paths:
            seen_paths.add(norm)
            unique_issues.append(p)

    if not unique_issues:
        log.warning("  Enculturation: no issue pages found")
        update_fetch_log(name)
        return 0

    log.info("  Enculturation: found %d issue pages", len(unique_issues))

    total = 0
    for path in unique_issues:
        # Extract issue number to estimate publication year
        m = re.search(r"/(\d+)", path)
        if m:
            n = int(m.group(1))
            # Vol 1 = 1996; ~2 issues per year
            year = 1995 + (n + 1) // 2
            pub_date = str(min(year, 2026))
        else:
            pub_date = None
        total += _scrape_enculturation_issue(path, pub_date, name)

    update_fetch_log(name)
    log.info("  %s — %d new articles", name, total)
    return total


# ── KB Journal ────────────────────────────────────────────────────────────────
# Drupal. RSS only returns ~9 recent items. All issues are listed in the sidebar
# nav of every issue page. Issue URLs follow seasonal patterns: /winter2023,
# /spring2021, /summer2019, etc. (with some older issues at /content/... paths).
# We seed from a known-recent issue to collect the full sidebar link list,
# then scrape each issue for articles.

KB_BASE = "https://kbjournal.org"

# Sidebar links that are issues follow these seasonal patterns
KB_ISSUE_RE = re.compile(
    r"/(winter|spring|summer|fall|autumn)\d{4}$"
    r"|/content/volume-\d+-issue-\d",
    re.I,
)

# Date extraction from issue URL slug
KB_SEASON_MONTHS = {"winter": "01", "spring": "04", "summer": "06", "fall": "09", "autumn": "09"}


def _kb_pub_date(issue_url):
    """Extract YYYY-MM pub date from a KB Journal issue URL, or None."""
    m = re.search(r"/(winter|spring|summer|fall|autumn)(\d{4})", issue_url, re.I)
    if m:
        season, year = m.group(1).lower(), m.group(2)
        month = KB_SEASON_MONTHS.get(season, "01")
        return f"{year}-{month}"
    m2 = re.search(r"(\d{4})", issue_url)
    return m2.group(1) if m2 else None


def _scrape_kb_issue(issue_url, journal_name):
    """Scrape one KB Journal issue page. Returns count of new articles."""
    _, soup = _get(issue_url)
    if soup is None:
        return 0

    pub_date = _kb_pub_date(issue_url)
    seen, new_count = set(), 0

    for a in soup.find_all("a", href=True):
        href = a["href"].strip()
        full = _abs_url(href, KB_BASE)
        if not full:
            continue
        if SKIP_PATTERNS.search(href):
            continue
        # Article URLs are slugs at the root: kbjournal.org/slug
        # Skip obvious non-articles
        path = full.replace("https://kbjournal.org", "").replace("http://kbjournal.org", "").replace("http://www.kbjournal.org", "")
        if not path or "/" not in path or path.count("/") > 2:
            continue
        if re.search(r"/(board|submit|cart|user|node/\d|content/conference|"
                     r"bibliography|spring\d|fall\d|winter\d|summer\d|autumn\d)", path, re.I):
            continue

        title = a.get_text(separator=" ", strip=True)
        if not title or _is_nav_text(title) or len(title) < 15:
            continue

        if full in seen:
            continue
        seen.add(full)

        new = upsert_article(
            url=full, doi=None, title=title, authors=None,
            abstract=None, pub_date=pub_date,
            journal=journal_name, source="scrape",
            tags=auto_tag(title, None),
        )
        new_count += new

    return new_count


def scrape_kb_journal():
    name = "KB Journal: The Journal of the Kenneth Burke Society"
    log.info("Scraping: %s", name)

    # Seed from the most recent issue to collect the full issue list from the sidebar
    seed_url = KB_BASE + "/winter2023"
    _, soup = _get(seed_url)
    if soup is None:
        log.warning("  KB Journal: could not load seed issue page")
        update_fetch_log(name)
        return 0

    # Collect all issue URLs from sidebar/nav
    issue_urls = set()
    for a in soup.find_all("a", href=True):
        href = a["href"].strip()
        full = _abs_url(href, KB_BASE)
        if not full:
            continue
        # Normalise www subdomain
        full = full.replace("http://www.kbjournal.org", KB_BASE).replace("https://www.kbjournal.org", KB_BASE)
        if KB_ISSUE_RE.search(full):
            issue_urls.add(full)
        # Also catch older /node/NNN style issue pages if linked in sidebar
        elif re.search(r"/node/\d{3}", full) and "kbjournal.org" in full:
            issue_urls.add(full)

    # Always include the seed
    issue_urls.add(seed_url)

    log.info("  KB Journal: found %d issue pages", len(issue_urls))

    total = 0
    for url in sorted(issue_urls, reverse=True):
        total += _scrape_kb_issue(url, name)

    update_fetch_log(name)
    log.info("  %s — %d new articles", name, total)
    return total


# ── Composition Studies ───────────────────────────────────────────────────────
# WordPress.com site. Not in CrossRef. Archive page lists issues; HTML issue
# pages (2017+) embed individual article links pointing to PDF files hosted
# at /wp-content/uploads/. Pre-2016 issues exist only as full-issue PDFs.

CS_BASE = "https://compstudiesjournal.com"

# Permanent nav links on every Composition Studies page — never articles
CS_NAV_SLUGS = {
    "/", "/history/history/", "/history/composition-studies-bylaws/",
    "/staff/", "/archive/", "/submissions-2/", "/submissions-2/course-designs/",
    "/submissions-2/book-reviews/", "/author-guidelines/", "/submissions-2/how-to-submit/",
    "/submissions-2/cfps/", "/subscriptions/", "/fen-blog-meet-the-editors/",
    "/fen-blog-submission-guidelines/", "/fen-blog-community-guidelines/",
    "/review-guidelines/", "/contact-advertising/", "/a-guide-for-anti-racist-scholarly-reviewing-practices-at-composition-studies/",
    "/covers/", "/praxis/", "/blog/",
}

# Partial titles that indicate supplementary materials, not standalone articles
CS_SUPPLEMENT_RE = re.compile(
    r"^\s*(syllabus|appendix|supplement|course design|full issue|"
    r"table of contents|toc|editors?['\u2019]?\s*intro|back cover)\b",
    re.I,
)


def _scrape_cs_issue(issue_url, pub_year, journal_name):
    """Scrape one Composition Studies issue page. Articles link to PDFs."""
    _, soup = _get(issue_url)
    if soup is None:
        return 0

    seen, new_count = set(), 0

    for a in soup.find_all("a", href=True):
        href = a["href"].strip()
        title = a.get_text(separator=" ", strip=True)

        # Only accept PDF links under wp-content (individual article PDFs)
        if "/wp-content/uploads/" not in href or not href.lower().endswith(".pdf"):
            continue
        if len(title) < 15:
            continue
        if CS_SUPPLEMENT_RE.match(title):
            continue

        url = href if href.startswith("http") else CS_BASE + href
        if url in seen:
            continue
        seen.add(url)

        new = upsert_article(
            url=url, doi=None, title=title, authors=None,
            abstract=None, pub_date=pub_year,
            journal=journal_name, source="scrape",
            tags=auto_tag(title, None),
        )
        new_count += new

    return new_count


def scrape_comp_studies():
    """
    Scrape Composition Studies from compstudiesjournal.com.
    Reads /archive/ to discover issue pages, then visits each HTML issue page
    to extract individual article PDF links. Pre-2016 issues (full-PDF only)
    are skipped automatically since they have no /wp-content/uploads/ article links.
    """
    name = "Composition Studies"
    log.info("Scraping: %s", name)
    total = 0

    _, soup = _get(CS_BASE + "/archive/")
    if soup is None:
        update_fetch_log(name)
        return 0

    # Collect (year, issue_url) for HTML issue pages
    issue_pages = []
    for a in soup.find_all("a", href=True):
        text = a.get_text(strip=True)
        href = a["href"].strip()

        # Match issue link text like "Fall 2025 53.2" or "Spring 2022 50.1"
        if not re.search(r"\d{2}\.\d", text):
            continue
        if ".pdf" in href.lower():
            continue                         # skip full-issue PDFs
        if "compstudiesjournal.com" not in href:
            continue                         # skip external redirects

        # Extract year from link text
        m = re.search(r"\b(20\d{2}|19\d{2})\b", text)
        year = m.group(1) if m else None

        full = href if href.startswith("http") else CS_BASE + href
        issue_pages.append((year or "unknown", full))

    # Deduplicate by URL
    seen_urls: set = set()
    unique = []
    for yr, url in issue_pages:
        if url not in seen_urls:
            seen_urls.add(url)
            unique.append((yr, url))

    log.info("  Composition Studies: found %d HTML issue pages", len(unique))

    for year, url in unique:
        total += _scrape_cs_issue(url, year, name)

    update_fetch_log(name)
    log.info("  %s — %d new articles", name, total)
    return total


# ── Writing Lab Newsletter ─────────────────────────────────────────────────────
# Print newsletter (1975–2015). Archive at /resources.html as full-issue PDFs.
# No individual article pages exist; each PDF is one issue of the newsletter.

WLN_BASE = "https://writinglabnewsletter.org"


def scrape_wln():
    name = "Writing Lab Newsletter"
    log.info("Scraping: %s", name)
    total = 0

    _, soup = _get(WLN_BASE + "/resources.html")
    if soup is None:
        update_fetch_log(name)
        return 0

    seen = set()
    for a in soup.find_all("a", href=True):
        href = a["href"].strip()
        if "/archives/" not in href or not href.lower().endswith(".pdf"):
            continue

        url = _abs_url(href, WLN_BASE)
        if not url or url in seen:
            continue
        seen.add(url)

        # Estimate year from volume number: Vol 1 = 1975–76, Vol N ≈ 1974+N
        m_vol = re.search(r"/v(\d+)/", href)
        pub_date = str(1974 + int(m_vol.group(1))) if m_vol else None

        link_text = a.get_text(strip=True)
        fname = href.split("/")[-1].replace(".pdf", "")
        title = f"WLN {link_text}" if link_text and len(link_text) >= 4 else f"WLN Issue {fname}"

        total += upsert_article(
            url=url, doi=None, title=title, authors=None,
            abstract=None, pub_date=pub_date,
            journal=name, source="scrape",
            tags=auto_tag(title, None),
        )

    update_fetch_log(name)
    log.info("  %s — %d new articles", name, total)
    return total


# ── Writing Center Journal ─────────────────────────────────────────────────────
# Purdue Digital Commons (Open Access). Issue TOC pages list articles with
# title, author(s), and URL. Individual article pages at /wcj/vol{N}/iss{N}/{N}.
# Vol 1 ≈ 1980; currently at Vol 43 (2025).

WCJ_BASE = "https://docs.lib.purdue.edu"


def _scrape_wcj_issue(issue_url, journal_name):
    """Scrape one Writing Center Journal issue TOC page."""
    _, soup = _get(issue_url)
    if soup is None:
        return 0

    # Try to extract year from page text
    page_text = soup.get_text(" ", strip=True)
    m_yr = re.search(r"\b(19[89]\d|200\d|201\d|202\d)\b", page_text)
    pub_date = m_yr.group(1) if m_yr else None
    if not pub_date:
        m_vol = re.search(r"/vol(\d+)/", issue_url)
        if m_vol:
            pub_date = str(1979 + int(m_vol.group(1)))

    seen, new_count = set(), 0
    for a in soup.find_all("a", href=True):
        href = a["href"]
        if not re.search(r"/wcj/vol\d+/iss\d+/\d+$", href):
            continue
        full = _abs_url(href, WCJ_BASE)
        if not full or full in seen:
            continue

        title = a.get_text(separator=" ", strip=True)
        if not title or len(title) < 10:
            continue

        # Try to extract author from parent container
        authors = None
        parent = a.find_parent(["li", "div", "p", "td"])
        if parent:
            parent_text = parent.get_text(" ", strip=True)
            author_text = parent_text.replace(title, "").strip()
            author_text = re.sub(r"^[,;\s\-]+|[,;\s\-]+$", "", author_text)
            if author_text and len(author_text) < 200:
                authors = "; ".join(
                    p.strip() for p in re.split(r"\s+and\s+", author_text) if p.strip()
                )

        seen.add(full)
        new_count += upsert_article(
            url=full, doi=None, title=title, authors=authors,
            abstract=None, pub_date=pub_date,
            journal=journal_name, source="scrape",
            tags=auto_tag(title, None),
        )

    return new_count


def scrape_wcj():
    name = "Writing Center Journal"
    log.info("Scraping: %s", name)
    total = 0

    _, soup = _get(WCJ_BASE + "/wcj/")
    if soup is None:
        update_fetch_log(name)
        return 0

    issue_urls = set()
    for a in soup.find_all("a", href=True):
        href = a["href"]
        if re.search(r"/wcj/vol\d+/iss\d+/?$", href):
            full = _abs_url(href, WCJ_BASE)
            if full:
                issue_urls.add(full.rstrip("/") + "/")

    # Also probe recent volumes directly
    for vol in range(43, 38, -1):
        for iss in range(1, 4):
            issue_urls.add(f"{WCJ_BASE}/wcj/vol{vol}/iss{iss}/")

    log.info("  Writing Center Journal: checking %d issue URLs", len(issue_urls))

    for url in sorted(issue_urls, reverse=True):
        total += _scrape_wcj_issue(url, name)

    update_fetch_log(name)
    log.info("  %s — %d new articles", name, total)
    return total


# ── The Peer Review ────────────────────────────────────────────────────────────
# IWCA WordPress site. Issues listed at /issues/. Issue pages at /issue-N[-M]/.
# Individual articles at root-level slugs: thepeerreview-iwca.org/article-slug/

PEER_REVIEW_BASE = "https://thepeerreview-iwca.org"

PEER_REVIEW_SKIP = re.compile(
    r"^(issues?|about|contact|submit|editorial|board|masthead|"
    r"wp-content|wp-admin|feed|tag|category|author|page|#)",
    re.I,
)


def _scrape_peer_review_issue(issue_url, pub_year, journal_name):
    """Scrape one Peer Review issue page."""
    _, soup = _get(issue_url)
    if soup is None:
        return 0

    seen, new_count = set(), 0
    for a in soup.find_all("a", href=True):
        href = a["href"].strip()
        full = _abs_url(href, PEER_REVIEW_BASE)
        if not full or not full.startswith(PEER_REVIEW_BASE + "/"):
            continue

        path = full.replace(PEER_REVIEW_BASE, "").strip("/")
        if not path or "/" in path:
            continue
        if PEER_REVIEW_SKIP.match(path):
            continue
        if full == issue_url or full == PEER_REVIEW_BASE + "/":
            continue

        title = a.get_text(separator=" ", strip=True)
        if not title or len(title) < 10 or _is_nav_text(title):
            continue

        if full in seen:
            continue
        seen.add(full)

        # Try to extract author from parent text
        authors = None
        parent = a.find_parent(["li", "div", "p", "article"])
        if parent:
            parent_text = parent.get_text(" ", strip=True)
            author_text = parent_text.replace(title, "").strip()
            author_text = re.sub(r"^[\s\-\u2013\u2014,]+|[\s\-\u2013\u2014,]+$", "", author_text)
            if author_text and len(author_text) < 150:
                authors = author_text

        new_count += upsert_article(
            url=full, doi=None, title=title, authors=authors,
            abstract=None, pub_date=pub_year,
            journal=journal_name, source="scrape",
            tags=auto_tag(title, None),
        )

    return new_count


def scrape_peer_review():
    name = "The Peer Review"
    log.info("Scraping: %s", name)
    total = 0

    _, soup = _get(PEER_REVIEW_BASE + "/issues/")
    if soup is None:
        update_fetch_log(name)
        return 0

    issue_pages = []
    for a in soup.find_all("a", href=True):
        href = a["href"].strip()
        if not re.search(r"/issue-\d", href):
            continue
        full = _abs_url(href, PEER_REVIEW_BASE)
        if not full:
            continue
        parent = a.find_parent(["li", "div", "p", "h2", "h3"])
        ctx = parent.get_text(" ", strip=True) if parent else a.get_text(" ", strip=True)
        m_yr = re.search(r"\b(20\d{2})\b", ctx)
        year = m_yr.group(1) if m_yr else None
        issue_pages.append((year, full))

    seen_urls: set = set()
    unique = []
    for yr, url in issue_pages:
        if url not in seen_urls:
            seen_urls.add(url)
            unique.append((yr, url))

    log.info("  The Peer Review: found %d issue pages", len(unique))

    for year, issue_url in unique:
        total += _scrape_peer_review_issue(issue_url, year, name)

    update_fetch_log(name)
    log.info("  %s — %d new articles", name, total)
    return total


# ── Dispatch ──────────────────────────────────────────────────────────────────

SCRAPERS = {
    "kairos":        scrape_kairos,
    "praxis":        scrape_praxis,
    "jmr":           scrape_jmr,
    "bwe":           scrape_bwe,
    "woe":           scrape_woe,
    "comp_studies":  scrape_comp_studies,
    "enculturation": scrape_enculturation,
    "kb_journal":    scrape_kb_journal,
    "wln":           scrape_wln,
    "wcj":           scrape_wcj,
    "peer_review":   scrape_peer_review,
}


def fetch_all():
    """
    Run all scrapers — covers SCRAPE_JOURNALS plus any RSS_JOURNALS entry
    that defines a `strategy` key (e.g. Enculturation, KB Journal).
    Returns total new article count.
    """
    init_db()
    total = 0

    # Merge both lists, deduplicating by strategy key
    all_journals = list(SCRAPE_JOURNALS) + [j for j in RSS_JOURNALS if j.get("strategy")]

    for journal in all_journals:
        strategy = journal.get("strategy")
        fn = SCRAPERS.get(strategy)
        if fn:
            try:
                total += fn()
            except Exception as e:
                log.error("Scraper error [%s]: %s", journal["name"], e)
        else:
            log.warning("No scraper registered for strategy: %s", strategy)
    log.info("Scrape fetch complete. Total new: %d", total)
    return total


if __name__ == "__main__":
    fetch_all()
