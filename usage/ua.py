"""usage/ua.py — read a User-Agent header into browser, OS, device, and, for
automated clients, a name and kind.

Only the parsed fields are stored, never the raw header: a full User-Agent is
close to a fingerprint, and "Firefox on Windows, desktop" is all a report
needs. Bot kinds let reports separate crawlers from feed readers, which are
subscribers rather than noise (Feedly and friends even say how many).
"""

from __future__ import annotations

import re

# (pattern, name, kind). First match wins, so specific names precede the
# generic catch-alls at the bottom.
_BOTS: list[tuple[re.Pattern, str, str]] = [(re.compile(p, re.I), n, k) for p, n, k in [
    # Feed readers — subscribers, reported separately.
    (r"Feedly", "Feedly", "feed"),
    (r"Inoreader", "Inoreader", "feed"),
    (r"NewsBlur", "NewsBlur", "feed"),
    (r"Feedbin", "Feedbin", "feed"),
    (r"theoldreader", "The Old Reader", "feed"),
    (r"FreshRSS", "FreshRSS", "feed"),
    (r"Tiny Tiny RSS", "Tiny Tiny RSS", "feed"),
    (r"NetNewsWire", "NetNewsWire", "feed"),
    (r"Miniflux", "Miniflux", "feed"),
    (r"Reeder", "Reeder", "feed"),
    (r"FeedFetcher-Google|Feedfetcher", "Google Feedfetcher", "feed"),
    (r"Thunderbird", "Thunderbird", "feed"),
    (r"Zotero", "Zotero", "feed"),
    (r"\bRSS\b|\bfeed ?reader\b|feedparser|\bfeed ?fetcher\b", "Feed reader", "feed"),
    # AI assistants and crawlers.
    (r"ChatGPT-User", "ChatGPT (user fetch)", "ai"),
    (r"OAI-SearchBot", "OpenAI search", "ai"),
    (r"GPTBot", "GPTBot", "ai"),
    (r"Claude-User", "Claude (user fetch)", "ai"),
    (r"Claude-SearchBot", "Claude search", "ai"),
    (r"ClaudeBot|anthropic-ai", "ClaudeBot", "ai"),
    (r"Perplexity-User", "Perplexity (user fetch)", "ai"),
    (r"PerplexityBot", "PerplexityBot", "ai"),
    (r"CCBot", "Common Crawl", "ai"),
    (r"Bytespider", "Bytespider", "ai"),
    (r"meta-externalagent|meta-externalfetcher", "Meta AI", "ai"),
    (r"Amazonbot", "Amazonbot", "ai"),
    (r"Applebot", "Applebot", "search"),
    (r"cohere-ai|cohere-training", "Cohere", "ai"),
    (r"Diffbot", "Diffbot", "ai"),
    (r"YouBot", "You.com", "ai"),
    (r"Timpibot|ImagesiftBot|omgili|AI2Bot|Ai2Bot", "AI crawler", "ai"),
    # Search engines.
    (r"Googlebot|GoogleOther|Google-InspectionTool|APIs-Google|AdsBot-Google", "Google", "search"),
    (r"bingbot|BingPreview|msnbot", "Bing", "search"),
    (r"DuckDuckBot|DuckAssistBot", "DuckDuckGo", "search"),
    (r"YandexBot|YandexAccessibilityBot", "Yandex", "search"),
    (r"Baiduspider", "Baidu", "search"),
    (r"SeznamBot", "Seznam", "search"),
    (r"Sogou", "Sogou", "search"),
    (r"Qwantify|Qwantbot", "Qwant", "search"),
    (r"PetalBot", "Petal", "search"),
    (r"Yeti/", "Naver", "search"),
    (r"ia_archiver|archive\.org_bot|Wayback", "Internet Archive", "archive"),
    # SEO and marketing crawlers.
    (r"AhrefsBot|AhrefsSiteAudit", "Ahrefs", "seo"),
    (r"SemrushBot|SiteAuditBot", "Semrush", "seo"),
    (r"MJ12bot", "Majestic", "seo"),
    (r"DotBot", "Moz", "seo"),
    (r"DataForSeoBot", "DataForSEO", "seo"),
    (r"BLEXBot", "BLEXBot", "seo"),
    (r"serpstatbot|SeekportBot|barkrowler|Screaming Frog", "SEO crawler", "seo"),
    # Link previews.
    (r"facebookexternalhit|FacebookBot|meta-webindexer", "Facebook", "social"),
    (r"Twitterbot", "X/Twitter", "social"),
    (r"LinkedInBot", "LinkedIn", "social"),
    (r"Slackbot|Slack-ImgProxy", "Slack", "social"),
    (r"Discordbot", "Discord", "social"),
    (r"TelegramBot", "Telegram", "social"),
    (r"WhatsApp", "WhatsApp", "social"),
    (r"Bluesky|Cardyb", "Bluesky", "social"),
    (r"Mastodon", "Mastodon", "social"),
    (r"redditbot", "Reddit", "social"),
    # Uptime monitors.
    (r"UptimeRobot|Pingdom|StatusCake|Site24x7|Better ?Uptime|uptime-kuma|Checkly", "Uptime monitor", "monitor"),
    # Scripts and HTTP libraries.
    (r"HeadlessChrome|PhantomJS|Puppeteer|Playwright|Selenium", "Headless browser", "script"),
    (r"python-requests|python-urllib|aiohttp|httpx|Scrapy|python/", "Python script", "script"),
    (r"^curl/|libcurl", "curl", "script"),
    (r"Wget", "Wget", "script"),
    (r"Go-http-client", "Go script", "script"),
    (r"^Java/|Apache-HttpClient|okhttp", "Java client", "script"),
    (r"node-fetch|axios|undici|got \(", "Node script", "script"),
    (r"libwww-perl|LWP::", "Perl script", "script"),
    (r"PostmanRuntime|insomnia", "API client", "script"),
    # Anything else that says what it is.
    (r"bot\b|crawl|spider|slurp|fetcher|scanner", "Other bot", "other"),
]]

_SUBSCRIBERS = re.compile(r"(\d+)\s+(?:subscribers?|readers?)", re.I)


def _family(ua: str) -> str:
    if "Edg/" in ua or "EdgA/" in ua or "EdgiOS/" in ua:
        return "Edge"
    if "OPR/" in ua or "Opera" in ua:
        return "Opera"
    if "SamsungBrowser" in ua:
        return "Samsung Internet"
    if "Firefox/" in ua or "FxiOS/" in ua:
        return "Firefox"
    if "CriOS/" in ua or "Chrome/" in ua or "Chromium/" in ua:
        return "Chrome"
    if "Safari/" in ua and ("Version/" in ua or "Mobile/" in ua):
        return "Safari"
    return "Other"


def _os(ua: str) -> str:
    if "iPhone" in ua or "iPad" in ua or "iPod" in ua:
        return "iOS"
    if "Android" in ua:
        return "Android"
    if "CrOS" in ua:
        return "ChromeOS"
    if "Windows" in ua:
        return "Windows"
    if "Mac OS X" in ua or "Macintosh" in ua:
        return "macOS"
    if "Linux" in ua or "X11" in ua:
        return "Linux"
    return "Other"


def _device(ua: str) -> str:
    if "iPad" in ua or "Tablet" in ua or ("Android" in ua and "Mobile" not in ua):
        return "tablet"
    if "Mobi" in ua or "iPhone" in ua or "iPod" in ua:
        return "mobile"
    return "desktop"


def parse(ua: str | None) -> dict:
    """{family, os, device, bot, bot_kind, feed_subs} for one User-Agent."""
    ua = (ua or "").strip()
    out = {"family": "Other", "os": "Other", "device": "desktop",
           "bot": None, "bot_kind": None, "feed_subs": None}
    if not ua:
        out.update(bot="No user agent", bot_kind="script")
        return out
    for pattern, name, kind in _BOTS:
        if pattern.search(ua):
            out.update(bot=name, bot_kind=kind)
            if kind == "feed":
                m = _SUBSCRIBERS.search(ua)
                if m:
                    out["feed_subs"] = int(m.group(1))
            break
    out["family"] = _family(ua)
    out["os"] = _os(ua)
    out["device"] = _device(ua)
    return out
