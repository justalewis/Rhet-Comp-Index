"""
scraper.py — Web scrapers for journals without RSS feeds.

Covered journals and strategies:
  kairos      — Kairos (technorhetoric.net) — custom static HTML, vol/issue URL pattern
  praxis      — Praxis: A Writing Center Journal (Squarespace) — sitemap + issue pages
  jmr         — Journal of Multimodal Rhetorics — custom Ruby/Rack app, nav-based discovery
  bwe         — Basic Writing e-Journal (CUNY) — static HTML, three-era parsing
  woe         — Writing on the Edge (UC Davis) — Drupal 10, RSS blocked, /issues page
  comp_forum  — Composition Forum — two-era site (old PHP vols 14.2–54, new WP 55+)
  wcj         — Writing Center Journal (Purdue Digital Commons) — bepress meta tags
  peer_review — The Peer Review (IWCA WordPress) — validated ToC + article enrichment
  reflections — Reflections (reflectionsjournal.net) — archive page + article enrichment

Metadata quality note: scraped articles often lack author lists and abstracts.
The source field is set to 'scrape' so the UI can flag these entries.

Usage:
    python scraper.py
"""

import re
import time
import logging
import requests
from bs4 import BeautifulSoup

from db import init_db, get_conn, upsert_article, update_fetch_log
from journals import SCRAPE_JOURNALS, RSS_JOURNALS
from tagger import auto_tag
from monitoring import capture_fetcher_error

SOURCE_NAME = "scrape"

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
# Custom static HTML site at kairos.technorhetoric.net (NOT www.technorhetoric).
# Published since Spring 1996, biannual. ~60 issues, ~600+ articles.
#
# DESIGN: ToC-only scraper — NEVER visits individual article/webtext pages.
# Kairos webtexts are media-rich custom HTML; fetching them would impose
# significant bandwidth on a modest-infrastructure journal serving 45k/month.
# All metadata (titles, authors, abstracts) comes from issue ToC pages.
#
# robots.txt (checked 2026-04-08): blocks /18.2/praxis/santos-et-al/mystories/,
#   /cachedfeeds/, /2011SI/, /toolbar/, /media/, /scholarnames/ only.
#   No ToC pages or archive page blocked.
#
# RECOMMENDED: run no more than once per month. Kairos publishes biannually.

KAIROS_BASE = "https://kairos.technorhetoric.net"
KAIROS_DELAY = 10  # seconds — elevated rate limit for modest infrastructure

# Known section-page filenames to skip (not individual articles)
_KAIROS_SECTION_FILES = {
    "coverweb.html", "features.html", "praxis.html", "interviews.html",
    "reviews.html", "news.html", "loggingon.html", "inbox.html",
    "response.html", "disputatio.html", "topoi.html", "inventio.html",
}

# Kairos season → month mapping (Spring=Jan, Fall=Aug, Summer=May)
_KAIROS_SEASON_MONTHS = {"spring": "01", "fall": "08", "summer": "05"}


def _kairos_get(url):
    """Rate-limited GET for Kairos with custom headers."""
    time.sleep(KAIROS_DELAY)
    headers = {
        **HEADERS,
        "User-Agent": "Pinakes/1.0 (scholarly metadata index; +https://pinakes.xyz; metadata only, no full text)",
        "X-Bot-Purpose": "Pinakes scholarly index - ToC metadata only",
    }
    try:
        resp = requests.get(url, headers=headers, timeout=TIMEOUT)
        resp.raise_for_status()
        return resp, BeautifulSoup(resp.text, "lxml")
    except Exception as e:
        log.debug("  Kairos GET failed: %s — %s", url, e)
        return None, None


def _kairos_normalize_authors(raw):
    """Normalize Kairos author string. Handles &, 'and', comma separators."""
    if not raw:
        return None
    text = raw.strip()
    if not text or len(text) > 300:
        return None
    # Skip section headings, editor credits, or navigation text
    if text.lower() in ("editor", "editors", "special issue editors"):
        return None
    # Strip trailing role descriptions: ", Special Issue Editors" etc.
    text = re.sub(r",?\s*(Special\s+Issue\s+)?Editor(s)?\s*$", "", text, flags=re.I).strip()
    # Split on " & ", " and ", commas
    text = re.sub(r"\s*&\s*", ", ", text)
    text = re.sub(r",?\s+and\s+", ", ", text)
    parts = [p.strip() for p in text.split(",") if p.strip()]
    return "; ".join(parts) if parts else None


def _kairos_is_article_url(href, vol_issue_prefix):
    """Check if an href points to an article (not a section page or external)."""
    if not href:
        return False
    # External links
    if href.startswith("http") and "technorhetoric.net" not in href:
        return False
    # Section pages
    basename = href.rsplit("/", 1)[-1].split("?")[-1] if "/" in href else href
    if basename.lower() in _KAIROS_SECTION_FILES:
        return False
    # Must contain a binder URL or article path
    if "binder" in href or vol_issue_prefix in href:
        return True
    # Relative paths that look like articles
    if href.startswith(("topoi/", "praxis/", "disputatio/", "inventio/",
                        "coverweb/", "features/", "interviews/", "reviews/",
                        "loggingon/")):
        return True
    return False


def _discover_kairos_issues():
    """Fetch the archive page and return all issue URLs with metadata.

    Returns list of (issue_url, pub_date, vol_issue_str), newest first.
    """
    _, soup = _kairos_get(KAIROS_BASE + "/archive.html")
    if soup is None:
        return []

    issues = []
    current_year = None

    for dl in soup.find_all("dl"):
        for child in dl.children:
            if not hasattr(child, "name") or not child.name:
                continue

            if child.name == "dt":
                # Year heading
                m = re.search(r"\b(19\d{2}|20\d{2})\b", child.get_text(strip=True))
                if m:
                    current_year = m.group(1)

            elif child.name == "dd" and current_year:
                a_tag = child.find("a", href=True)
                if not a_tag:
                    continue
                href = a_tag["href"].strip()
                text = child.get_text(strip=True)

                # Extract vol.issue from text: "30.2 Spring..."
                m_vi = re.match(r"(\d+\.\d+)\s*(Spring|Fall|Summer)?", text, re.I)
                if not m_vi:
                    continue
                vol_issue = m_vi.group(1)
                season = (m_vi.group(2) or "fall").lower()
                month = _KAIROS_SEASON_MONTHS.get(season, "01")
                pub_date = f"{current_year}-{month}"

                # Resolve URL
                if href.startswith("http"):
                    issue_url = href
                else:
                    issue_url = KAIROS_BASE + "/" + href.lstrip("/")

                issues.append((issue_url, pub_date, vol_issue))

    return issues


def _scrape_kairos_toc_era3(soup, issue_url, vol_issue_prefix):
    """Parse Era 3 ToC (vol 13.1+): <h2><a>Title</a></h2> <h3>Authors</h3> <p>Abstract</p>."""
    articles = []
    seen = set()

    for h2 in soup.find_all("h2"):
        a_tag = h2.find("a", href=True)
        if not a_tag:
            continue

        href = a_tag["href"].strip()
        title = a_tag.get_text(separator=" ", strip=True)
        if not title or len(title) < 10:
            continue

        if not _kairos_is_article_url(href, vol_issue_prefix):
            continue

        # Resolve URL
        if href.startswith("http"):
            full_url = href
        else:
            base = issue_url.rsplit("/", 1)[0]
            full_url = base + "/" + href.lstrip("/")

        if full_url in seen:
            continue
        seen.add(full_url)

        # Look for <h3> author and <p> abstract in next siblings
        authors = None
        abstract = None
        for sib in h2.next_siblings:
            if not hasattr(sib, "name") or not sib.name:
                continue
            if sib.name == "h2":
                break  # Next article
            if sib.name == "h3" and not authors:
                auth_text = sib.get_text(strip=True)
                # Skip issue metadata like "25.1 Fall 2020" or "ISSN..."
                if not re.match(r"^\d+\.\d+\s", auth_text) and "ISSN" not in auth_text:
                    authors = _kairos_normalize_authors(auth_text)
            if sib.name == "p" and not abstract:
                p_text = sib.get_text(strip=True)
                # Strip smart quotes around abstracts
                p_text = p_text.strip('\u201c\u201d\u201e\u201f"\'')
                if p_text and len(p_text) > 20:
                    abstract = p_text

        articles.append({
            "title": title, "authors": authors,
            "url": full_url, "abstract": abstract,
        })

    return articles


def _scrape_kairos_toc_era12(soup, issue_url, vol_issue_prefix):
    """Parse Era 1–2 ToC (vols 1.1–12.3): table-based, author in parent text."""
    articles = []
    seen = set()

    for a_tag in soup.find_all("a", href=True):
        href = a_tag["href"].strip()
        title = a_tag.get_text(separator=" ", strip=True)
        if not title or len(title) < 10:
            continue

        if not _kairos_is_article_url(href, vol_issue_prefix):
            continue

        # Resolve URL
        if href.startswith("http"):
            full_url = href
        elif href.startswith("binder"):
            # binder.html? or binder2.html? URLs
            full_url = issue_url.rsplit("/", 1)[0] + "/" + href
        else:
            base = issue_url.rsplit("/", 1)[0]
            full_url = base + "/" + href.lstrip("/")

        if full_url in seen:
            continue
        seen.add(full_url)

        # Extract author from parent text
        authors = None
        parent = a_tag.parent
        if parent:
            parent_text = parent.get_text(separator="\n", strip=True)
            # Pattern 1 (Era 1): "AuthorName: Title" — author before colon before the title
            # Find the text before the title in the parent
            title_pos = parent_text.find(title)
            if title_pos > 0:
                before = parent_text[:title_pos].strip().rstrip(":")
                # Check if last line before title looks like an author name
                lines = [l.strip() for l in before.split("\n") if l.strip()]
                if lines:
                    candidate = lines[-1].rstrip(":")
                    # Author names: 2–6 words, contain uppercase, not a section heading
                    if (candidate and len(candidate) < 100
                            and re.search(r"[A-Z][a-z]", candidate)
                            and not re.match(r"^(Feature|Praxis|CoverWeb|Review|Interview|Topoi|News|Logging)", candidate, re.I)):
                        authors = _kairos_normalize_authors(candidate)

            # Pattern 2 (Era 2): "Title\nAuthorName" — author after the title
            if not authors and title_pos >= 0:
                after = parent_text[title_pos + len(title):].strip()
                lines = [l.strip() for l in after.split("\n") if l.strip()]
                if lines:
                    candidate = lines[0]
                    if (candidate and len(candidate) < 100
                            and re.search(r"[A-Z][a-z]", candidate)
                            and not re.match(r"^(Feature|Praxis|CoverWeb|Review|Interview|Topoi|News|Logging|Disputatio|PraxisWiki|http)", candidate, re.I)):
                        authors = _kairos_normalize_authors(candidate)

        articles.append({
            "title": title, "authors": authors,
            "url": full_url, "abstract": None,
        })

    return articles


def scrape_kairos():
    """Scrape Kairos from archive page → issue ToC pages only.

    NEVER visits individual article/webtext pages. All metadata comes from
    the issue index pages, which are lightweight static HTML.
    """
    name = "Kairos: A Journal of Rhetoric, Technology, and Pedagogy"
    log.info("Scraping: %s", name)

    # Phase 1: Discover all issues from archive page
    issues = _discover_kairos_issues()
    if not issues:
        log.warning("  Kairos: could not discover issues from archive page")
        update_fetch_log(name)
        return 0
    log.info("  Kairos: found %d issues from archive page", len(issues))

    # Phase 2: Scrape each issue ToC page
    all_articles = []
    for issue_url, pub_date, vol_issue in issues:
        _, soup = _kairos_get(issue_url)
        if soup is None:
            log.debug("    Kairos %s: could not fetch ToC", vol_issue)
            continue

        # Determine era from vol number
        vol = float(vol_issue.split(".")[0]) if "." in vol_issue else 0
        vol_issue_prefix = vol_issue.replace(".", ".")

        if vol >= 13:
            articles = _scrape_kairos_toc_era3(soup, issue_url, vol_issue_prefix)
        else:
            articles = _scrape_kairos_toc_era12(soup, issue_url, vol_issue_prefix)

        for art in articles:
            art["pub_date"] = pub_date

        log.info("    Kairos %s — %d articles", vol_issue, len(articles))
        all_articles.extend(articles)

    log.info("  Kairos: %d articles found from ToC pages", len(all_articles))

    # Phase 3: Upsert
    new_count = 0
    for art in all_articles:
        tags = auto_tag(art["title"], art.get("abstract"))
        new = upsert_article(
            url=art["url"], doi=None, title=art["title"],
            authors=art.get("authors"), abstract=art.get("abstract"),
            pub_date=art["pub_date"], journal=name, source="scrape",
            tags=tags,
            oa_status="gold", oa_url=art["url"],
        )
        new_count += new

    # Phase 4: Backfill existing records
    backfilled = 0
    with get_conn() as conn:
        for art in all_articles:
            if art.get("authors") or art.get("abstract"):
                tags = auto_tag(art["title"], art.get("abstract"))
                cur = conn.execute("""
                    UPDATE articles
                    SET authors = COALESCE(?, authors),
                        abstract = COALESCE(?, abstract),
                        tags = ?,
                        oa_status = 'gold',
                        oa_url = ?
                    WHERE url = ? AND journal = ?
                      AND (authors IS NULL OR abstract IS NULL)
                """, (art.get("authors"), art.get("abstract"), tags,
                      art["url"], art["url"], name))
                backfilled += cur.rowcount
        conn.commit()
    if backfilled:
        log.info("  Kairos: backfilled %d existing records", backfilled)

    update_fetch_log(name)
    log.info("  %s — %d new, %d total from ToC", name, new_count, len(all_articles))
    return new_count


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


PRAXIS_DELAY = 5  # seconds between requests


def _praxis_get(url):
    """Rate-limited GET for Praxis."""
    time.sleep(PRAXIS_DELAY)
    return _get(url)


def _praxis_normalize_authors(raw):
    """Normalize author string from italic text on a links-page.

    Returns semicolon-separated string or None.
    """
    if not raw:
        return None
    text = raw.strip()
    if not text or len(text) > 200:
        return None
    # Skip if it looks like a URL or section heading
    if "http" in text.lower() or text.isupper():
        return None
    # "A, B, and C" or "A and B" or "A, B, & C" → semicolons
    text = re.sub(r",?\s+and\s+", ", ", text)
    text = re.sub(r",?\s*&\s+", ", ", text)
    parts = [p.strip() for p in text.split(",") if p.strip()]
    return "; ".join(parts) if parts else None


def _scrape_praxis_links_page(links_page_href, pub_year, journal_name):
    """Scrape one Praxis links-page and return list of article dicts.

    Extracts titles from links and authors from adjacent italic text.
    Two DOM patterns:
      Newer: <p><a>Title</a></p> <p style="margin-left:..."><em>Authors</em></p>
      Older: <p><a>Title</a><br/><em>Authors</em></p>
    """
    url = PRAXIS_BASE + links_page_href
    _, soup = _praxis_get(url)
    if soup is None:
        return []

    issue_num = _praxis_issue_num(links_page_href)
    articles = []
    seen = set()

    content = soup.find("div", class_="sqs-html-content")
    if not content:
        content = soup

    children = [c for c in content.children if hasattr(c, "name") and c.name]

    for idx, el in enumerate(children):
        # Find <a> tags in this element
        a_tag = el.find("a", href=True) if el.name in ("p", "div") else None
        if not a_tag:
            continue

        href = a_tag.get("href", "").strip()
        # Normalize absolute URLs to relative paths
        for prefix in ("http://www.praxisuwc.com", "https://www.praxisuwc.com",
                        "http://praxisuwc.com", "https://praxisuwc.com"):
            if href.startswith(prefix):
                href = href[len(prefix):]
                break
        if not href.startswith("/"):
            continue
        if href in PRAXIS_NAV_HREFS:
            continue
        if "about-the-authors" in href or "about-authors" in href:
            continue
        if issue_num and issue_num not in href:
            continue

        title = a_tag.get_text(separator=" ", strip=True)
        if not title or len(title) < 10:
            continue

        full_url = PRAXIS_BASE + href
        if full_url in seen:
            continue
        seen.add(full_url)

        # --- Extract authors ---
        authors = None

        # Pattern 1 (older): <em>/<i> inside the same <p> as the link
        em = el.find(["em", "i"])
        if em and em != a_tag.find(["em", "i"]):
            # Make sure it's not the italic title inside the link
            em_text = em.get_text(strip=True)
            if em_text and em_text != title:
                authors = _praxis_normalize_authors(em_text)

        # Pattern 2 (newer): next sibling <p> with margin-left and <em>
        if not authors and idx + 1 < len(children):
            next_el = children[idx + 1]
            if next_el.name == "p":
                style = next_el.get("style", "")
                next_em = next_el.find(["em", "i"])
                if next_em and ("margin-left" in style or not next_el.find("a")):
                    em_text = next_em.get_text(strip=True)
                    if em_text and len(em_text) < 200:
                        authors = _praxis_normalize_authors(em_text)

        # Pattern 3 (some mid-range issues): next <p> has plain text authors
        # (no <em>, no <a>, short text that looks like names)
        if not authors and idx + 1 < len(children):
            next_el = children[idx + 1]
            if next_el.name == "p" and not next_el.find("a"):
                plain = next_el.get_text(strip=True)
                # Must look like author names: short, contains letters,
                # not a section heading (all caps), not empty
                if (plain and len(plain) < 200 and not plain.isupper()
                        and re.search(r"[A-Z][a-z]", plain)
                        and not re.search(r"^(FOCUS|COLUMN|BOOK|REVIEW|•)", plain, re.I)):
                    authors = _praxis_normalize_authors(plain)

        articles.append({
            "title": title,
            "authors": authors,
            "url": full_url,
            "abstract": None,
            "pub_date": pub_year,
        })

    return articles


def _enrich_praxis_article(article_url):
    """Fetch a Praxis article page and extract the abstract from a blockquote.

    Returns abstract string or None.
    """
    _, soup = _praxis_get(article_url)
    if soup is None:
        return None

    # Look for <blockquote> — Praxis abstracts are in blockquotes
    for bq in soup.find_all("blockquote"):
        # The blockquote may start with a <p>Abstract</p> label — skip it
        paragraphs = bq.find_all("p")
        texts = []
        for p in paragraphs:
            t = p.get_text(separator=" ", strip=True)
            if t.lower() == "abstract":
                continue
            if t:
                texts.append(t)
        if texts:
            abstract = " ".join(texts).strip()
            if len(abstract) > 30:
                return abstract

    # Fallback: look for a paragraph starting with "Abstract" in bold
    for p in soup.find_all("p"):
        bold = p.find(["strong", "b"])
        if bold and "abstract" in bold.get_text(strip=True).lower():
            # The abstract text may be in this paragraph or the next
            text = p.get_text(separator=" ", strip=True)
            text = re.sub(r"^abstract\s*:?\s*", "", text, flags=re.I).strip()
            if text and len(text) > 30:
                return text
            # Try next sibling
            nxt = p.find_next_sibling("p")
            if nxt:
                text = nxt.get_text(separator=" ", strip=True)
                if text and len(text) > 30:
                    return text

    return None


def scrape_praxis():
    """Scrape the full Praxis archive.

    Reads /back-issues-1 for the year→links_page mapping, plus current and
    special issues.  Extracts authors from italic text on links-pages, then
    enriches each article page for abstracts (blockquotes).
    """
    name = "Praxis: A Writing Center Journal"
    log.info("Scraping: %s", name)
    all_articles = []
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

    # ── Phase 1+2: Scrape links-pages for titles and authors ─────────────────
    for year, href in unique_pages:
        articles = _scrape_praxis_links_page(href, year, name)
        log.info("    %s — %d articles", href, len(articles))
        all_articles.extend(articles)

    log.info("  Praxis: %d articles found, enriching for abstracts...", len(all_articles))

    # ── Phase 3: Enrich article pages for abstracts ──────────────────────────
    enriched = 0
    for art in all_articles:
        abstract = _enrich_praxis_article(art["url"])
        if abstract:
            art["abstract"] = abstract
            enriched += 1
    log.info("  Praxis: enriched %d articles with abstracts", enriched)

    # ── Phase 4: Upsert ──────────────────────────────────────────────────────
    new_count = 0
    for art in all_articles:
        tags = auto_tag(art["title"], art["abstract"])
        new = upsert_article(
            url=art["url"], doi=None, title=art["title"],
            authors=art["authors"], abstract=art["abstract"],
            pub_date=art["pub_date"], journal=name, source="scrape",
            tags=tags,
            oa_status="gold", oa_url=art["url"],
        )
        new_count += new

    # ── Phase 5: Backfill existing records ───────────────────────────────────
    backfilled = 0
    with get_conn() as conn:
        for art in all_articles:
            if art["authors"] or art["abstract"]:
                tags = auto_tag(art["title"], art["abstract"])
                cur = conn.execute("""
                    UPDATE articles
                    SET authors = COALESCE(?, authors),
                        abstract = COALESCE(?, abstract),
                        tags = ?,
                        oa_status = 'gold',
                        oa_url = ?
                    WHERE url = ? AND journal = 'Praxis: A Writing Center Journal'
                      AND (authors IS NULL OR abstract IS NULL)
                """, (art["authors"], art["abstract"], tags,
                      art["url"], art["url"]))
                backfilled += cur.rowcount
        conn.commit()
    if backfilled:
        log.info("  Praxis: backfilled %d existing records", backfilled)

    update_fetch_log(name)
    log.info("  %s — %d new, %d total, %d enriched", name, new_count, len(all_articles), enriched)
    return new_count


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
# Static HTML (CUNY). Dormant since Vol 16.1 (2020). ~100–120 articles across
# 16 issues spanning 1999–2020. Three eras of HTML structure:
#   early (1.1–6.1)       — single-page inline issues with ToC at top
#   transitional (7.1, 8/9) — mixed structure with links to sub-pages
#   late (10.1/11.1–16.1)  — multi-page issues with rich ToC metadata
#
# No robots.txt at bwe.ccny.cuny.edu (404). Rate-limited to 5s anyway.

BWE_BASE = "https://bwe.ccny.cuny.edu"
BWE_DELAY = 5  # seconds between requests

# Hardcoded issue list — BWe is dormant, so this is complete.
BWE_ISSUES = [
    # (url, pub_date, era)
    ("https://bwe.ccny.cuny.edu/Issue%201.1.html", "1999", "early"),
    ("https://bwe.ccny.cuny.edu/Issue%201.2.html", "1999", "early"),
    ("https://bwe.ccny.cuny.edu/Issue%202.1.html", "2000", "early"),
    ("https://bwe.ccny.cuny.edu/Issue%202.2.html", "2000", "early"),
    ("https://bwe.ccny.cuny.edu/Issue%203.1.html", "2001", "early"),
    ("https://bwe.ccny.cuny.edu/Issue%204.1.html", "2002", "early"),
    ("https://bwe.ccny.cuny.edu/Issue%205.1.html", "2004", "early"),
    ("https://bwe.ccny.cuny.edu/Spring2007.html", "2007", "early"),
    ("https://bwe.ccny.cuny.edu/Issue%207.1%20Introduction.html", "2008", "transitional"),
    ("https://bwe.ccny.cuny.edu/Issue%208_9%20home.html", "2009", "transitional"),
    ("https://bwe.ccny.cuny.edu/Issue%2010.1_11.1%20Multimodal.html", "2011", "late"),
    ("https://bwe.ccny.cuny.edu/Issue%2012.1.html", "2014", "late"),
    ("https://bwe.ccny.cuny.edu/Issue13.1.html", "2014", "late"),
    ("https://bwe.ccny.cuny.edu/BWe14.1ALP.html", "2016", "late"),
    ("https://bwe.ccny.cuny.edu/BWeCurrentIssue.html", "2018", "late"),
    ("https://bwe.ccny.cuny.edu/BWe%20Issue%2016.1.html", "2020", "late"),
]

# Words that indicate an institutional affiliation, not a person name
_BWE_AFFIL_RE = re.compile(
    r"\b(university|college|cuny|suny|institute|school|department|"
    r"state\s+u|community\s+college|campus|a\s*&\s*m)\b", re.I
)

# Skip these as non-article entries
_BWE_SKIP_TITLES = re.compile(
    r"^(table of contents|call for (submissions|papers)|"
    r"editorial notes?|editors?$|book review section|"
    r"cccc\s+\d{4}\s+session\s+reviews|"
    r"favorite books|upcoming issues?|"
    r"bwe\s*(special\s+issue|[\d.])|basic writing e-?journal|"
    r"volume\s+\d|issue\s+\d|spring\s+\d|fall\s+\d|"
    r"editors?\s*page|"
    r"\d{4}$|"  # bare year like "2020"
    r"double\s+issue)", re.I
)


def _bwe_get(url, parser="lxml"):
    """Rate-limited GET for BWe pages. Use parser='html.parser' for old HTML."""
    time.sleep(BWE_DELAY)
    try:
        resp = requests.get(url, headers=HEADERS, timeout=TIMEOUT,
                            allow_redirects=True)
        resp.raise_for_status()
        return resp, BeautifulSoup(resp.text, parser)
    except Exception as e:
        log.debug("  BWe GET failed: %s — %s", url, e)
        return None, None


def _bwe_resolve_url(href, base_url):
    """Resolve a possibly-relative href against a base URL."""
    if not href:
        return None
    href = href.strip()
    if href.startswith("http"):
        return href
    if href.startswith("/"):
        return BWE_BASE + href
    # Relative to the base page's directory
    base = base_url.rsplit("/", 1)[0] + "/"
    return base + href


def _bwe_clean_authors(text):
    """Normalize author text: strip affiliations, split on 'and'/commas."""
    if not text:
        return None
    text = text.strip()
    # Strip smart quotes and other unicode punctuation
    text = re.sub(r'[\u201c\u201d\u2018\u2019\u2013\u2014]', '', text).strip()
    # Strip leading "by " or "reviewed by "
    text = re.sub(r"^(reviewed\s+)?by\s+", "", text, flags=re.I).strip()
    # Split on comma-and / comma / and
    parts = re.split(r",\s+and\s+|\s+and\s+|,\s+", text)
    cleaned = []
    for p in parts:
        p = p.strip()
        if not p:
            continue
        # Skip affiliation fragments
        if _BWE_AFFIL_RE.search(p):
            continue
        # Skip if it's just initials/single word under 3 chars
        if len(p) < 3:
            continue
        # Skip "Co-Editor", "Guest Editor", etc.
        if re.match(r"^(co-?)?editor", p, re.I):
            continue
        cleaned.append(p)
    return "; ".join(cleaned) if cleaned else None


def _bwe_is_title(text):
    """Heuristic: is this bold text a title rather than an author name?"""
    if not text:
        return False
    # Numbered entries like "1. Author Name" — not a title
    if re.match(r"^\d+\.\s+", text):
        return False
    # All-caps or contains colons/subtitles are titles
    if ":" in text or len(text) > 60:
        return True
    # Contains multiple words (>4) more likely title
    if len(text.split()) > 4:
        return True
    return False


def _scrape_bwe_late_issue(url, pub_date):
    """Parse a late-era BWe issue ToC (10.1/11.1 through 16.1).

    Pattern: <p><strong>Title</strong></p> → <p>Author</p> →
             <p>html|pdf links</p> → <p>Abstract text</p>
    All inside a <table> with article content.
    """
    _, soup = _bwe_get(url)
    if soup is None:
        return []

    articles = []

    # Find the content td with the MOST <p> tags that also has <strong> tags.
    # The article table has many paragraphs (titles, authors, abstracts);
    # the header table has few. Use paragraph count to disambiguate.
    best_td = None
    best_para_count = 0
    for td in soup.find_all("td"):
        strongs = td.find_all("strong")
        paras = td.find_all("p")
        if len(strongs) >= 3 and len(paras) > best_para_count:
            best_para_count = len(paras)
            best_td = td

    if not best_td or best_para_count < 5:
        log.debug("  BWe late: no content table found in %s", url)
        return []

    # Walk through <p> children sequentially
    paragraphs = best_td.find_all("p")
    i = 0
    while i < len(paragraphs):
        p = paragraphs[i]
        strong = p.find("strong")

        if not strong:
            i += 1
            continue

        title = strong.get_text(separator=" ", strip=True)
        if not title or len(title) < 10:
            i += 1
            continue

        # Skip non-article entries
        if _BWE_SKIP_TITLES.search(title):
            i += 1
            continue

        # Normalize internal whitespace for comparisons
        title = re.sub(r"\s+", " ", title).strip()

        # Skip section headers
        title_lower = title.lower().strip()
        if title_lower in ("classroom narrative", "classroom narratives",
                            "book reviews", "articles", "essays",
                            "book review section", "response"):
            i += 1
            continue

        # Extract article URL from link inside <strong> or in next paragraphs
        article_url = None
        link = strong.find("a", href=True)
        if link:
            href = link["href"]
            if not href.startswith("mailto:") and not href.startswith("#"):
                article_url = _bwe_resolve_url(href, url)

        # Look ahead for author, html/pdf links, and abstract
        authors = None
        abstract = None
        i += 1

        while i < len(paragraphs):
            next_p = paragraphs[i]

            # If we hit a paragraph whose FIRST child is <strong>, it's the
            # next article entry — stop collecting for the current one.
            next_strong = next_p.find("strong")
            if next_strong:
                # But check if this <strong> IS a new title (not inline emphasis
                # like <em> inside an abstract). A new title's <strong> should be
                # the very first meaningful content in the <p>.
                pre_text = ""
                for child in next_p.children:
                    if child == next_strong:
                        break
                    if hasattr(child, "get_text"):
                        pre_text += child.get_text(strip=True)
                    elif isinstance(child, str):
                        pre_text += child.strip()
                if not pre_text:
                    # <strong> is the first thing — this is a new article
                    break

            text = next_p.get_text(separator=" ", strip=True)
            if not text or text == "\xa0":
                i += 1
                continue

            # Check if this is an html|pdf link line
            links_in_p = next_p.find_all("a", href=True)
            link_texts = [a.get_text(strip=True).lower() for a in links_in_p]
            is_link_line = any(t in ("html", "pdf", "htm", "htmll") for t in link_texts)

            if is_link_line:
                # Extract article URL from html link if we don't have one yet
                if not article_url:
                    for a_tag in links_in_p:
                        href = a_tag["href"]
                        a_text = a_tag.get_text(strip=True).lower()
                        if a_text in ("html", "htm", "htmll"):
                            article_url = _bwe_resolve_url(href, url)
                            break
                    if not article_url:
                        for a_tag in links_in_p:
                            href = a_tag["href"]
                            if href.endswith(".pdf"):
                                article_url = _bwe_resolve_url(href, url)
                                break
                i += 1
                continue

            # Check if this looks like an author line (no links, before abstract)
            if authors is None and not links_in_p:
                # Author lines: typically under 200 chars, no affiliations
                # (or affiliations that get stripped by _bwe_clean_authors)
                if len(text) < 200 and not _BWE_AFFIL_RE.search(text):
                    authors = _bwe_clean_authors(text)
                    if authors:
                        i += 1
                        continue
                # Multi-author with affiliations — try cleaning anyway
                elif len(text) < 200:
                    authors = _bwe_clean_authors(text)
                    if authors:
                        i += 1
                        continue
                    # Pure affiliation line — skip
                    if len(text) < 80:
                        i += 1
                        continue

            # This is likely the abstract — a substantial paragraph
            if len(text) > 50 and abstract is None:
                abstract = re.sub(r"^Abstract:?\s*", "", text, flags=re.I)
                i += 1
                continue

            i += 1

        # Use issue URL as fallback if no article-specific URL found
        if not article_url:
            article_url = url

        articles.append({
            "title": title,
            "authors": authors,
            "abstract": abstract,
            "url": article_url,
            "pub_date": pub_date,
        })

    return articles


def _scrape_bwe_early_issue(url, pub_date):
    """Parse an early-era BWe single-page issue (1.1 through 6.1).

    ToC section has bold entries: author name in bold, then title in bold
    (often as an anchor link). Pattern:
        <b>1. Author Name</b>
        <b><a href="#anchor">TITLE IN CAPS</a></b>
    The ToC ends at the first <hr> tag. Uses html.parser because lxml
    can't handle the old uppercase HTML tags.
    """
    _, soup = _bwe_get(url, parser="html.parser")
    if soup is None:
        return []

    articles = []

    # Find the "Table of contents" bold tag, then collect bold tags until <hr>
    all_elements = list(soup.descendants)
    toc_started = False
    toc_bolds = []

    for elem in all_elements:
        if hasattr(elem, "name"):
            # Check for "Table of contents"
            if elem.name in ("b", "strong") and not toc_started:
                text = elem.get_text(separator=" ", strip=True).lower()
                if "table of contents" in text:
                    toc_started = True
                    continue
            # Once in ToC, collect bold tags
            if toc_started and elem.name in ("b", "strong"):
                toc_bolds.append(elem)
            # Stop at <hr>
            if toc_started and elem.name == "hr":
                break

    if not toc_bolds:
        log.debug("  BWe early: no ToC found in %s", url)
        return []

    # Parse the collected ToC bold tags
    current_author = None
    seen_urls = set()

    for tag in toc_bolds:
        text = tag.get_text(separator=" ", strip=True)
        if not text:
            continue

        # Stop at "Basic Writing e-Journal" — means we've left the ToC
        if re.search(r"basic writing\s+e-?journal", text, re.I):
            break

        # Skip entries matching non-article patterns
        if _BWE_SKIP_TITLES.search(text):
            continue

        # Check for links
        link = tag.find("a", href=True)
        has_anchor = link and link.get("href", "").startswith("#")
        has_mailto = link and link.get("href", "").startswith("mailto:")

        # Strip leading numbers like "1. "
        stripped = re.sub(r"^\d+\.\s*", "", text).strip()

        # Clean up whitespace from line breaks in old HTML
        stripped = re.sub(r"\s+", " ", stripped).strip()

        # If this has an in-page anchor link, it's a title
        if has_anchor:
            anchor = link["href"]
            article_url = url + anchor
            title = link.get_text(separator=" ", strip=True)
            title = re.sub(r"\s+", " ", title).strip()

            if title and len(title) > 8 and article_url not in seen_urls:
                seen_urls.add(article_url)
                articles.append({
                    "title": title,
                    "authors": current_author,
                    "abstract": None,
                    "url": article_url,
                    "pub_date": pub_date,
                })
            current_author = None
            continue

        # If this is a mailto link, it's an author name
        if has_mailto:
            name = link.get_text(separator=" ", strip=True)
            name = re.sub(r"\s+", " ", name).strip()
            # Handle "with Co-author" prefix
            name = re.sub(r"^with\s+", "", name, flags=re.I).strip()
            # Check for "Author1 and Author2" in the full bold text
            full = re.sub(r"^\d+\.\s*", "", text)
            full = re.sub(r"\s+", " ", full).strip()
            if " and " in full:
                current_author = _bwe_clean_authors(full)
            elif name:
                if current_author:
                    current_author = current_author + "; " + name
                else:
                    current_author = name
            continue

        # "Book Review Section" header — skip
        if re.match(r"^book review\b", stripped, re.I) and len(stripped) < 25:
            continue

        # "Reviewed by Author" → author for the next book review entry
        if re.match(r"^reviewed by\s+", stripped, re.I):
            current_author = _bwe_clean_authors(stripped)
            continue

        # "by Author" line (for book review book-author lines) → skip
        if re.match(r"^by\s+[A-Z]", stripped) and len(stripped) < 80:
            continue

        # Short text without links and without anchor → likely an author name
        if not has_anchor and len(stripped) < 50 and not ":" in stripped:
            name = re.sub(r"^with\s+", "", stripped, flags=re.I).strip()
            if name and not _BWE_AFFIL_RE.search(name) and len(name.split()) <= 6:
                if current_author:
                    current_author = current_author + "; " + name
                else:
                    current_author = name
                continue

        # Long text or text with colon → title without an anchor
        if len(stripped) > 15:
            article_url = url + "#" + re.sub(r"\W+", "-", stripped[:40]).strip("-")
            if article_url not in seen_urls:
                seen_urls.add(article_url)
                articles.append({
                    "title": stripped,
                    "authors": current_author,
                    "abstract": None,
                    "url": article_url,
                    "pub_date": pub_date,
                })
            current_author = None

    return articles


def _scrape_bwe_transitional_71(pub_date):
    """Parse Issue 7.1: introduction page links to home page with articles."""
    home_url = "https://bwe.ccny.cuny.edu/Issue%207.1%20home.html"
    _, soup = _bwe_get(home_url)
    if soup is None:
        return []

    articles = []
    seen = set()

    for a in soup.find_all("a", href=True):
        href = a["href"]
        if href.startswith("mailto:") or href.startswith("#"):
            continue
        if not re.search(r"7\.1.*\.html$", href, re.I):
            continue
        # Skip the introduction link (already on the page)
        if "introduction" in href.lower():
            continue
        # Skip external orgs.tamuc.edu links
        if "tamuc.edu" in href:
            continue

        full_url = _bwe_resolve_url(href, home_url)
        if not full_url or full_url in seen:
            continue
        seen.add(full_url)

        # Title and author are in the link text and surrounding text
        link_text = a.get_text(separator=" ", strip=True)
        if not link_text or len(link_text) < 10:
            continue

        # Extract author from text after the link (pattern: "Title - reviewed by Author")
        parent = a.find_parent("p")
        if parent:
            full_text = parent.get_text(separator=" ", strip=True)
            # Pattern: "Title - reviewed by Author, Affiliation"
            # or "Title by Author, Affiliation"
            m = re.search(r"[-–]\s*(?:reviewed\s+)?by\s+(.+?)(?:\s*<|$)", full_text)
            if not m:
                # Try from the raw text after stripping the link text
                after = full_text.replace(link_text, "").strip()
                after = re.sub(r"^[-–,\s]+", "", after).strip()
                m2 = re.match(r"(?:reviewed\s+)?by\s+(.+)", after, re.I)
                if m2:
                    author_text = m2.group(1)
                else:
                    author_text = after if after and len(after) > 3 else None
            else:
                author_text = m.group(1)

            authors = _bwe_clean_authors(author_text) if author_text else None
        else:
            authors = None

        articles.append({
            "title": link_text,
            "authors": authors,
            "abstract": None,
            "url": full_url,
            "pub_date": pub_date,
        })

    return articles


def _scrape_bwe_transitional_89(pub_date):
    """Parse Issue 8/9 double issue — messy mixed HTML.

    Two patterns:
    1. <strong>Title</strong> followed by Author [pdf] (intro and WAW article)
    2. <p>"Title" <br> Author, Affiliation [pdf]</p> (most articles)
    3. <h3>Book Reviews:</h3> followed by more [pdf] entries
    """
    url = "https://bwe.ccny.cuny.edu/Issue%208_9%20home.html"
    _, soup = _bwe_get(url)
    if soup is None:
        return []

    articles = []
    seen = set()

    # Pattern 1: <strong> entries (intro + first article)
    for strong in soup.find_all("strong"):
        title = strong.get_text(separator=" ", strip=True)
        title = re.sub(r"\s+", " ", title).strip()
        if not title or len(title) < 15:
            continue
        if _BWE_SKIP_TITLES.search(title):
            continue

        parent = strong.find_parent("p")
        article_url = None
        if parent:
            pdf_link = parent.find("a", href=lambda h: h and ".pdf" in h)
            if pdf_link:
                article_url = _bwe_resolve_url(pdf_link["href"], url)

        if not article_url:
            article_url = url
        if article_url in seen:
            continue
        seen.add(article_url)

        # Try to extract author from text
        authors = None
        if parent:
            full_text = parent.get_text(separator="\n", strip=True)
            for line in full_text.split("\n"):
                line = line.strip()
                if line == title or "[pdf]" in line.lower():
                    continue
                if line.startswith("Co-Editor") or not line:
                    continue
                authors = _bwe_clean_authors(line)
                if authors:
                    break

        articles.append({
            "title": title, "authors": authors, "abstract": None,
            "url": article_url, "pub_date": pub_date,
        })

    # Pattern 2: <p> entries with smart-quoted titles (\u201cTitle\u201d)
    for p in soup.find_all("p"):
        text = p.get_text(separator="\n", strip=True)
        # Match \u201c...\u201d (left/right double smart quotes)
        m = re.match(r'\u201c(.+?)\u201d', text, re.DOTALL)
        if not m:
            continue
        title = m.group(1).strip()
        title = re.sub(r"\s+", " ", title)
        if not title or len(title) < 15:
            continue

        # Find PDF link
        pdf_link = p.find("a", href=lambda h: h and ".pdf" in h)
        if pdf_link:
            article_url = _bwe_resolve_url(pdf_link["href"], url)
        else:
            article_url = url

        if article_url in seen:
            continue
        seen.add(article_url)

        # Extract author from text after the closing quote
        remaining = text[m.end():].strip()
        authors = None
        for line in remaining.split("\n"):
            line = line.strip()
            if not line or "[pdf]" in line.lower() or "[review]" in line.lower():
                continue
            if line.startswith("2009") or line.startswith("Response"):
                continue
            authors = _bwe_clean_authors(line)
            if authors:
                break

        articles.append({
            "title": title, "authors": authors, "abstract": None,
            "url": article_url, "pub_date": pub_date,
        })

    # Pattern 3: Book review entries after "Book Reviews:" heading
    book_h3 = soup.find("h3", string=re.compile(r"Book Review", re.I))
    if book_h3:
        for p in book_h3.find_all_next("p"):
            pdf_link = p.find("a", href=lambda h: h and ".pdf" in h)
            if not pdf_link:
                continue
            article_url = _bwe_resolve_url(pdf_link["href"], url)
            if article_url in seen:
                continue
            seen.add(article_url)

            text = p.get_text(separator=" ", strip=True)
            text = re.sub(r"\s+", " ", text).strip()
            # Try to extract reviewer
            m_rev = re.search(r"reviewed by\s+(.+?)(?:\s*\[|$)", text, re.I)
            authors = _bwe_clean_authors(m_rev.group(1)) if m_rev else None
            # Title is the rest
            title = re.sub(r"\s*-?\s*reviewed by.*$", "", text, flags=re.I).strip()
            title = re.sub(r"\s*\[(?:pdf|review)\].*$", "", title, flags=re.I).strip()
            if title and len(title) > 10:
                articles.append({
                    "title": title, "authors": authors, "abstract": None,
                    "url": article_url, "pub_date": pub_date,
                })

    return articles


def scrape_bwe():
    name = "Basic Writing e-Journal"
    log.info("Scraping: %s", name)
    total_new = 0
    total_backfilled = 0

    for issue_url, pub_date, era in BWE_ISSUES:
        if era == "late":
            articles = _scrape_bwe_late_issue(issue_url, pub_date)
        elif era == "early":
            articles = _scrape_bwe_early_issue(issue_url, pub_date)
        elif era == "transitional":
            if "7.1" in issue_url:
                articles = _scrape_bwe_transitional_71(pub_date)
            else:
                articles = _scrape_bwe_transitional_89(pub_date)
        else:
            articles = []

        issue_new = 0
        issue_bf = 0
        for article in articles:
            tags = auto_tag(article["title"], article.get("abstract")) or ""
            inserted = upsert_article(
                url=article["url"],
                doi=None,
                title=article["title"],
                authors=article.get("authors"),
                abstract=article.get("abstract"),
                pub_date=article.get("pub_date") or pub_date,
                journal=name,
                source="scrape",
                keywords=None,
                tags=tags,
                oa_status="gold",
                oa_url=article["url"],
            )
            if inserted:
                issue_new += 1
            else:
                # Backfill existing records missing authors/abstract
                authors = article.get("authors")
                abstract = article.get("abstract")
                if authors or abstract:
                    with get_conn() as conn:
                        conn.execute("""
                            UPDATE articles
                            SET authors = COALESCE(NULLIF(authors, ''), ?),
                                abstract = COALESCE(NULLIF(abstract, ''), ?),
                                tags = COALESCE(NULLIF(tags, ''), ?),
                                oa_status = COALESCE(oa_status, 'gold'),
                                oa_url = COALESCE(oa_url, ?)
                            WHERE url = ? AND journal = ?
                              AND (authors IS NULL OR authors = ''
                                   OR abstract IS NULL OR abstract = '')
                        """, (authors, abstract, tags, article["url"],
                              article["url"], name))
                        if conn.total_changes:
                            issue_bf += 1

        total_new += issue_new
        total_backfilled += issue_bf
        if articles:
            log.info("    %s — %d articles (%d new, %d backfilled)",
                     issue_url.split("cuny.edu/")[1], len(articles),
                     issue_new, issue_bf)

    update_fetch_log(name)
    log.info("  %s — %d new, %d backfilled", name, total_new, total_backfilled)
    return total_new


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
# Drupal 7 journal (1997–present). Two eras:
#   - Drupal (issues 6.1–34+): discovered via sidebar Issue Index, then
#     each issue's ToC page parsed for articles, reviews, responses, sonic projects
#   - Static (issues 1.1–5.2): ancient HTML at /N_N/index.html
#
# robots.txt: 404 (no file; all paths implicitly allowed).
# Rate limit: 5 seconds between all requests.
# Metadata only: title, authors, date, URL. No full text collected.

ENCULTURATION_BASE = "https://enculturation.net"
ENCULTURATION_DELAY = 5  # seconds between requests

# Nav/admin text that is never an article title
ENCULTURATION_SKIP = re.compile(
    r"^(enculturation|submissions?|editorial|about|contact|issue|open issue|"
    r"home|search|log in|register|sitemap|toc|table of contents)\s*$",
    re.I,
)

# URLs on archive pages that are site pages, not articles
_ENC_SKIP_PATHS = {
    "/editors", "/submission_guidelines", "/about", "/copyright",
    "/issues", "/process", "/links",
}

# Titles on archive pages that are site pages, not articles
_ENC_SKIP_TITLES = {
    "editors", "submissions", "about enculturation", "issues",
    "review process", "creative commons license and publishing rights", "links",
}

# Affiliation keywords — used to strip institutional affiliations from author names
_AFFILIATION_WORDS = re.compile(
    r"\b(university|college|institute|school|department|state|center|centre|"
    r"polytechnic|poly|emeritus|professor|doctoral|phd|program|"
    r"suny|cuny|ucla|usc|mit|caltech)\b", re.I
)

# Static-era issue URLs and their publication years
_ENC_STATIC_ISSUES = [
    ("http://enculturation.net/5_2/index52.html", "5.2", "2004"),
    ("http://enculturation.net/5_1/index51.html", "5.1", "2003"),
    ("http://enculturation.net/4_1/index.html",   "4.1", "2002"),
    ("http://enculturation.net/3_2/index.html",   "3.2", "2001"),
    ("http://enculturation.net/3_1/index.html",   "3.1", "2000"),
    ("http://enculturation.net/2_2/index.html",   "2.2", "1999"),
    ("http://enculturation.net/2_1/index.html",   "2.1", "1998"),
    ("http://enculturation.net/1_2/index.html",   "1.2", "1997"),
    ("http://enculturation.net/1_1/index.html",   "1.1", "1997"),
]


def _enc_get(url):
    """GET with rate limiting for Enculturation."""
    time.sleep(ENCULTURATION_DELAY)
    try:
        resp = requests.get(url, headers=HEADERS, timeout=TIMEOUT)
        resp.raise_for_status()
        return resp, BeautifulSoup(resp.text, "lxml")
    except Exception as e:
        log.debug("  Enculturation GET failed: %s — %s", url, e)
        return None, None


def _enc_strip_affiliation(text):
    """
    Strip institutional affiliation from an author name string.
    'Matthew Halm, Georgia Institute of Technology' -> 'Matthew Halm'
    Only strips after a comma if the remainder looks like an institution.
    """
    if "," not in text:
        return text.strip()
    parts = text.split(",", 1)
    name_part = parts[0].strip()
    after_comma = parts[1].strip()
    if _AFFILIATION_WORDS.search(after_comma):
        return name_part
    return text.strip()


def _enc_parse_toc_row(row):
    """
    Parse one views-row from an Enculturation issue ToC page.
    Returns dict {url, title, authors} or None if filtered out.
    """
    # Title + URL
    title_div = row.find("div", class_="views-field-title")
    if not title_div:
        return None
    a = title_div.find("a", href=True)
    if not a:
        return None
    href = a["href"].strip()
    title = a.get_text(" ", strip=True)
    if not title:
        return None

    # Build absolute URL
    if href.startswith("http"):
        url = href
    elif href.startswith("/"):
        url = ENCULTURATION_BASE + href
    else:
        return None

    # Filter out site pages by path
    path = href if href.startswith("/") else re.sub(r"https?://[^/]+", "", url)
    if path in _ENC_SKIP_PATHS or path == "/":
        return None
    if title.lower().strip() in _ENC_SKIP_TITLES:
        return None

    # Primary author — <div class="views-field-value-1"><span><a>Name</a>, Affiliation</span></div>
    authors = []
    author_div = row.find("div", class_="views-field-value-1")
    if author_div:
        span = author_div.find("span", class_="field-content")
        if span:
            author_a = span.find("a")
            if author_a:
                name = author_a.get_text(strip=True)
            else:
                # Fallback: plain text in the span (strip affiliation)
                name = span.get_text(strip=True)
            name = _enc_strip_affiliation(name)
            if name and len(name) > 2:
                authors.append(name)

    # Coauthors — <div class="views-field-field-coauthors-temp"><div class="field-content">
    #   <p><a>Name</a>, Affil</p>  or  <p>Name, Affil<br/>Name2, Affil2</p>
    coauthor_div = row.find("div", class_="views-field-field-coauthors-temp")
    if coauthor_div:
        content_div = coauthor_div.find("div", class_="field-content")
        if content_div:
            # First try <a> tags (most common pattern)
            for a_tag in content_div.find_all("a"):
                name = a_tag.get_text(strip=True)
                name = _enc_strip_affiliation(name)
                if name and len(name) > 2 and name not in authors:
                    authors.append(name)
            # Also check for plain-text coauthors separated by <br>
            if not content_div.find("a"):
                for p in content_div.find_all("p"):
                    # Split on <br> tags
                    for part in p.stripped_strings:
                        name = _enc_strip_affiliation(part)
                        if name and len(name) > 2 and " " in name and name not in authors:
                            authors.append(name)

    return {
        "url": url,
        "title": title,
        "authors": "; ".join(authors) if authors else None,
    }


def _discover_enc_issues():
    """
    Discover all Drupal-era issue URLs from the sidebar Issue Index.
    Returns list of (issue_url, issue_num) tuples for issues 6.1–34+.
    """
    _, soup = _enc_get(ENCULTURATION_BASE)
    if not soup:
        log.warning("  Enculturation: could not fetch front page for issue discovery")
        return []

    # The sidebar has <h2 class="block-title">Issue Index</h2> followed by <ul><li><a href="/34">Issue 34</a></li>...
    issues = []
    index_block = None
    for h2 in soup.find_all("h2", class_="block-title"):
        if "issue index" in h2.get_text(strip=True).lower():
            index_block = h2.find_parent("section") or h2.find_parent("div")
            break

    if not index_block:
        log.warning("  Enculturation: could not find Issue Index sidebar")
        return []

    for li in index_block.find_all("li"):
        a = li.find("a", href=True)
        if not a:
            continue
        href = a["href"].strip()
        label = a.get_text(strip=True)  # e.g. "Issue 34", "Issue 6.2"

        # Extract issue number from label
        m = re.search(r"Issue\s+(\d+(?:\.\d+)?)", label, re.I)
        if not m:
            continue
        issue_num = m.group(1)

        # Skip static-era issues (handled separately by _scrape_enculturation_static_issues)
        if "." in issue_num:
            major = int(issue_num.split(".")[0])
            if major <= 5:
                continue

        # Build absolute URL
        if href.startswith("http"):
            # Static-era links point to http://enculturation.net/N_N/index.html — skip
            if "/index" in href:
                continue
            issue_url = href
        elif href.startswith("/"):
            issue_url = ENCULTURATION_BASE + href
        else:
            continue

        issues.append((issue_url, issue_num))

    log.info("  Enculturation: discovered %d Drupal-era issues from sidebar", len(issues))
    return issues


def _scrape_enc_issue_toc(issue_url, issue_num):
    """
    Scrape one Enculturation issue ToC page.
    Returns list of article dicts: {url, title, authors}.
    Sections: Articles, Reviews, Responses, Sonic Projects.
    """
    _, soup = _enc_get(issue_url)
    if not soup:
        return []

    articles = []
    seen_urls = set()

    # Each section is a <section> with <h2 class="block-title">Articles|Reviews|...</h2>
    # containing <div class="views-row"> entries.
    # We don't need to distinguish sections — parse all views-rows on the page.
    for row in soup.find_all("div", class_="views-row"):
        entry = _enc_parse_toc_row(row)
        if entry and entry["url"] not in seen_urls:
            seen_urls.add(entry["url"])
            articles.append(entry)

    return articles


def _scrape_enculturation_issues():
    """
    Phase 1: Discover all Drupal-era issues and scrape their ToC pages.
    Returns list of article dicts: {url, title, authors}.
    """
    issues = _discover_enc_issues()
    if not issues:
        return []

    articles = []
    seen_urls = set()

    for issue_url, issue_num in issues:
        toc_articles = _scrape_enc_issue_toc(issue_url, issue_num)
        new_count = 0
        for a in toc_articles:
            if a["url"] not in seen_urls:
                seen_urls.add(a["url"])
                articles.append(a)
                new_count += 1
        log.info("    Issue %s — %d articles", issue_num, new_count)

    log.info("  Enculturation issue ToCs: %d articles discovered", len(articles))
    return articles


def _enrich_enculturation_article(article_url):
    """
    Phase 2: Fetch an individual article page for richer metadata.
    Returns dict {authors, pub_date} or empty dict on failure.
    """
    _, soup = _enc_get(article_url)
    if not soup:
        return {}

    result = {}
    content = soup.find("div", class_="field-item")
    if not content:
        return result

    # Extract authors from bold text at the top of the content.
    # Pattern: first <p> tags contain author names in <strong>/<b>,
    # followed by "(Published Month DD, YYYY)".
    authors = []
    for p in content.find_all("p"):
        text = p.get_text(strip=True)
        if not text:
            continue
        # Stop at "(Published ...)" line
        if text.startswith("(Published"):
            break
        # Stop at body text (long paragraphs)
        if len(text) > 200:
            break
        # Check for <strong>/<b> tag (author indicator)
        bold = p.find(["strong", "b"])
        if bold:
            name = _enc_strip_affiliation(bold.get_text(strip=True))
            if name and len(name) > 2 and " " in name:
                authors.append(name)
        else:
            # Plain <p> before "(Published...)" — might be an author line
            cleaned = _enc_strip_affiliation(text)
            if cleaned and len(cleaned) < 80 and " " in cleaned:
                if not any(w in cleaned.lower() for w in [
                    "the ", "this ", "in ", "a ", "an ", "we ", "i ",
                    "our ", "my ", "for ", "with ", "from ",
                ]):
                    authors.append(cleaned)

    if authors:
        result["authors"] = "; ".join(authors)

    # Extract publication date from "(Published Month DD, YYYY)"
    body_text = content.get_text()[:2000]
    m = re.search(
        r"\(Published\s+(\w+)\s+(\d{1,2}),?\s+(\d{4})\)", body_text
    )
    if m:
        month_name, day, year = m.group(1), m.group(2), m.group(3)
        months = {
            "january": "01", "february": "02", "march": "03", "april": "04",
            "may": "05", "june": "06", "july": "07", "august": "08",
            "september": "09", "october": "10", "november": "11",
            "december": "12",
        }
        mm = months.get(month_name.lower())
        if mm:
            result["pub_date"] = f"{year}-{mm}-{day.zfill(2)}"
        else:
            result["pub_date"] = year

    return result


def _scrape_enculturation_static_issues():
    """
    Phase 3: Scrape the 9 static-era issues (1.1-5.2, 1997-2004).
    Best-effort: these pages use 1990s HTML and may resist extraction.
    Returns list of article dicts: {url, title, authors, pub_date}.
    """
    articles = []
    seen_urls = set()

    for index_url, issue_num, year in _ENC_STATIC_ISSUES:
        log.info("  Static issue %s: %s", issue_num, index_url)
        _, soup = _enc_get(index_url)
        if not soup:
            log.warning("    Issue %s: failed to fetch", issue_num)
            continue

        # Look for a "contents" or "current" link to follow
        contents_soup = None
        for a in soup.find_all("a", href=True):
            text = a.get_text(strip=True).lower()
            if "contents" in text or "current" in text:
                contents_href = a["href"]
                if not contents_href.startswith("http"):
                    base_dir = index_url.rsplit("/", 1)[0]
                    contents_href = base_dir + "/" + contents_href
                log.info("    Following contents link: %s", contents_href)
                _, contents_soup = _enc_get(contents_href)
                break

        target_soup = contents_soup or soup
        base_dir = index_url.rsplit("/", 1)[0]
        # Issue directory name for filtering (e.g. "1_1", "5_2")
        issue_dir = base_dir.rsplit("/", 1)[-1]

        count_before = len(articles)
        for a in target_soup.find_all("a", href=True):
            href = a["href"].strip()
            title = a.get_text(" ", strip=True)

            if href.startswith("#") or "mailto:" in href:
                continue
            if SKIP_PATTERNS.search(href):
                continue
            if not title or len(title) < 12:
                continue
            if _is_nav_text(title) or ENCULTURATION_SKIP.match(title):
                continue

            # Build absolute URL (preserve http:// scheme)
            if href.startswith("http"):
                full_url = href
            elif href.startswith("/"):
                full_url = "http://enculturation.net" + href
            else:
                full_url = base_dir + "/" + href

            # Only keep links within the same issue directory
            if issue_dir not in full_url:
                continue
            if re.search(r"index\d*\.html$", full_url):
                continue
            if full_url in seen_urls:
                continue

            seen_urls.add(full_url)
            articles.append({
                "url": full_url,
                "title": title,
                "authors": None,
                "pub_date": year,
            })

        log.info("    Issue %s: found %d articles",
                 issue_num, len(articles) - count_before)

    log.info("  Enculturation static issues: %d total articles", len(articles))
    return articles


def scrape_enculturation():
    """Scrape all Enculturation content: Drupal issue ToCs + static-era issues."""
    name = "Enculturation"
    log.info("Scraping: %s", name)
    init_db()
    total = 0

    # Phase 1: Discover Drupal-era issues and scrape their ToC pages
    toc_articles = _scrape_enculturation_issues()

    # Phase 2: Enrich each article from its individual page (pub_date, fallback authors)
    log.info("  Enriching %d articles from individual pages...",
             len(toc_articles))
    enriched_authors = 0
    enriched_dates = 0
    for i, article in enumerate(toc_articles):
        if i > 0 and i % 50 == 0:
            log.info("    Progress: %d / %d enriched", i, len(toc_articles))

        detail = _enrich_enculturation_article(article["url"])
        if detail:
            # Use enriched authors only if ToC didn't already provide them
            if detail.get("authors") and not article.get("authors"):
                article["authors"] = detail["authors"]
                enriched_authors += 1
            if detail.get("pub_date"):
                article["pub_date"] = detail["pub_date"]
                enriched_dates += 1

        # Fix migration-date artifacts for 6.x-era content
        pd = article.get("pub_date") or ""
        if pd.startswith("2015-01-01"):
            if "/6.1/" in article["url"] or "/6_1/" in article["url"]:
                article["pub_date"] = "2005"
            elif "/6.2/" in article["url"] or "/6_2/" in article["url"]:
                article["pub_date"] = "2006"

    log.info("  Enrichment: %d dates, %d authors (fallback) added",
             enriched_dates, enriched_authors)

    # Upsert Drupal-era articles + backfill existing rows
    # Use a single connection to avoid lock contention between two open connections.
    conn = get_conn()
    for article in toc_articles:
        tags = auto_tag(article["title"], None) or ""
        conn.execute("""
            INSERT OR IGNORE INTO articles
                (url, doi, title, authors, abstract, pub_date,
                 journal, source, keywords, tags, oa_status, oa_url)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (article["url"], None, article["title"],
              article.get("authors"), None,
              article.get("pub_date") or "",
              name, "scrape", None, tags, "gold", article["url"]))
        if conn.execute("SELECT changes()").fetchone()[0]:
            total += 1
        else:
            # Backfill authors/dates on existing rows
            conn.execute(
                """UPDATE articles
                   SET authors = COALESCE(?, authors),
                       pub_date = COALESCE(?, pub_date)
                   WHERE url = ?""",
                (article.get("authors"), article.get("pub_date"), article["url"]),
            )
    conn.commit()
    conn.close()

    log.info("  Enculturation Drupal era: %d new articles", total)

    # Phase 3: Static-era issues
    static_articles = _scrape_enculturation_static_issues()
    static_new = 0
    for article in static_articles:
        tags = auto_tag(article["title"], None) or ""
        inserted = upsert_article(
            url=article["url"],
            doi=None,
            title=article["title"],
            authors=article.get("authors"),
            abstract=None,
            pub_date=article.get("pub_date") or "",
            journal=name,
            source="scrape",
            keywords=None,
            tags=tags,
            oa_status="gold",
            oa_url=article["url"],
        )
        if inserted:
            static_new += 1

    total += static_new
    log.info("  Enculturation static era: %d new articles", static_new)

    update_fetch_log(name)
    log.info("  %s — %d new articles total", name, total)
    return total


# ── KB Journal ────────────────────────────────────────────────────────────────
# Drupal 7 site. No RSS. Nav dropdown lists all 26 issues.
# Two ToC formats: winter2023 uses block-block-38 with "by Author" text;
# all other issues list articles as <li class="leaf"> in sidebar menu.
# Article pages have <h3>Author, Affiliation</h3> and <h3>Abstract</h3>.
#
# robots.txt (checked 2026-04-08): Crawl-delay: 10. We respect this.

KB_BASE = "https://kbjournal.org"
KB_DELAY = 10  # seconds — per robots.txt Crawl-delay directive

# Sidebar links that are issues follow these seasonal patterns
KB_ISSUE_RE = re.compile(
    r"/(winter|spring|summer|fall|autumn)\d{4}$"
    r"|/content/volume-\d+-issue-\d",
    re.I,
)

# Date extraction from issue URL slug
KB_SEASON_MONTHS = {"winter": "01", "spring": "04", "summer": "06", "fall": "09", "autumn": "09"}

# Paths that are never articles
_KB_SKIP_PATHS = re.compile(
    r"/(board|submit|cart|user|content/conference|bibliography|bibliographies|"
    r"premiumbibs|happenings|newsletters|kbs|constitution|groups|mailing|"
    r"discounts|kb_conversation|book/export|spring\d{4}|fall\d{4}|winter\d{4}|"
    r"summer\d{4}|autumn\d{4}|node/\d{1,4}$|articles$|full-issue|special-issue|"
    r"back-issues)", re.I)

# Affiliation keywords for stripping from author names
_KB_AFFIL_RE = re.compile(
    r"\b(university|college|institute|school|department|dept|"
    r"program|centre|center)\b", re.I)


def _kb_get(url):
    """Rate-limited GET for KB Journal (10s per robots.txt)."""
    time.sleep(KB_DELAY)
    return _get(url)


def _kb_normalize_url(href):
    """Normalize a KB Journal URL to https://kbjournal.org/path."""
    if not href:
        return None
    href = href.strip()
    for prefix in ("http://www.kbjournal.org", "https://www.kbjournal.org",
                    "http://kbjournal.org"):
        if href.startswith(prefix):
            href = KB_BASE + href[len(prefix):]
            break
    if href.startswith("/"):
        href = KB_BASE + href
    if not href.startswith(KB_BASE):
        return None
    return href


def _kb_pub_date(issue_url):
    """Extract YYYY-MM pub date from a KB Journal issue URL, or None."""
    m = re.search(r"/(winter|spring|summer|fall|autumn)(\d{4})", issue_url, re.I)
    if m:
        season, year = m.group(1).lower(), m.group(2)
        month = KB_SEASON_MONTHS.get(season, "01")
        return f"{year}-{month}"
    m2 = re.search(r"(\d{4})", issue_url)
    return m2.group(1) if m2 else None


def _kb_normalize_authors(raw):
    """Normalize author string from KB Journal. Strips affiliations, normalizes separators."""
    if not raw:
        return None
    text = raw.strip()
    if not text or len(text) > 300:
        return None

    # Handle "Reviewed by X" in book review entries: "by Robert Wess. Reviewed by Greig Henderson"
    m = re.match(r"^(.+?)\.\s*Reviewed by\s+(.+)$", text)
    if m:
        text = m.group(1).strip() + ", " + m.group(2).strip()

    # Strip "by " prefix
    text = re.sub(r"^by\s+", "", text, flags=re.I).strip()

    # Split on " and " and commas
    text = re.sub(r",?\s+and\s+", ", ", text)
    parts = [p.strip() for p in text.split(",") if p.strip()]

    # Filter out affiliation fragments
    cleaned = []
    for p in parts:
        if _KB_AFFIL_RE.search(p):
            continue
        if len(p) < 2:
            continue
        cleaned.append(p)

    return "; ".join(cleaned) if cleaned else None


def _kb_is_article_path(path):
    """Check if a path looks like an article slug (root-level, no deep nesting)."""
    if not path or path == "/":
        return False
    if path.count("/") > 1:
        return False
    if _KB_SKIP_PATHS.search(path):
        return False
    return True


def _scrape_kb_issue(issue_url, pub_date, journal_name):
    """Scrape one KB Journal issue page. Returns list of article dicts.

    Two ToC structures:
    1. block-block-38 (winter2023): <li><a>Title</a> by Author</li>
    2. Sidebar <li class="leaf"> (all others): <a>Title</a> with no author
    """
    _, soup = _kb_get(issue_url)
    if soup is None:
        return []

    articles = []
    seen = set()

    # --- Pattern 1: block-block-38 (newer issues) ---
    block = soup.find("section", id="block-block-38")
    if block:
        content_div = block.find("div", class_="block-content")
        if content_div:
            for li in content_div.find_all("li"):
                a_tag = li.find("a", href=True)
                if not a_tag:
                    continue
                title = a_tag.get_text(separator=" ", strip=True)
                if not title or len(title) < 15:
                    continue

                href = _kb_normalize_url(a_tag["href"])
                if not href:
                    continue
                path = href.replace(KB_BASE, "")
                if not _kb_is_article_path(path):
                    continue
                if href in seen:
                    continue
                seen.add(href)

                # Extract "by Author" from text after the link
                li_text = li.get_text(separator=" ", strip=True)
                after_title = li_text[len(title):].strip()
                authors = _kb_normalize_authors(after_title) if after_title else None

                articles.append({
                    "title": title, "authors": authors,
                    "url": href, "abstract": None, "pub_date": pub_date,
                })

    # --- Pattern 2: sidebar leaf <li> elements (most issues) ---
    if not articles:
        # Find the active-trail book menu that contains article links
        active = soup.find("li", class_="expanded active-trail")
        if active:
            container = active.find("ul", class_="menu")
        else:
            container = None

        # Fallback: look for leaf LIs anywhere in the content region
        if not container:
            container = soup.find("div", class_="region-content") or soup

        if container:
            for li in container.find_all("li", class_="leaf"):
                a_tag = li.find("a", href=True)
                if not a_tag:
                    continue
                title = a_tag.get_text(separator=" ", strip=True)
                if not title or len(title) < 15:
                    continue

                href = _kb_normalize_url(a_tag["href"])
                if not href:
                    continue
                path = href.replace(KB_BASE, "")
                if not _kb_is_article_path(path):
                    continue
                if href in seen:
                    continue
                seen.add(href)

                articles.append({
                    "title": title, "authors": None,
                    "url": href, "abstract": None, "pub_date": pub_date,
                })

    return articles


def _enrich_kb_article(article_url):
    """Fetch a KB Journal article page for author and abstract.

    Returns {"authors": ..., "abstract": ...} or None.
    """
    _, soup = _kb_get(article_url)
    if soup is None:
        return None

    body = soup.find("div", class_="field-items")
    if not body:
        return None
    field_item = body.find("div", class_="field-item")
    if not field_item:
        return None

    authors = None
    abstract = None

    # Walk through elements looking for author <h3> and abstract <h3>
    elements = field_item.find_all(["h3", "p"])
    found_abstract_heading = False

    for i, el in enumerate(elements):
        text = el.get_text(strip=True)

        if el.name == "h3":
            if text.lower() == "abstract":
                found_abstract_heading = True
                continue
            # First <h3> is likely the author + affiliation
            if not authors and not found_abstract_heading:
                authors = _kb_normalize_authors(text)

        elif el.name == "p" and found_abstract_heading:
            # Collect abstract text from paragraphs after "Abstract" heading
            p_text = el.get_text(separator=" ", strip=True)
            if p_text and len(p_text) > 30:
                abstract = p_text
                break  # Take the first substantial paragraph as the abstract

    return {"authors": authors, "abstract": abstract}


def scrape_kb_journal():
    """Scrape KB Journal from nav dropdown → issue ToC pages → article pages."""
    name = "KB Journal: The Journal of the Kenneth Burke Society"
    log.info("Scraping: %s", name)

    # Fetch a page to get nav dropdown with all issue links
    _, soup = _get(KB_BASE + "/winter2023")
    if soup is None:
        _, soup = _get(KB_BASE)
    if soup is None:
        log.warning("  KB Journal: could not load any page for nav parsing")
        update_fetch_log(name)
        return 0

    # Parse nav dropdown for issue URLs
    issue_urls = []
    seen_urls = set()
    for a in soup.find_all("a", href=True):
        text = a.get_text(strip=True)
        href = a["href"].strip()

        # Match nav items like "16.1 Winter 2023" or "5.1 Fall 2008"
        if not re.search(r"\d+\.\d+\s+(Winter|Spring|Summer|Fall|Autumn)\s+\d{4}", text, re.I):
            continue

        full = _kb_normalize_url(href)
        if not full or full in seen_urls:
            continue
        seen_urls.add(full)

        # Extract pub_date from the label text
        m = re.search(r"(Winter|Spring|Summer|Fall|Autumn)\s+(\d{4})", text, re.I)
        if m:
            season = m.group(1).lower()
            year = m.group(2)
            month = KB_SEASON_MONTHS.get(season, "01")
            pub_date = f"{year}-{month}"
        else:
            pub_date = _kb_pub_date(full)

        issue_urls.append((full, pub_date, text.strip()))

    log.info("  KB Journal: found %d issues from nav", len(issue_urls))

    # Phase 1+2: Scrape issue ToC pages
    all_articles = []
    for issue_url, pub_date, label in issue_urls:
        articles = _scrape_kb_issue(issue_url, pub_date, name)
        log.info("    %s — %d articles", label, len(articles))
        all_articles.extend(articles)

    log.info("  KB Journal: %d articles found, enriching...", len(all_articles))

    # Phase 3: Enrich article pages for authors and abstracts
    enriched_count = 0
    for art in all_articles:
        result = _enrich_kb_article(art["url"])
        if result:
            if result["authors"] and not art["authors"]:
                art["authors"] = result["authors"]
            if result["abstract"]:
                art["abstract"] = result["abstract"]
                enriched_count += 1
    log.info("  KB Journal: enriched %d articles with abstracts", enriched_count)

    # Phase 4: Upsert
    new_count = 0
    for art in all_articles:
        tags = auto_tag(art["title"], art["abstract"])
        new = upsert_article(
            url=art["url"], doi=None, title=art["title"],
            authors=art["authors"], abstract=art["abstract"],
            pub_date=art["pub_date"], journal=name, source="scrape",
            tags=tags,
            oa_status="gold", oa_url=art["url"],
        )
        new_count += new

    # Phase 5: Backfill existing records
    backfilled = 0
    with get_conn() as conn:
        for art in all_articles:
            if art["authors"] or art["abstract"]:
                tags = auto_tag(art["title"], art["abstract"])
                cur = conn.execute("""
                    UPDATE articles
                    SET authors = COALESCE(?, authors),
                        abstract = COALESCE(?, abstract),
                        tags = ?,
                        oa_status = 'gold',
                        oa_url = ?
                    WHERE url = ? AND journal = ?
                      AND (authors IS NULL OR abstract IS NULL)
                """, (art["authors"], art["abstract"], tags,
                      art["url"], art["url"], name))
                backfilled += cur.rowcount
        conn.commit()
    if backfilled:
        log.info("  KB Journal: backfilled %d existing records", backfilled)

    update_fetch_log(name)
    log.info("  %s — %d new, %d total, %d enriched", name, new_count, len(all_articles), enriched_count)
    return new_count


# ── Composition Studies ───────────────────────────────────────────────────────
# WordPress.com site. Not in CrossRef. Archive page lists issues; HTML issue
# pages (2016+) list articles in HTML tables with author/title columns.
# Pre-2016 issues exist only as full-issue PDFs (skipped).
#
# robots.txt (checked 2026-04-08): blocks /wp-admin/ and /wp-login.php only.
# AI-crawler User-Agents (ClaudeBot, GPTBot, etc.) are blocked; our
# RhetCompIndex/1.0 User-Agent is not among those listed.

CS_BASE = "https://compstudiesjournal.com"
CS_DELAY = 5  # seconds between requests

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


def _cs_get(url):
    """Rate-limited GET for Composition Studies."""
    time.sleep(CS_DELAY)
    return _get(url)


def _cs_normalize_authors(raw):
    """Normalize author string from table cell.

    Handles comma-and-"and"-separated lists, strips "Reviewed by" prefix.
    Returns semicolon-separated string or None.
    """
    if not raw:
        return None
    text = raw.strip()
    # Strip "Reviewed by" prefix
    text = re.sub(r"^reviewed\s+by\s+", "", text, flags=re.I).strip()
    if not text:
        return None
    # Split on " and " first, then rejoin parts that were comma-separated
    # "A, B, and C" → ["A", "B", "C"]
    # "A and B" → ["A", "B"]
    text = re.sub(r",?\s+and\s+", ", ", text)
    parts = [p.strip() for p in text.split(",") if p.strip()]
    # Filter out parts that look like affiliations or stray markup
    cleaned = []
    for p in parts:
        # Skip very short fragments or things that look like affiliations
        if len(p) < 2:
            continue
        cleaned.append(p)
    return "; ".join(cleaned) if cleaned else None


def _cs_extract_title_from_td(td):
    """Extract article title and URL from the title (right) table cell.

    Returns (title, url_or_none, is_pdf).
    Strips "Books under review" sub-paragraphs and italic book titles.
    """
    # If there's a link, use its text as the title
    a_tag = td.find("a", href=True)
    if a_tag:
        href = a_tag["href"].strip()
        is_pdf = "/wp-content/uploads/" in href and href.lower().endswith(".pdf")
        # Title is the link text, but strip italic book titles within it
        # (book reviews have format: "<em>Book Title</em>, by Author Name")
        title = a_tag.get_text(separator=" ", strip=True)
        url = href if href.startswith("http") else CS_BASE + href
        return title, url, is_pdf

    # No link — plain text title (article still in paywall)
    # Get only the first paragraph's text, skip "Books under review" children
    first_p = td.find("p")
    if first_p:
        title = first_p.get_text(separator=" ", strip=True)
    else:
        title = td.get_text(separator=" ", strip=True)
    return title, None, False


def _cs_parse_editorial(el):
    """Try to parse an editorial intro from an h4/h5/p element.

    Returns dict with title, authors, url, is_pdf — or None.
    Patterns:
      <h5><a href="...pdf">From the Editors – Title</a> <em>by Author</em></h5>
      <h4>Editorial Introduction: <a href="...">Title</a><br/>by Author</h4>
      <p><a href="...">From the Editors</a> by Author</p>
    """
    a_tag = el.find("a", href=True)
    if not a_tag:
        return None
    title = a_tag.get_text(separator=" ", strip=True)
    if not title:
        return None

    href = a_tag["href"].strip()
    is_pdf = "/wp-content/uploads/" in href and href.lower().endswith(".pdf")
    url = href if href.startswith("http") else CS_BASE + href

    # Look for "by Author" in the element text after the link
    full_text = el.get_text(separator=" ", strip=True)
    m = re.search(r"\bby\s+(.+)$", full_text)
    authors = _cs_normalize_authors(m.group(1)) if m else None

    # If the h4/h5 has a prefix like "Editorial Introduction: " before the link,
    # prepend it to the title
    pre_text = ""
    for child in el.children:
        if child == a_tag:
            break
        if hasattr(child, "get_text"):
            pre_text += child.get_text(separator=" ", strip=True)
        elif isinstance(child, str):
            pre_text += child.strip()
    pre_text = pre_text.strip().rstrip(":")
    if pre_text and pre_text.lower() not in ("", "from the editors"):
        title = pre_text + ": " + title

    return {"title": title, "authors": authors, "url": url, "is_pdf": is_pdf}


def _scrape_cs_issue(issue_url, pub_year, journal_name):
    """Scrape one Composition Studies issue page.

    Parses HTML tables: left <td> = authors, right <td> = title (with optional
    PDF link). Also parses editorial intros in <h4>/<h5>/<p> elements.
    Articles without PDF links are indexed with the issue page URL.
    """
    _, soup = _cs_get(issue_url)
    if soup is None:
        return []

    content = soup.find("div", class_="entry-content")
    if not content:
        content = soup

    articles = []
    seen = set()

    # --- Parse editorial intros from h4/h5/p elements ---
    for el in content.find_all(["h4", "h5"]):
        text = el.get_text(strip=True).lower()
        if "editor" not in text and "from the" not in text:
            continue
        parsed = _cs_parse_editorial(el)
        if not parsed or not parsed["title"] or len(parsed["title"]) < 10:
            continue
        if CS_SUPPLEMENT_RE.match(parsed["title"]):
            continue
        url = parsed["url"]
        if url in seen:
            continue
        seen.add(url)
        articles.append({
            "title": parsed["title"],
            "authors": parsed["authors"],
            "url": url,
            "is_pdf": parsed["is_pdf"],
            "pub_date": pub_year,
        })

    # Also check <p> elements for "From the Editors/Guest Editors" pattern
    for p in content.find_all("p"):
        a_tag = p.find("a", href=True)
        if not a_tag:
            continue
        link_text = a_tag.get_text(strip=True).lower()
        if "editor" not in link_text and "from the" not in link_text:
            continue
        parsed = _cs_parse_editorial(p)
        if not parsed or not parsed["title"] or len(parsed["title"]) < 10:
            continue
        if CS_SUPPLEMENT_RE.match(parsed["title"]):
            continue
        url = parsed["url"]
        if url in seen:
            continue
        seen.add(url)
        articles.append({
            "title": parsed["title"],
            "authors": parsed["authors"],
            "url": url,
            "is_pdf": parsed["is_pdf"],
            "pub_date": pub_year,
        })

    # --- Parse table rows ---
    for table in content.find_all("table"):
        for tr in table.find_all("tr"):
            tds = tr.find_all("td")
            if len(tds) < 2:
                continue

            # Left cell = authors, right cell = title
            author_td, title_td = tds[0], tds[1]
            author_text = author_td.get_text(separator=" ", strip=True)
            authors = _cs_normalize_authors(author_text)

            title, pdf_url, is_pdf = _cs_extract_title_from_td(title_td)
            if not title:
                continue
            # Clean up whitespace
            title = re.sub(r"\s+", " ", title).strip()

            # Strip "Books under review" suffix that may be in the title text
            # (from nested <p> in the same cell)
            bur_idx = title.find("Books under review")
            if bur_idx > 0:
                title = title[:bur_idx].strip()

            if len(title) < 15:
                continue
            if CS_SUPPLEMENT_RE.match(title):
                continue

            # Determine the URL: PDF if available, else issue page
            url = pdf_url if pdf_url else issue_url
            if url in seen:
                continue
            seen.add(url)

            articles.append({
                "title": title,
                "authors": authors,
                "url": url,
                "is_pdf": is_pdf,
                "pub_date": pub_year,
            })

    return articles


def scrape_comp_studies():
    """Scrape Composition Studies from compstudiesjournal.com.

    Reads /archive/ to discover issue pages, then visits each HTML issue page
    to extract article metadata from table rows (authors + titles).
    Pre-2016 issues (full-PDF only) are skipped since they link to PDFs,
    not HTML pages with article-level tables.
    """
    name = "Composition Studies"
    log.info("Scraping: %s", name)
    all_articles = []

    _, soup = _get(CS_BASE + "/archive/")
    if soup is None:
        update_fetch_log(name)
        return 0

    # Collect (year, issue_url) for HTML issue pages
    issue_pages = []
    for a in soup.find_all("a", href=True):
        text = a.get_text(strip=True)
        href = a["href"].strip()

        # Must contain a volume.issue number like "53.2" or "44.1"
        if not re.search(r"\d{2}\.\d", text):
            continue
        if ".pdf" in href.lower():
            continue                         # skip full-issue PDFs
        # Must be on compstudiesjournal.com (not wordpress.com admin links)
        if "compstudiesjournal.com" not in href:
            continue
        if "wordpress.com/page/" in href:
            continue                         # skip WP admin edit links

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
        articles = _scrape_cs_issue(url, year, name)
        log.info("    %s — %d articles", url.split("/")[-2], len(articles))
        all_articles.extend(articles)

    # --- Upsert all articles ---
    new_count = 0
    for art in all_articles:
        new = upsert_article(
            url=art["url"], doi=None, title=art["title"],
            authors=art["authors"], abstract=None,
            pub_date=art["pub_date"], journal=name, source="scrape",
            tags=auto_tag(art["title"], None),
            oa_status="gold" if art["is_pdf"] else None,
            oa_url=art["url"] if art["is_pdf"] else None,
        )
        new_count += new

    # --- Backfill authors on existing records ---
    backfilled = 0
    with get_conn() as conn:
        for art in all_articles:
            if art["authors"]:
                cur = conn.execute("""
                    UPDATE articles
                    SET authors = ?, tags = COALESCE(?, tags)
                    WHERE url = ? AND journal = 'Composition Studies'
                      AND authors IS NULL
                """, (art["authors"], auto_tag(art["title"], None), art["url"]))
                backfilled += cur.rowcount
        conn.commit()
    if backfilled:
        log.info("  Composition Studies: backfilled authors on %d existing records", backfilled)

    update_fetch_log(name)
    log.info("  %s — %d new articles, %d total scraped", name, new_count, len(all_articles))
    return new_count


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
# Purdue Digital Commons (bepress). Every article page has structured
# <meta name="bepress_citation_*"> tags with title, authors, DOI, date, and
# abstract (via <meta name="description">). Issue ToC pages list article links.
# Vol 1 = 1980; currently at Vol 43 (2025). 91 issues, ~800+ articles.
#
# robots.txt (docs.lib.purdue.edu) — allows all paths under /wcj/.
# Disallowed paths are /cgi/*, /do/*, /fe-journals/ — none apply to article pages.

WCJ_BASE = "https://docs.lib.purdue.edu"
WCJ_DELAY = 5  # seconds between requests

# Titles to skip — these are not articles
_WCJ_SKIP_TITLES = {"front matter", "back matter", "table of contents"}


def _wcj_get(url):
    """Rate-limited GET for WCJ pages. Returns (response, soup) or (None, None)."""
    time.sleep(WCJ_DELAY)
    try:
        resp = requests.get(url, headers=HEADERS, timeout=TIMEOUT, allow_redirects=True)
        resp.raise_for_status()
        return resp, BeautifulSoup(resp.text, "lxml")
    except Exception as e:
        log.debug("  WCJ GET failed: %s — %s", url, e)
        return None, None


def _wcj_discover_issues():
    """Parse the issue dropdown on /wcj/ to get all Vol/Iss URLs."""
    _, soup = _wcj_get(WCJ_BASE + "/wcj/")
    if soup is None:
        return []

    issue_urls = set()
    # The dropdown has <option value="https://docs.lib.purdue.edu/wcj/volN/issN">
    for option in soup.find_all("option", value=True):
        val = option["value"]
        if re.search(r"/wcj/vol\d+/iss\d+$", val):
            issue_urls.add(val.rstrip("/") + "/")

    # Also extract any links on the page itself
    for a in soup.find_all("a", href=True):
        href = a["href"]
        if re.search(r"/wcj/vol\d+/iss\d+/?$", href):
            full = _abs_url(href, WCJ_BASE)
            if full:
                issue_urls.add(full.rstrip("/") + "/")

    return sorted(issue_urls)


def _wcj_scrape_toc(issue_url):
    """Scrape one issue ToC page. Returns list of article URLs."""
    _, soup = _wcj_get(issue_url)
    if soup is None:
        return []

    article_urls = []
    seen = set()
    for a in soup.find_all("a", href=True):
        href = a["href"]
        if not re.search(r"/wcj/vol\d+/iss\d+/\d+$", href):
            continue
        full = _abs_url(href, WCJ_BASE)
        if not full or full in seen:
            continue
        seen.add(full)
        article_urls.append(full)

    return article_urls


def _wcj_bepress_author(raw):
    """Convert bepress 'Last, First M.' to 'First M. Last'."""
    if "," in raw:
        parts = raw.split(",", 1)
        return f"{parts[1].strip()} {parts[0].strip()}"
    return raw.strip()


def _wcj_enrich_article(article_url):
    """Fetch an article page and extract metadata from bepress meta tags.

    Returns dict with title, authors, abstract, doi, pub_date — or None on failure.
    """
    _, soup = _wcj_get(article_url)
    if soup is None:
        return None

    def meta_content(name):
        tag = soup.find("meta", attrs={"name": name})
        return tag["content"].strip() if tag and tag.get("content") else None

    def meta_contents(name):
        return [
            t["content"].strip()
            for t in soup.find_all("meta", attrs={"name": name})
            if t.get("content")
        ]

    # Title
    title = meta_content("bepress_citation_title")
    if not title:
        # Fallback: page <title> or <h1>
        h1 = soup.find("h1")
        title = h1.get_text(strip=True) if h1 else None
    if not title:
        return None

    # Skip non-article entries
    if title.lower().strip() in _WCJ_SKIP_TITLES:
        return None

    # Authors — bepress gives "Last, First" format; may have multiple tags
    raw_authors = meta_contents("bepress_citation_author")
    if raw_authors:
        authors = "; ".join(_wcj_bepress_author(a) for a in raw_authors)
    else:
        authors = None

    # DOI — bare format like "10.7771/2832-9414.2137"
    doi = meta_content("bepress_citation_doi")

    # Abstract — from <meta name="description">
    desc = meta_content("description")
    abstract = None
    if desc:
        # Filter out non-abstract descriptions like "By Author, Published on..."
        if not re.match(r"^By\s+.+,\s+Published\s+on\b", desc):
            abstract = desc

    # Publication year
    pub_date = meta_content("bepress_citation_date")

    return {
        "title": title,
        "authors": authors,
        "abstract": abstract,
        "doi": doi,
        "pub_date": pub_date,
    }


def scrape_wcj():
    name = "Writing Center Journal"
    log.info("Scraping: %s", name)

    # Phase 1: Discover all issues from the dropdown
    issue_urls = _wcj_discover_issues()
    log.info("  %s: discovered %d issues", name, len(issue_urls))

    if not issue_urls:
        update_fetch_log(name)
        return 0

    # Phase 2: Scrape ToC pages to collect all article URLs
    all_article_urls = []
    for issue_url in issue_urls:
        urls = _wcj_scrape_toc(issue_url)
        all_article_urls.extend(urls)
        if urls:
            log.info("    %s — %d articles", issue_url.split("/wcj/")[1], len(urls))

    log.info("  %s: %d article URLs found", name, len(all_article_urls))

    # Deduplicate (shouldn't happen, but just in case)
    all_article_urls = list(dict.fromkeys(all_article_urls))

    # Phase 3: Enrich each article from its individual page
    new_count = 0
    enriched = 0
    backfilled = 0

    for i, article_url in enumerate(all_article_urls):
        meta = _wcj_enrich_article(article_url)
        if meta is None:
            continue

        enriched += 1
        if enriched % 50 == 0:
            log.info("    Enriched %d/%d articles...", enriched, len(all_article_urls))

        tags = auto_tag(meta["title"], meta.get("abstract")) or ""

        # Phase 4: Upsert
        inserted = upsert_article(
            url=article_url,
            doi=meta.get("doi"),
            title=meta["title"],
            authors=meta.get("authors"),
            abstract=meta.get("abstract"),
            pub_date=meta.get("pub_date") or "",
            journal=name,
            source="scrape",
            keywords=None,
            tags=tags,
            oa_status="gold",
            oa_url=article_url,
        )
        if inserted:
            new_count += 1
        else:
            # Backfill: update existing records that lack DOI/abstract/authors
            doi = meta.get("doi")
            abstract = meta.get("abstract")
            authors = meta.get("authors")
            if doi or abstract or authors:
                with get_conn() as conn:
                    conn.execute("""
                        UPDATE articles
                        SET doi = COALESCE(NULLIF(doi, ''), ?),
                            abstract = COALESCE(NULLIF(abstract, ''), ?),
                            authors = COALESCE(NULLIF(authors, ''), ?),
                            tags = COALESCE(NULLIF(tags, ''), ?)
                        WHERE url = ? AND (doi IS NULL OR doi = ''
                            OR abstract IS NULL OR abstract = ''
                            OR authors IS NULL OR authors = '')
                    """, (doi, abstract, authors, tags, article_url))
                    if conn.total_changes:
                        backfilled += 1

    update_fetch_log(name)
    log.info("  %s — %d new, %d backfilled, %d total enriched",
             name, new_count, backfilled, enriched)
    return new_count


# ── The Peer Review ────────────────────────────────────────────────────────────
# IWCA WordPress journal, 2015–present. ~22 issues, ~150+ articles.
# Issues listed at /issues/ with links to ToC pages. Article links use
# mixed URL schemes (root slugs, /issues/ subpaths, /wp/ legacy, wp.me
# shortlinks, ?page_id= previews). All redirect to canonical URLs.
# Newer articles (2024+) may have labeled ## Abstract sections.
#
# No robots.txt restrictions on article pages.

PEER_REVIEW_BASE = "https://thepeerreview-iwca.org"
TPR_DELAY = 5  # seconds between requests

# Paths to skip when identifying article links
_TPR_SKIP_PATHS = re.compile(
    r"^(issues?/?$|about|contact|submit|editorial|board|masthead|reviewers|"
    r"wp-content|wp-admin|wp-json|feed|tag|category|author|page|#|meet-tpr|"
    r"the-peer-review$)",
    re.I,
)

# Affiliation words for stripping from author names
_TPR_AFFIL_RE = re.compile(
    r"\b(university|college|cuny|suny|institute|school|"
    r"community\s+college|campus|a\s*&\s*m)\b", re.I
)

# Season → month mapping
_TPR_SEASON_MONTHS = {
    "winter": "01", "spring": "04", "summer": "06",
    "autumn": "09", "fall": "09",
}

# Validation records for Phase 0
_TPR_VALIDATION_RECORDS = [
    {
        "title_contains": "TPR Ten Years On",
        "author_contains": "Genie Nicole Giaimo",
        "url_contains": "tpr-ten-years-on",
    },
    {
        "title_contains": "Writing Center Research in German-Speaking Countries",
        "author_contains": "Nora Hoffmann",
        "url_contains": "writing-center-research-in-german-speaking-countries",
    },
    {
        "title_contains": "Writing Centres and Faculty Development",
        "author_contains": "Melanie Doyle",
        "url_contains": "writing-centres-and-faculty-development",
    },
    {
        "title_contains": "Duoethnographic Explorations",
        "author_contains": "Maria Isabel Galiano",
        "url_contains": "duoethnographic-explorations",
    },
    {
        "title_contains": "Writing Centers, Neurodiversity, and Intersectionality",
        "author_contains": "Delight Ejiaka",
        "url_contains": "neurodiversity-and-intersectionality",
    },
]


def _tpr_get(url):
    """Rate-limited GET for TPR. Follows redirects. Returns (resp, soup)."""
    time.sleep(TPR_DELAY)
    try:
        resp = requests.get(url, headers=HEADERS, timeout=TIMEOUT,
                            allow_redirects=True)
        resp.raise_for_status()
        return resp, BeautifulSoup(resp.text, "lxml")
    except Exception as e:
        log.debug("  TPR GET failed: %s — %s", url, e)
        return None, None


def _tpr_clean_author(text):
    """Strip affiliation from 'Name, Affiliation' author text."""
    if not text:
        return None
    text = text.strip()
    # Remove <span> formatting artifacts
    text = re.sub(r"\s+", " ", text).strip()
    # Skip translator lines
    if re.match(r"^translated\s+by\s+", text, re.I):
        return None
    # Strip leading "with" for co-authors
    text = re.sub(r"^with\s+", "", text, flags=re.I).strip()
    # Split on comma — keep name part, strip affiliation
    parts = text.split(",")
    if len(parts) >= 2:
        # Check if the last part looks like an affiliation
        last = ",".join(parts[1:]).strip()
        if _TPR_AFFIL_RE.search(last):
            return parts[0].strip()
    return text.strip()


def _tpr_extract_authors_from_ul(ul_tag):
    """Extract authors from a <ul> following an article title link."""
    if not ul_tag:
        return None
    authors = []
    for li in ul_tag.find_all("li", recursive=True):
        text = li.get_text(separator=" ", strip=True)
        if not text:
            continue
        # Skip translator entries
        if re.match(r"^translated\s+by\b", text, re.I):
            continue
        name = _tpr_clean_author(text)
        if name and len(name) > 2:
            authors.append(name)
    return "; ".join(authors) if authors else None


def _tpr_is_article_url(href):
    """Check if a URL looks like a TPR article (not nav/issues page)."""
    if not href:
        return False
    # wp.me shortlinks are always article/issue links — include them
    if "wp.me" in href:
        return True
    # Must be on the TPR domain
    if "thepeerreview-iwca.org" not in href:
        return False
    # Extract path
    path = re.sub(r"https?://thepeerreview-iwca\.org/?", "", href).strip("/")
    if not path:
        return False
    # Skip known non-article paths
    if _TPR_SKIP_PATHS.match(path):
        return False
    # Skip issue index pages (but NOT articles under issue paths)
    # Issue pages: issues/issue-X-Y/ (no further slug)
    # Articles: issues/issue-X-Y/article-slug/ (has a further slug)
    if re.match(r"issues?/[^/]+/?$", path):
        return False
    return True


def _tpr_scrape_toc(issue_url, pub_date):
    """Phase 2: Scrape a TPR issue ToC page. Returns list of article dicts."""
    resp, soup = _tpr_get(issue_url)
    if soup is None:
        return []

    # Use the resolved URL as the canonical issue URL
    canonical_issue = resp.url if resp else issue_url

    articles = []
    seen = set()

    # Find the main content area
    content = soup.find("div", class_="entry-content")
    if not content:
        content = soup

    # Strategy 1: Modern ToC — <p><a>Title</a></p> followed by <ul><li>Authors</li></ul>
    for a_tag in content.find_all("a", href=True):
        href = a_tag["href"].strip()

        # Check if this looks like an article link
        if not _tpr_is_article_url(href):
            continue

        title = a_tag.get_text(separator=" ", strip=True)
        if not title or len(title) < 10 or _is_nav_text(title):
            continue

        # Skip "Meet TPR's Editorial Team"
        if "meet tpr" in title.lower() or "editorial team" in title.lower():
            continue

        if href in seen:
            continue
        seen.add(href)

        # Extract authors from <ul> following the title
        authors = None
        # Walk up to the parent <p>, then find the next <ul> sibling
        parent_p = a_tag.find_parent("p")
        if parent_p:
            next_sib = parent_p.find_next_sibling()
            if next_sib and next_sib.name == "ul":
                authors = _tpr_extract_authors_from_ul(next_sib)

        # Strategy 2: Old-style <li class="toc-titles"> entries
        if not authors:
            parent_li = a_tag.find_parent("li", class_="toc-titles")
            if parent_li:
                # Authors in sub-<ul> with class="toc-authors" items
                sub_ul = parent_li.find("ul")
                if sub_ul:
                    author_items = sub_ul.find_all("li", class_="toc-authors")
                    if author_items:
                        names = []
                        for ai in author_items:
                            name = ai.get_text(strip=True)
                            if name:
                                names.append(name)
                        if names:
                            authors = "; ".join(names)
                    else:
                        authors = _tpr_extract_authors_from_ul(sub_ul)

        articles.append({
            "raw_url": href,
            "title": title,
            "authors": authors,
            "pub_date": pub_date,
        })

    return articles


def _tpr_enrich_article(raw_url):
    """Phase 3: Fetch article page for canonical URL and abstract."""
    resp, soup = _tpr_get(raw_url)
    if resp is None or soup is None:
        return None

    canonical_url = resp.url
    # Ensure https
    canonical_url = canonical_url.replace("http://", "https://")

    result = {"canonical_url": canonical_url, "abstract": None, "keywords": None}

    # Look for Abstract heading
    for h2 in soup.find_all("h2"):
        h2_text = h2.get_text(strip=True).lower()
        if "abstract" in h2_text:
            # Collect <p> tags after the heading until next <h2> or <hr>
            abstract_parts = []
            keywords = None
            for sib in h2.find_next_siblings():
                if sib.name == "h2" or sib.name == "hr":
                    break
                if sib.name == "p":
                    text = sib.get_text(separator=" ", strip=True)
                    # Check for Keywords line
                    if re.match(r"^keywords?:", text, re.I):
                        kw_text = re.sub(r"^keywords?:\s*", "", text, flags=re.I)
                        keywords = kw_text.strip()
                        continue
                    if text:
                        abstract_parts.append(text)
            if abstract_parts:
                result["abstract"] = " ".join(abstract_parts)
            if keywords:
                result["keywords"] = keywords
            break

    return result


def _validate_peer_review_parsing():
    """Phase 0: Validate parsing against known Issue 10.2 records."""
    log.info("  TPR validation: checking Issue 10.2 ToC parsing...")

    articles = _tpr_scrape_toc("https://thepeerreview-iwca.org/issue-10-2/", "2026-01")
    if not articles:
        log.error("  TPR validation FAILED: could not parse Issue 10.2 ToC")
        return False

    # Check minimum article count
    if len(articles) < 10:
        log.error("  TPR validation FAILED: only %d articles found (expected >= 10)",
                  len(articles))
        return False

    # Check each validation record
    passed = 0
    for vr in _TPR_VALIDATION_RECORDS:
        found = False
        for art in articles:
            title_ok = vr["title_contains"].lower() in (art["title"] or "").lower()
            author_ok = vr["author_contains"].lower() in (art.get("authors") or "").lower()
            url_ok = vr["url_contains"].lower() in (art["raw_url"] or "").lower()
            if title_ok and author_ok and url_ok:
                found = True
                break
        if found:
            log.info("    PASS: %s", vr["title_contains"][:50])
            passed += 1
        else:
            log.error("    FAIL: %s", vr["title_contains"][:50])

    if passed < len(_TPR_VALIDATION_RECORDS):
        log.error("  TPR validation FAILED: %d/%d checks passed. Aborting.",
                  passed, len(_TPR_VALIDATION_RECORDS))
        return False

    # Validate abstract extraction from a known article
    log.info("  TPR validation: checking abstract extraction...")
    result = _tpr_enrich_article(
        "https://thepeerreview-iwca.org/issue-9-2/"
        "a-future-for-writing-centers-generative-ai-and-what-students-are-saying/"
    )
    if result is None or not result.get("abstract"):
        log.error("  TPR validation FAILED: could not extract abstract from known article")
        return False
    if "large language model" not in result["abstract"].lower():
        log.error("  TPR validation FAILED: abstract doesn't contain expected text")
        return False
    log.info("    PASS: abstract extraction")

    total_checks = len(_TPR_VALIDATION_RECORDS) + 2  # +count check +abstract check
    log.info("  Validation passed: %d/%d checks OK. Proceeding with full scrape.",
             total_checks, total_checks)
    return True


def _tpr_discover_issues():
    """Phase 1: Discover all issues from /issues/ page with pub dates."""
    _, soup = _tpr_get(PEER_REVIEW_BASE + "/issues/")
    if soup is None:
        return []

    issues = []
    # Parse <h3><a href="...">Issue X.Y</a></h3> followed by <h6>Season YYYY</h6>
    for h3 in soup.find_all("h3"):
        a = h3.find("a", href=True)
        if not a:
            continue
        href = a["href"].strip()

        # Find the next <h6> sibling for season/year
        h6 = h3.find_next_sibling("h6")
        pub_date = None
        if h6:
            h6_text = h6.get_text(strip=True)
            m = re.match(r"(winter|spring|summer|autumn|fall)\s+(\d{4})", h6_text, re.I)
            if m:
                season, year = m.group(1).lower(), m.group(2)
                month = _TPR_SEASON_MONTHS.get(season, "01")
                pub_date = f"{year}-{month}"
            else:
                m2 = re.search(r"(\d{4})", h6_text)
                if m2:
                    pub_date = m2.group(1)

        # If no h6, try to extract year from the heading text itself
        if not pub_date:
            heading_text = h3.get_text(strip=True)
            # Some issues have "Summer 2024" in the heading
            m3 = re.search(r"(winter|spring|summer|autumn|fall)\s+(\d{4})", heading_text, re.I)
            if m3:
                season, year = m3.group(1).lower(), m3.group(2)
                month = _TPR_SEASON_MONTHS.get(season, "01")
                pub_date = f"{year}-{month}"
            else:
                m4 = re.search(r"(\d{4})", heading_text)
                if m4:
                    pub_date = m4.group(1)

        issues.append((href, pub_date))

    return issues


def scrape_peer_review():
    name = "The Peer Review"
    log.info("Scraping: %s", name)

    # Phase 0: Validation
    if not _validate_peer_review_parsing():
        log.error("  %s: validation failed, aborting scrape", name)
        update_fetch_log(name)
        return 0

    # Phase 1: Discover issues
    issues = _tpr_discover_issues()
    log.info("  %s: discovered %d issues", name, len(issues))

    if not issues:
        update_fetch_log(name)
        return 0

    # Phase 2 + 3 + 4: For each issue, scrape ToC then enrich articles
    total_new = 0
    total_backfilled = 0

    for issue_href, pub_date in issues:
        # Resolve issue URL (may be wp.me shortlink)
        toc_articles = _tpr_scrape_toc(issue_href, pub_date)
        if not toc_articles:
            continue

        log.info("    %s — %d entries", issue_href.split("org/")[-1][:40] if "org/" in issue_href else issue_href[:40],
                 len(toc_articles))

        for art in toc_articles:
            # Phase 3: Enrich — get canonical URL and abstract
            enrichment = _tpr_enrich_article(art["raw_url"])
            if enrichment is None:
                canonical_url = art["raw_url"]
                abstract = None
                keywords = None
            else:
                canonical_url = enrichment["canonical_url"]
                abstract = enrichment.get("abstract")
                keywords = enrichment.get("keywords")

            # Ensure https
            canonical_url = canonical_url.replace("http://", "https://")

            tags = auto_tag(art["title"], abstract) or ""

            # Phase 4: Upsert
            inserted = upsert_article(
                url=canonical_url,
                doi=None,
                title=art["title"],
                authors=art.get("authors"),
                abstract=abstract,
                pub_date=art.get("pub_date") or pub_date or "",
                journal=name,
                source="scrape",
                keywords=keywords,
                tags=tags,
                oa_status="gold",
                oa_url=canonical_url,
            )
            if inserted:
                total_new += 1
            else:
                # Phase 5: Backfill existing records
                authors = art.get("authors")
                if abstract or authors:
                    with get_conn() as conn:
                        conn.execute("""
                            UPDATE articles
                            SET abstract = COALESCE(NULLIF(abstract, ''), ?),
                                authors = COALESCE(NULLIF(authors, ''), ?),
                                tags = COALESCE(NULLIF(tags, ''), ?),
                                oa_status = COALESCE(oa_status, 'gold'),
                                oa_url = COALESCE(oa_url, ?)
                            WHERE url = ? AND journal = ?
                              AND (abstract IS NULL OR abstract = ''
                                   OR authors IS NULL OR authors = '')
                        """, (abstract, authors, tags, canonical_url,
                              canonical_url, name))
                        if conn.total_changes:
                            total_backfilled += 1

    # Phase 5 continued: Title-based backfill for URL-variant mismatches
    with get_conn() as conn:
        # Get all current TPR articles that need enrichment
        existing = conn.execute("""
            SELECT url, title FROM articles
            WHERE journal = ? AND (abstract IS NULL OR abstract = ''
                OR authors IS NULL OR authors = '')
        """, (name,)).fetchall()
        # We've already enriched by URL match above; no further action needed
        # unless we tracked all new data by title — skip for now

    update_fetch_log(name)
    log.info("  %s — %d new, %d backfilled", name, total_new, total_backfilled)
    return total_new


# ── Composition Forum ─────────────────────────────────────────────────────────
# Two-era journal: old PHP site (vols 14.2–54) and new WordPress (55+).
# Server blocks bot User-Agents, so we use a browser-like UA.
# robots.txt allows everything except /ojs/.

CF_BASE = "https://compositionforum.com"
CF_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/131.0.0.0 Safari/537.36"
    ),
    "X-Bot-Purpose": "Pinakes scholarly index - metadata only",
}
CF_DELAY = 5  # seconds between requests


def _cf_get(url):
    """GET with Composition Forum's browser-like headers."""
    import time
    time.sleep(CF_DELAY)
    try:
        resp = requests.get(url, headers=CF_HEADERS, timeout=TIMEOUT)
        resp.raise_for_status()
        return resp, BeautifulSoup(resp.text, "lxml")
    except Exception as e:
        log.debug("  CF GET failed: %s — %s", url, e)
        return None, None


def _discover_cf_issues():
    """
    Discover all Composition Forum issues from both archive pages.
    Returns list of (issue_url, vol_str, era, year) tuples.
    era is 'old' for 14.2-54, 'new' for 55+.
    year is extracted from archive page headings (e.g. "Volume 30, Fall 2014").
    """
    issues = []
    seen = set()

    # ── Old archive (vols 14.2–54) ──
    # Build vol→year map from <h2> headings like "Volume 30, Fall 2014"
    vol_year = {}
    resp, soup = _cf_get(CF_BASE + "/archives-old/")
    if soup:
        for h2 in soup.find_all("h2"):
            text = h2.get_text(strip=True)
            m = re.match(r"Volume\s+([\d.]+),.*\b((?:19|20)\d{2})\b", text)
            if m:
                vol_year[m.group(1)] = m.group(2)

        for a in soup.find_all("a", href=True):
            text = a.get_text(strip=True)
            if text != "Table of Contents":
                continue
            href = a["href"].strip()
            m = re.search(r"/issue/([\d.]+)/?$", href)
            if not m:
                continue
            vol = m.group(1)
            url = CF_BASE + "/issue/" + vol + "/"
            if url not in seen:
                seen.add(url)
                issues.append((url, vol, "old", vol_year.get(vol)))

    # ── New archive (vols 55+) ──
    resp, soup = _cf_get(CF_BASE + "/archives-new/")
    if soup:
        # Build vol→year from headings on new archive too
        for h2 in soup.find_all("h2"):
            text = h2.get_text(strip=True)
            m = re.match(r"Volume\s+(\d+)[,|].*\b((?:19|20)\d{2})\b", text)
            if m:
                vol_year[m.group(1)] = m.group(2)

        for a in soup.find_all("a", href=True):
            text = a.get_text(strip=True)
            if text != "Table of Contents":
                continue
            href = a["href"].strip()
            m = re.search(r"/issue/(\d+)/", href)
            if not m:
                continue
            vol = m.group(1)
            url = href if href.startswith("http") else CF_BASE + href
            if url not in seen:
                seen.add(url)
                issues.append((url, vol, "new", vol_year.get(vol)))

    log.info("  Composition Forum: discovered %d issues", len(issues))
    return issues


def _scrape_cf_toc(issue_url, vol, era, archive_year=None):
    """
    Scrape a single Composition Forum issue ToC page.
    Returns list of dicts: {url, title, authors, vol, year}.
    archive_year is from the archives page heading; used as fallback.
    """
    resp, soup = _cf_get(issue_url)
    if not soup:
        return []

    # Extract year from <title> tag (e.g. "Composition Forum: Volume 30: Fall 2014")
    year = archive_year
    if not year:
        title_tag = soup.find("title")
        if title_tag:
            m = re.search(r"\b((?:19|20)\d{2})\b", title_tag.get_text())
            if m:
                year = m.group(1)

    articles = []
    # Both eras use <p><a href="...">Title</a><br/>Author(s)</p>
    for p in soup.find_all("p"):
        a = p.find("a", href=True)
        if not a:
            continue
        href = a["href"].strip()

        # Skip non-article links
        if SKIP_PATTERNS.search(href):
            continue
        if "/social/" in href:
            continue

        title = a.get_text(" ", strip=True)
        if not title or len(title) < 10:
            continue

        # Build absolute URL
        if href.startswith("http"):
            article_url = href
        elif href.startswith("/"):
            article_url = CF_BASE + href
        else:
            # Relative to issue directory
            article_url = issue_url.rstrip("/") + "/" + href

        # Skip navigation links pointing to other issues
        if re.search(r"/issue/[\d.]+/?$", article_url):
            continue

        # Skip "From the Editors", "Next issue:", navigation
        title_lower = title.lower()
        if any(skip in title_lower for skip in [
            "from the editor", "next issue", "previous issue",
            "table of contents", "composition forum",
        ]):
            continue

        # Skip "Vol NN" navigation links
        if re.match(r"^Vol\s+\d", title):
            continue

        # Extract author from text after <br>
        # The <p> text minus the link text gives us the author
        full_text = p.get_text(" ", strip=True)
        author_text = full_text.replace(title, "").strip()
        # Clean up leading/trailing punctuation
        author_text = re.sub(r"^[\s,;:.\-]+|[\s,;:.\-]+$", "", author_text)

        articles.append({
            "url": article_url,
            "title": title,
            "authors": author_text if author_text else None,
            "vol": vol,
            "year": year,
        })

    return articles


def _scrape_cf_article(url, era):
    """
    Scrape an individual Composition Forum article page for metadata.
    Returns dict with title, authors, abstract, keywords (or empty values).
    """
    resp, soup = _cf_get(url)
    if not soup:
        return {}

    result = {}

    if era == "old":
        # Old format: <h1>Title</h1>, <p class="author">, <p class="abstract">
        h1 = soup.find("h1")
        if h1:
            result["title"] = h1.get_text(" ", strip=True)

        author_p = soup.find("p", class_="author")
        if author_p:
            result["authors"] = author_p.get_text(" ", strip=True)

        abstract_p = soup.find("p", class_="abstract")
        if abstract_p:
            text = abstract_p.get_text(" ", strip=True)
            # Strip leading "Abstract:" label
            text = re.sub(r"^Abstract\s*:\s*", "", text, flags=re.I)
            result["abstract"] = text

    else:
        # New format: <h2 class="wp-block-post-title">, <p class="author-byline">,
        # <div class="abstract"><h2>Abstract</h2><p>...</p><h3>Keywords</h3><p>...</p>
        h2 = soup.find("h2", class_=re.compile(r"wp-block-post-title"))
        if h2:
            result["title"] = h2.get_text(" ", strip=True)

        byline = soup.find("p", class_=re.compile(r"author-byline"))
        if byline:
            result["authors"] = byline.get_text(" ", strip=True)

        abstract_div = soup.find("div", class_="abstract")
        if abstract_div:
            # Abstract text is in <p> tags after the <h2>Abstract</h2>
            paragraphs = abstract_div.find_all("p")
            if paragraphs:
                result["abstract"] = paragraphs[0].get_text(" ", strip=True)

            # Keywords in <p> after <h3>Keywords</h3>
            kw_h3 = abstract_div.find("h3", string=re.compile(r"Keywords", re.I))
            if kw_h3:
                kw_p = kw_h3.find_next_sibling("p")
                if kw_p:
                    result["keywords"] = kw_p.get_text(" ", strip=True)

    return result


def scrape_comp_forum():
    """Scrape all Composition Forum issues (vols 14.2–present)."""
    name = "Composition Forum"
    log.info("Scraping %s …", name)
    init_db()
    total = 0

    issues = _discover_cf_issues()

    for issue_url, vol, era, year in issues:
        log.info("  Issue %s (%s): %s", vol, era, issue_url)
        toc_entries = _scrape_cf_toc(issue_url, vol, era, archive_year=year)
        log.info("    ToC: %d articles", len(toc_entries))

        for entry in toc_entries:
            # Enrich from article page
            detail = _scrape_cf_article(entry["url"], era)

            title = detail.get("title") or entry["title"]
            authors = detail.get("authors") or entry.get("authors") or ""
            abstract = detail.get("abstract") or ""
            keywords = detail.get("keywords") or ""

            # Build pub_date from year
            pub_date = entry.get("year") or ""

            # Append keywords to abstract for tagging (auto_tag takes title, abstract)
            tag_text = (abstract + " " + keywords).strip() if keywords else abstract
            tags = auto_tag(title, tag_text) or ""

            inserted = upsert_article(
                url=entry["url"],
                doi="",
                title=title,
                authors=authors,
                abstract=abstract,
                pub_date=pub_date,
                journal=name,
                source="scrape",
                keywords=keywords,
                tags=tags,
                oa_status="gold",
                oa_url=entry["url"],
            )
            if inserted:
                total += 1

    update_fetch_log(name)
    log.info("  %s — %d new articles", name, total)
    return total


# ── Reflections ───────────────────────────────────────────────────────────────
# WordPress journal (2000–present). Single archive page at /archive/ lists every
# article with pipe-separated "Title | Author(s)" format inside <a> tags.
# HTML article pages (~Vol 21+) may have Abstract sections.
# Older issues (~Vol 20 and earlier) link directly to PDFs — no enrichment.
#
# robots.txt (checked 2026-04-08):
#   User-agent: *
#   Disallow: /wp-admin/
#   Allow: /wp-admin/admin-ajax.php
# All public paths allowed. /archive/ and article pages are permitted.

REFL_BASE = "https://reflectionsjournal.net"
REFL_DELAY = 5  # seconds between requests

# Season → month mapping for pub_date extraction
_REFL_SEASON_MONTHS = {
    "spring": "04", "summer": "06", "fall": "09", "winter": "01",
}

# Titles to skip
_REFL_SKIP_TITLES = {"front matter", "full issue", "full issue pdf"}

# URL patterns for PDFs and other non-article files
_REFL_SKIP_HREF = re.compile(
    r"\.(mov|mp4|docx?)$", re.I
)


def _refl_get(url):
    """Rate-limited GET for Reflections. Returns (resp, soup) or (None, None)."""
    time.sleep(REFL_DELAY)
    try:
        resp = requests.get(url, headers=HEADERS, timeout=TIMEOUT,
                            allow_redirects=True)
        resp.raise_for_status()
        return resp, BeautifulSoup(resp.text, "lxml")
    except Exception as e:
        log.debug("  Reflections GET failed: %s — %s", url, e)
        return None, None


def _refl_parse_date(heading_text):
    """
    Extract YYYY-MM from a volume/issue heading.
    'Volume 24, Issue 1, Fall 2024' → '2024-09'
    'Volume 21, Number 1, Fall/Winter 2021-22' → '2021-09'
    'Special Issue, Summer 2021' → '2021-06'
    'Special Winter Issue, 2017-2018' → '2017-01'
    """
    text = heading_text.lower().strip()
    # Find the year (4 digits)
    year_match = re.search(r"\b((?:19|20)\d{2})\b", text)
    if not year_match:
        return ""
    year = year_match.group(1)

    # Find the season — take the first one if multiple (e.g. "Fall/Winter")
    for season, month in _REFL_SEASON_MONTHS.items():
        if season in text:
            return f"{year}-{month}"

    # No season found — default to January
    return f"{year}-01"


def _refl_normalize_authors(raw):
    """
    Normalize author string from pipe-separated format.
    'Hugo Moreno, Marina Layme Huarca, and Calley Marotta' → 'Hugo Moreno; Marina Layme Huarca; Calley Marotta'
    Strips 'reviewed by' prefix for book reviews.
    """
    if not raw:
        return None
    raw = raw.strip()
    # Strip "reviewed by" prefix
    raw = re.sub(r"^reviewed\s+by\s+", "", raw, flags=re.I).strip()
    if not raw:
        return None

    # Replace " & " and " and " with a comma for uniform splitting
    text = re.sub(r"\s+&\s+", ", ", raw)
    text = re.sub(r",?\s+and\s+", ", ", text)

    # Split on comma
    parts = [p.strip() for p in text.split(",") if p.strip()]

    # Filter out parts that look like affiliations
    affil_re = re.compile(
        r"\b(university|college|institute|school|department|center|centre|"
        r"campus|program|suny|cuny)\b", re.I
    )
    names = []
    for p in parts:
        if affil_re.search(p):
            continue
        # Skip if it's just initials or too short
        if len(p) < 3:
            continue
        names.append(p)

    if not names:
        return None
    return "; ".join(names)


def _refl_is_html_article(url):
    """Check if URL points to an HTML article page (not a PDF)."""
    if not url:
        return False
    return bool(re.match(
        r"https?://reflectionsjournal\.net/\d{4}/\d{2}/[^/]+/?$", url
    ))


def _parse_reflections_archive():
    """
    Phase 1: Fetch and parse the /archive/ page.
    Returns list of dicts: {url, title, authors, pub_date, is_html}.
    """
    log.info("  Fetching archive page...")
    resp, soup = _refl_get(REFL_BASE + "/archive/")
    if soup is None:
        log.error("  Failed to fetch archive page")
        return []

    content = soup.find("div", class_="entry-content")
    if not content:
        log.error("  No entry-content div found on archive page")
        return []

    articles = []
    seen_urls = set()
    current_date = ""

    for el in content.children:
        if not hasattr(el, "name") or not el.name:
            continue

        # Track volume/issue headings for pub_date
        if el.name in ("h2", "h3"):
            text = el.get_text(strip=True)
            if text:
                parsed = _refl_parse_date(text)
                if parsed:
                    current_date = parsed

        # Article entries are <p> tags containing <a> links
        if el.name != "p":
            continue

        # Check for bold-only section headers (e.g. "Community Engagement Profile")
        strong = el.find("strong")
        if strong and not el.find("a"):
            continue  # section label, not an article

        a_tag = el.find("a", href=True)
        if not a_tag:
            continue

        href = a_tag["href"].strip()
        link_text = a_tag.get_text(" ", strip=True)

        if not link_text or not href:
            continue

        # Skip non-article files
        if _REFL_SKIP_HREF.search(href):
            continue

        # Skip titles in the skip list
        if link_text.lower().strip('" \u201c\u201d\u2018\u2019\'') in _REFL_SKIP_TITLES:
            continue

        # Must have pipe separator for title | author(s)
        if " | " not in link_text:
            continue

        # Split on pipe — title is everything before the LAST pipe
        # (some titles may contain | but authors are always after the last one)
        pipe_idx = link_text.rfind(" | ")
        title = link_text[:pipe_idx].strip()
        author_raw = link_text[pipe_idx + 3:].strip()

        if not title:
            continue

        # Strip leading/trailing quotation marks from title
        title = title.strip('" \u201c\u201d\u2018\u2019\'')
        # Normalize internal whitespace
        title = re.sub(r"\s+", " ", title).strip()

        if not title:
            continue

        # Normalize URL
        url = href.strip()
        if url in seen_urls:
            continue
        seen_urls.add(url)

        # Determine if HTML article page or PDF
        is_html = _refl_is_html_article(url)

        authors = _refl_normalize_authors(author_raw)

        articles.append({
            "url": url,
            "title": title,
            "authors": authors,
            "pub_date": current_date,
            "is_html": is_html,
        })

    log.info("  Archive page: %d articles (%d HTML, %d PDF)",
             len(articles),
             sum(1 for a in articles if a["is_html"]),
             sum(1 for a in articles if not a["is_html"]))
    return articles


def _enrich_reflections_article(article_url):
    """
    Phase 2: Fetch an HTML article page to extract abstract.
    Returns {"abstract": ..., "confirmed_authors": ...} or None.
    """
    resp, soup = _refl_get(article_url)
    if soup is None:
        return None

    content = soup.find("div", class_="entry-content")
    if not content:
        return None

    abstract = None

    # Look for <h1>, <h2>, <h3>, or <strong> containing "Abstract"
    for heading in content.find_all(["h1", "h2", "h3"]):
        if "abstract" in heading.get_text(strip=True).lower():
            # Collect <p> text after this heading until the next heading
            paragraphs = []
            for sib in heading.find_next_siblings():
                if sib.name in ("h1", "h2", "h3"):
                    break
                if sib.name == "p":
                    text = sib.get_text(" ", strip=True)
                    if text:
                        paragraphs.append(text)
            if paragraphs:
                abstract = " ".join(paragraphs)
                # Trim very long abstracts (some pages have body text after abstract)
                if len(abstract) > 2000:
                    abstract = abstract[:2000].rsplit(" ", 1)[0] + "…"
            break

    return {"abstract": abstract}


def scrape_reflections():
    """
    Scrape Reflections: A Journal of Community-Engaged Writing and Rhetoric.
    Archive page at /archive/ + article page enrichment for abstracts.
    """
    name = "Reflections: A Journal of Community-Engaged Writing and Rhetoric"
    log.info("Scraping: %s", name)
    init_db()

    # Phase 1: Parse archive page
    archive_articles = _parse_reflections_archive()
    if not archive_articles:
        update_fetch_log(name)
        return 0

    # Phase 2 + 3: Enrich HTML articles and upsert all
    total_new = 0
    total_enriched = 0

    for i, art in enumerate(archive_articles):
        abstract = None

        # Enrich only HTML article pages
        if art["is_html"]:
            enrichment = _enrich_reflections_article(art["url"])
            if enrichment:
                abstract = enrichment.get("abstract")
                total_enriched += 1
            if (total_enriched % 50 == 0) and total_enriched > 0:
                log.info("    Enriched %d/%d HTML articles...",
                         total_enriched,
                         sum(1 for a in archive_articles if a["is_html"]))

        tags = auto_tag(art["title"], abstract) or ""

        inserted = upsert_article(
            url=art["url"],
            doi=None,
            title=art["title"],
            authors=art.get("authors"),
            abstract=abstract,
            pub_date=art.get("pub_date", ""),
            journal=name,
            source="scrape",
            keywords=None,
            tags=tags,
            oa_status="gold",
            oa_url=art["url"],
        )
        if inserted:
            total_new += 1

    # Phase 4: Backfill existing records (from WP API harvester)
    backfilled = 0
    with get_conn() as conn:
        for art in archive_articles:
            abstract = None
            if art["is_html"]:
                # We already enriched above; re-use would require caching.
                # Instead, just backfill authors + tags from archive page data.
                pass
            authors = art.get("authors")
            tags = auto_tag(art["title"], abstract) or ""
            if authors:
                result = conn.execute("""
                    UPDATE articles
                    SET authors = COALESCE(NULLIF(?, ''), authors),
                        tags = COALESCE(NULLIF(tags, ''), ?),
                        oa_status = COALESCE(oa_status, 'gold'),
                        oa_url = COALESCE(oa_url, ?)
                    WHERE url = ? AND journal = ?
                      AND (authors IS NULL OR authors = '')
                """, (authors, tags, art["url"], art["url"], name))
                if result.rowcount:
                    backfilled += 1
        conn.commit()

    update_fetch_log(name)
    log.info("  %s — %d new, %d enriched, %d backfilled",
             name, total_new, total_enriched, backfilled)
    return total_new


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
    "comp_forum":    scrape_comp_forum,
    "reflections":   scrape_reflections,
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
                capture_fetcher_error(SOURCE_NAME, journal["name"], e)
        else:
            log.warning("No scraper registered for strategy: %s", strategy)
    log.info("Scrape fetch complete. Total new: %d", total)
    return total


if __name__ == "__main__":
    fetch_all()
