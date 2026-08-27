"""panel_check.py — drive every analytical panel in a real browser.

    python -m tools.panel_check
    python -m tools.panel_check --page explore --headed
    python -m tools.panel_check --admin-token "$env:PINAKES_ADMIN_TOKEN"

This is the layer that answers the question the HTTP crawler cannot: *does the
tool actually draw?*

`tools/smoke_crawl.py` proves each API endpoint returns well-formed, non-empty
JSON. That is necessary and not sufficient. Every tool on this site is a
JavaScript panel that fetches one of those endpoints and renders it, and the
panels are hash-routed inside three pages — `/explore`, `/wac`, `/datastories`.
A fragment never reaches the server, so an HTTP crawl of `/explore` returns 200
whether all eighteen panels work or none of them do. A renderer that throws on
a field rename, a D3 module that fails to import, a chart that silently draws
into a zero-height container: all invisible to a status-code crawl, all
user-visible breakage.

The panel list is parsed out of the templates, not hand-maintained:

    /explore       .explore-tab[data-hash]      18 panels
    /wac           .wac-card[data-viz]          24 panels
    /datastories   [data-hash]                  25 panels

Add a panel to a template and it gets checked. Nothing to remember.

Each panel is classified into one of four outcomes, because "no chart" has two
very different causes:

    DREW     a chart rendered with real marks
    EMPTY    the panel rendered an explicit "No data." state - correct
             behaviour on an empty index, suspicious on a populated one
    BLANK    nothing rendered and nothing explained - the broken case
    ERROR    a console error or a failed fetch while the panel loaded

Requires Playwright:

    pip install -r requirements-browser.txt
    python -m playwright install chromium
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
from dataclasses import dataclass, field, asdict
from pathlib import Path

DEFAULT_BASE_URL = "https://testpinakes.wacclearinghouse.org"
REPO_ROOT = Path(__file__).resolve().parent.parent

DREW, EMPTY, BLANK, ERROR, SKIP = "DREW", "EMPTY", "BLANK", "ERROR", "SKIP"

# A chart with fewer marks than this is not a chart. Axes and a legend alone
# can produce a handful of elements on an empty plot, so the floor sits above
# incidental furniture rather than at zero.
MIN_MARKS = 6

# Text a panel shows when it has nothing to draw. Sourced from the renderers in
# static/js/viz/ and static/js/wac/.
EMPTY_STATE_PATTERNS = [
    r"\bno data\b",
    r"\bnot enough\b",
    r"no results",
    r"nothing to show",
    r"no articles",
    r"try lowering",
    r"try a different",
]

# Console noise that is not a panel fault.
IGNORABLE_CONSOLE = [
    r"favicon",
    r"Failed to load resource.*404.*favicon",
    r"\[HMR\]",
    r"DevTools",
    r"Download the React DevTools",
    r"was preloaded using link preload",
]

# Console messages that are properties of the page, not of the panel that
# happened to be active when they fired. Attributing a Content-Security-Policy
# violation to a panel would fail all 26 Datastories panels for one header.
SITE_LEVEL_CONSOLE = [
    (r"Content Security Policy", "csp-violation",
     "A Content-Security-Policy directive is blocking a request the page makes. "
     "The script loads but its network call does not, so the feature fails "
     "silently in every browser."),
]

# Pages that render nothing useful without a credential. Without one, their
# panels are skipped rather than reported as missing markup.
GATED_PAGES = {
    "/datastories": "Datastories is password-gated; pass --admin-token to check its panels",
}


@dataclass
class Panel:
    page: str            # "/explore"
    slug: str            # "timeline" — the URL hash, or the data-viz value
    activation: str      # "hash" | "scroll"
    selector: str        # CSS selector for the card, when activation is scroll
    container: str       # CSS selector for the element the panel renders into


@dataclass
class PanelResult:
    page: str
    slug: str
    status: str = DREW
    marks: int = 0
    svg_count: int = 0
    elapsed_ms: int = 0
    notes: list[str] = field(default_factory=list)
    console_errors: list[str] = field(default_factory=list)
    failed_requests: list[str] = field(default_factory=list)
    screenshot: str | None = None


# ── Panel registry, parsed from the templates ───────────────────────────────

def _read_template(name: str) -> str:
    path = REPO_ROOT / "templates" / name
    if not path.exists():
        raise SystemExit(
            f"Template not found: {path}\n"
            "panel_check parses the panel list out of the repository's templates, "
            "so it must run from a clone of the repo."
        )
    return path.read_text(encoding="utf-8")


def discover_panels(pages: set[str] | None = None) -> list[Panel]:
    """Parse the panel registry out of the templates.

    Deliberately reads the local templates rather than scraping the live page:
    the check should be driven by what the code says should exist, so that a
    panel which fails to render at all is still checked instead of silently
    vanishing from the list.
    """
    panels: list[Panel] = []

    # The URL hash and the panel's DOM id are NOT the same string:
    #   <button onclick="showTab('sleepers', this)" data-hash="sleeping-beauties">
    # activates #tab-sleepers. Measuring the wrong element is how every panel
    # reads as blank, so the mapping is parsed rather than assumed.
    tab_button = re.compile(
        r"""showTab\(\s*['"]([A-Za-z0-9_-]+)['"]\s*,\s*this\s*\)[^>]*?data-hash=["']([^"']+)["']"""
    )

    def _hash_page(path: str, template: str) -> list[Panel]:
        html = _read_template(template)
        found = []
        for tab, slug in tab_button.findall(html):
            found.append(Panel(path, slug, "hash", "", f"#tab-{tab}"))
        if not found:
            raise SystemExit(
                f"No panels parsed from templates/{template}. The tab markup has "
                "changed shape; update the tab_button pattern in discover_panels "
                "rather than letting the check silently cover nothing."
            )
        # Dedupe: each panel appears as both an accordion card and a tab button.
        seen, unique = set(), []
        for panel in found:
            if panel.slug in seen:
                continue
            seen.add(panel.slug)
            unique.append(panel)
        return unique

    if pages is None or "explore" in pages:
        panels += _hash_page("/explore", "explore.html")

    # /wac renders each card lazily through an IntersectionObserver, so the
    # only way to trigger one is to put it on screen. The card is both the
    # trigger and the container.
    if pages is None or "wac" in pages:
        html = _read_template("wac.html")
        for slug in dict.fromkeys(re.findall(r'data-viz="([^"]+)"', html)):
            selector = f'.wac-card[data-viz="{slug}"]'
            panels.append(Panel("/wac", slug, "scroll", selector, selector))

    if pages is None or "datastories" in pages:
        panels += _hash_page("/datastories", "datastories.html")

    return panels


# ── In-page measurement ─────────────────────────────────────────────────────
# Runs inside the browser. Measures what a reader would actually see: visible
# drawing surfaces, how many marks are in them, and whether the panel said
# anything about having no data.

_MEASURE_JS = r"""
([containerSel, minMarks]) => {
  const scope = document.querySelector(containerSel);
  if (!scope) return { missing: true, marks: 0, svgCount: 0, text: '' };

  const style = getComputedStyle(scope);
  if (style.display === 'none' || style.visibility === 'hidden') {
    return { hidden: true, marks: 0, svgCount: 0, text: '' };
  }

  // Dimensions are read off getBoundingClientRect, which reports real size for
  // elements below the fold too — panels routinely render off-screen and are
  // no less rendered for it.
  const drawn = (el) => {
    const r = el.getBoundingClientRect();
    if (r.width < 2 || r.height < 2) return false;
    const s = getComputedStyle(el);
    return s.display !== 'none' && s.visibility !== 'hidden' && s.opacity !== '0';
  };

  let marks = 0, svgCount = 0;
  const MARK_SEL = 'path,rect,circle,line,polygon,polyline,ellipse,text';
  for (const el of scope.querySelectorAll('svg, canvas')) {
    if (!drawn(el)) continue;
    svgCount++;
    if (el.tagName === 'CANVAS') { marks += minMarks; continue; }
    marks += el.querySelectorAll(MARK_SEL).length;
  }
  // A table is a legitimate rendering for several WAC panels, not a fallback.
  for (const t of scope.querySelectorAll('table')) {
    if (drawn(t)) marks += t.querySelectorAll('tbody tr').length;
  }

  return {
    marks,
    svgCount,
    height: Math.round(scope.getBoundingClientRect().height),
    text: (scope.innerText || '').replace(/\s+/g, ' ').slice(0, 4000),
  };
}
"""


def _is_ignorable(message: str) -> bool:
    return any(re.search(p, message, re.I) for p in IGNORABLE_CONSOLE)


def _looks_empty(text: str) -> str | None:
    for pattern in EMPTY_STATE_PATTERNS:
        match = re.search(pattern, text, re.I)
        if match:
            start = max(0, match.start() - 40)
            return text[start:match.end() + 60].strip().replace("\n", " ")
    return None


# ── Driver ──────────────────────────────────────────────────────────────────

class PanelChecker:

    def __init__(self, args):
        self.base = args.base_url.rstrip("/")
        self.timeout = args.timeout * 1000
        self.settle_ms = args.settle
        self.admin_token = args.admin_token
        self.screenshot_dir = Path(args.screenshot_dir) if args.screenshot_dir else None
        self.headed = args.headed
        self.index_is_empty = False
        self.results: list[PanelResult] = []
        self.site_findings: dict[str, dict] = {}
        if self.screenshot_dir:
            self.screenshot_dir.mkdir(parents=True, exist_ok=True)

    def note_site_finding(self, key: str, message: str, example: str) -> None:
        finding = self.site_findings.setdefault(
            key, {"message": message, "count": 0, "examples": []})
        finding["count"] += 1
        if example and len(finding["examples"]) < 2:
            finding["examples"].append(example[:200])

    # -- per-page listeners --------------------------------------------------

    def _attach_listeners(self, page, bucket):
        def on_console(msg):
            if msg.type != "error" or _is_ignorable(msg.text):
                return
            for pattern, key, description in SITE_LEVEL_CONSOLE:
                if re.search(pattern, msg.text, re.I):
                    self.note_site_finding(key, description, msg.text)
                    return
            bucket["console"].append(msg.text[:300])

        def on_pageerror(exc):
            bucket["console"].append(f"uncaught: {str(exc)[:300]}")

        def on_response(response):
            if response.status >= 400 and not _is_ignorable(response.url):
                bucket["failed"].append(f"{response.status} {response.url[:160]}")

        def on_requestfailed(request):
            if not _is_ignorable(request.url):
                bucket["failed"].append(f"failed {request.url[:160]}")

        def on_request(request):
            if "/api/" in request.url:
                bucket["last_api"] = time.monotonic()

        page.on("console", on_console)
        page.on("pageerror", on_pageerror)
        page.on("response", on_response)
        page.on("requestfailed", on_requestfailed)
        page.on("request", on_request)

    def _wait_for_settle(self, bucket, budget_ms=None):
        """Wait until the panel has stopped fetching.

        networkidle is unreliable here: several panels keep a connection warm,
        and one that fetches nothing at all would make a fixed wait pure delay.
        Instead, wait for a quiet period after the last /api/ request.
        """
        budget = (budget_ms or self.timeout) / 1000.0
        deadline = time.monotonic() + budget
        quiet = self.settle_ms / 1000.0
        while time.monotonic() < deadline:
            last = bucket.get("last_api")
            if last is None:
                # Nothing fetched yet; give it a moment to start.
                if time.monotonic() > bucket["started"] + max(quiet, 1.0):
                    return
            elif time.monotonic() - last > quiet:
                return
            time.sleep(0.1)

    # -- one panel -----------------------------------------------------------

    def check_panel(self, page, panel: Panel, bucket) -> PanelResult:
        result = PanelResult(page=panel.page, slug=panel.slug)
        bucket["console"].clear()
        bucket["failed"].clear()
        bucket["last_api"] = None
        bucket["started"] = time.monotonic()
        started = time.monotonic()

        try:
            if panel.activation == "hash":
                # Setting location.hash fires hashchange, which the loaders
                # listen for. Far cheaper than reloading a 400 KB page per
                # panel, and it is the same code path a sidebar link takes.
                current = page.evaluate("() => location.pathname")
                if current != panel.page:
                    page.goto(f"{self.base}{panel.page}#{panel.slug}",
                              wait_until="domcontentloaded", timeout=self.timeout)
                else:
                    page.evaluate("(h) => { location.hash = h; }", panel.slug)
            else:
                card = page.locator(panel.selector).first
                if card.count() == 0:
                    result.status = SKIP
                    result.notes.append("card not present in the rendered page")
                    return result
                card.scroll_into_view_if_needed(timeout=self.timeout)

            self._wait_for_settle(bucket)
            measured = page.evaluate(_MEASURE_JS, [panel.container, MIN_MARKS])
        except Exception as exc:
            result.status = ERROR
            result.notes.append(f"driver error: {type(exc).__name__}: {str(exc)[:200]}")
            result.elapsed_ms = int((time.monotonic() - started) * 1000)
            return result

        result.elapsed_ms = int((time.monotonic() - started) * 1000)
        result.marks = measured.get("marks", 0)
        result.svg_count = measured.get("svgCount", 0)
        result.console_errors = list(bucket["console"])
        result.failed_requests = list(bucket["failed"])

        if measured.get("missing"):
            result.status = ERROR
            result.notes.append(
                f"container {panel.container} is not in the DOM — the panel's markup "
                "moved, or the tab failed to activate"
            )
            return result
        if measured.get("hidden"):
            result.status = ERROR
            result.notes.append(
                f"container {panel.container} is hidden — activating #{panel.slug} "
                "did not reveal its panel"
            )
            return result

        if result.console_errors or result.failed_requests:
            result.status = ERROR
            for message in result.console_errors[:3]:
                result.notes.append(f"console: {message}")
            for message in result.failed_requests[:3]:
                result.notes.append(f"request: {message}")
        elif result.marks >= MIN_MARKS:
            result.status = DREW
            result.notes.append(f"{result.marks} marks in {result.svg_count} drawing surface(s)")
        else:
            empty_message = _looks_empty(measured.get("text", ""))
            if empty_message:
                result.status = EMPTY
                result.notes.append(f'explicit empty state: "{empty_message[:100]}"')
            else:
                result.status = BLANK
                result.notes.append(
                    f"nothing rendered and nothing explained "
                    f"({result.marks} marks, {result.svg_count} surfaces)"
                )

        if self.screenshot_dir and result.status in (BLANK, ERROR):
            name = f"{panel.page.strip('/').replace('/', '-')}-{panel.slug}.png"
            target = self.screenshot_dir / name
            try:
                page.screenshot(path=str(target), full_page=False)
                result.screenshot = str(target)
            except Exception:
                pass

        return result

    # -- run -----------------------------------------------------------------

    def run(self, panels: list[Panel]) -> list[PanelResult]:
        from playwright.sync_api import sync_playwright

        by_page: dict[str, list[Panel]] = {}
        for panel in panels:
            by_page.setdefault(panel.page, []).append(panel)

        with sync_playwright() as pw:
            browser = pw.chromium.launch(headless=not self.headed)
            headers = {}
            if self.admin_token:
                # auth_datastories.is_authenticated falls through to the admin
                # token, so one secret unlocks both the gated page and the XHRs
                # its panels make.
                headers["Authorization"] = f"Bearer {self.admin_token}"
            context = browser.new_context(
                viewport={"width": 1440, "height": 1000},
                extra_http_headers=headers or None,
                ignore_https_errors=True,
            )
            page = context.new_page()
            page.set_default_timeout(self.timeout)

            bucket = {"console": [], "failed": [], "last_api": None, "started": time.monotonic()}
            self._attach_listeners(page, bucket)

            self.index_is_empty = self._probe_index_empty(page)

            for page_path, page_panels in by_page.items():
                print(f"\n  {page_path}")
                try:
                    page.goto(f"{self.base}{page_path}",
                              wait_until="domcontentloaded", timeout=self.timeout)
                    bucket["started"] = time.monotonic()
                    self._wait_for_settle(bucket, budget_ms=15000)
                except Exception as exc:
                    print(f"    could not load {page_path}: {exc}")
                    for panel in page_panels:
                        result = PanelResult(page=page_path, slug=panel.slug, status=ERROR)
                        result.notes.append(f"page did not load: {str(exc)[:160]}")
                        self.results.append(result)
                    continue

                # A gated page renders its public landing view instead of the
                # tools, so every container is legitimately absent. Detect that
                # once rather than reporting "markup moved" for every panel.
                if page_path in GATED_PAGES and not self.admin_token:
                    present = page.evaluate(
                        "(sels) => sels.some(s => document.querySelector(s) !== null)",
                        [p.container for p in page_panels],
                    )
                    if not present:
                        print(f"    {GATED_PAGES[page_path]}")
                        for panel in page_panels:
                            result = PanelResult(page=page_path, slug=panel.slug, status=SKIP)
                            result.notes.append(GATED_PAGES[page_path])
                            self.results.append(result)
                        continue

                for panel in page_panels:
                    result = self.check_panel(page, panel, bucket)
                    self.results.append(result)
                    self._emit(result)

            context.close()
            browser.close()

        return self.results

    def _probe_index_empty(self, page) -> bool:
        try:
            response = page.request.get(f"{self.base}/api/articles?limit=1")
            if response.ok:
                payload = response.json()
                return not payload.get("articles") and not payload.get("total")
        except Exception:
            pass
        return False

    def _emit(self, result: PanelResult) -> None:
        colour = {DREW: "\033[32m", EMPTY: "\033[33m", BLANK: "\033[31m",
                  ERROR: "\033[31m", SKIP: "\033[90m"}
        tint = colour.get(result.status, "") if sys.stdout.isatty() else ""
        off = "\033[0m" if tint else ""
        print(f"    {tint}{result.status:<5}{off} {result.elapsed_ms:>6}ms  {result.slug}")
        for note in result.notes:
            print(f"            {note}")
        if result.screenshot:
            print(f"            screenshot: {result.screenshot}")


# ── Reporting ───────────────────────────────────────────────────────────────

def report(checker: PanelChecker, results: list[PanelResult], started: float) -> int:
    counts = {DREW: 0, EMPTY: 0, BLANK: 0, ERROR: 0, SKIP: 0}
    for r in results:
        counts[r.status] += 1

    print("\n" + "=" * 78)
    print("SUMMARY")
    print("=" * 78)

    if checker.site_findings:
        print("\n  SITE-LEVEL FINDINGS (page properties, not panel faults):")
        for finding in checker.site_findings.values():
            print(f"\n    {finding['message']}")
            print(f"      seen {finding['count']} time(s)")
            for example in finding["examples"]:
                print(f"      e.g. {example}")

    print(f"\n  {'page':<16} {'drew':>6} {'empty':>6} {'blank':>6} {'error':>6} {'skip':>6}")
    by_page: dict[str, dict[str, int]] = {}
    for r in results:
        bucket = by_page.setdefault(r.page, {DREW: 0, EMPTY: 0, BLANK: 0, ERROR: 0, SKIP: 0})
        bucket[r.status] += 1
    for page_path in sorted(by_page):
        b = by_page[page_path]
        print(f"  {page_path:<16} {b[DREW]:>6} {b[EMPTY]:>6} {b[BLANK]:>6} "
              f"{b[ERROR]:>6} {b[SKIP]:>6}")
    print(f"  {'TOTAL':<16} {counts[DREW]:>6} {counts[EMPTY]:>6} {counts[BLANK]:>6} "
          f"{counts[ERROR]:>6} {counts[SKIP]:>6}")

    for status, heading in ((ERROR, "ERRORED"), (BLANK, "BLANK")):
        if counts[status]:
            print(f"\n  {heading} ({counts[status]}):")
            for r in results:
                if r.status == status:
                    print(f"    {r.page}#{r.slug}")
                    for note in r.notes:
                        print(f"        {note}")

    if counts[EMPTY]:
        print(f"\n  EMPTY STATE ({counts[EMPTY]}):")
        for r in results:
            if r.status == EMPTY:
                print(f"    {r.page}#{r.slug}")

    print(f"\n  {len(results)} panels in {time.monotonic() - started:.0f}s")

    if checker.index_is_empty:
        print("\n  NOTE: the index reports 0 articles, so no panel has anything to draw.")
        print("        Only ERROR fails this run - a console error or a failed fetch is a")
        print("        fault whatever the data says. BLANK is reported but not failed:")
        print("        it means the panel rendered neither a chart nor a 'no data'")
        print("        message, which is a real gap in empty-state handling but not")
        print("        evidence the tool is broken. Re-run after a fetch to judge that.")
        return 1 if counts[ERROR] else 0

    # On a populated index every panel should draw. EMPTY and BLANK both mean a
    # reader arrived at a tool and got nothing.
    return 1 if (counts[BLANK] or counts[ERROR] or counts[EMPTY]) else 0


def main(argv=None) -> int:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass

    parser = argparse.ArgumentParser(
        description="Drive every analytical panel in a real browser and report what rendered.")
    parser.add_argument("--base-url", default=os.environ.get("PINAKES_SITE_URL", DEFAULT_BASE_URL))
    parser.add_argument("--admin-token", default=os.environ.get("PINAKES_ADMIN_TOKEN"),
                        help="unlocks the Datastories panels and their XHRs")
    parser.add_argument("--page", action="append", choices=["explore", "wac", "datastories"],
                        help="limit to one page; repeatable")
    parser.add_argument("--only", metavar="REGEX", help="limit to panels whose slug matches")
    parser.add_argument("--headed", action="store_true", help="show the browser")
    parser.add_argument("--timeout", type=float, default=45.0, help="per-panel timeout, seconds")
    parser.add_argument("--settle", type=int, default=900,
                        help="ms of network quiet before measuring (default 900)")
    parser.add_argument("--screenshot-dir", metavar="DIR",
                        help="save a screenshot for every BLANK or ERROR panel")
    parser.add_argument("--json-report", metavar="PATH")
    args = parser.parse_args(argv)

    try:
        import playwright  # noqa: F401
    except ImportError:
        print(
            "Playwright is not installed.\n\n"
            "    pip install -r requirements-browser.txt\n"
            "    python -m playwright install chromium\n",
            file=sys.stderr,
        )
        return 2

    panels = discover_panels(set(args.page) if args.page else None)
    if args.only:
        pattern = re.compile(args.only)
        panels = [p for p in panels if pattern.search(p.slug)]
    if not panels:
        print("No panels matched.", file=sys.stderr)
        return 2

    checker = PanelChecker(args)
    started = time.monotonic()

    print("Pinakes panel check")
    print(f"Target:      {checker.base}")
    print(f"Panels:      {len(panels)}")
    print(f"Admin token: {'supplied' if checker.admin_token else 'not supplied'}")

    results = checker.run(panels)
    code = report(checker, results, started)

    if args.json_report:
        with open(args.json_report, "w", encoding="utf-8") as fh:
            json.dump({
                "base_url": checker.base,
                "index_is_empty": checker.index_is_empty,
                "results": [asdict(r) for r in results],
            }, fh, indent=2)
        print(f"\n  JSON report: {args.json_report}")

    return code


if __name__ == "__main__":
    sys.exit(main())
