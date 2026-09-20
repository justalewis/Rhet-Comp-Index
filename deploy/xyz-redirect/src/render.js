// Request handling for the pinakes.xyz redirect Worker. Nothing in this file
// imports Workers-only modules, so `npm test` runs it under plain Node.

export const NEW_HOST = 'pinakes.wacclearinghouse.org';
export const NEW_ORIGIN = `https://${NEW_HOST}`;
export const ASSET_PREFIX = '/_moved/';
export const SKIP_COOKIE = 'pinakes_skip';

// Requests that come from feed readers, scripts, and emailed links. They get a
// real redirect instead of the page, so subscriptions, API clients, and
// one-click links (unsubscribe, verify, ORCID) keep working without a stop here.
const DIRECT_PREFIXES = [
  '/api', '/alerts', '/redaction-request', '/datastories', '/jwa',
  '/admin', '/health', '/static', '/export', '/fetch',
];
const DIRECT_FILES = /\.(xml|opml|json|bib|ris|csv|txt|ico)$/i;

// Names for the line above the new address ("Atlas of the Field is now at").
const NAMED_PAGES = {
  '/about': 'The About page',
  '/atlas': 'Atlas of the Field',
  '/authors': 'The author list',
  '/books': 'The book list',
  '/citations': 'The citations page',
  '/coverage': 'Index Coverage',
  '/explore': 'The Explore page',
  '/feeds': 'The feeds page',
  '/feeds/select': 'The feed builder',
  '/glossary': 'The glossary',
  '/most-cited': 'Most Cited',
  '/new': 'What’s New',
  '/tools': 'The tools page',
  '/wac': 'The WAC Clearinghouse as a Press',
};

const ITEM_PAGES = {
  article: 'The article you were looking for is now at',
  book: 'The book you were looking for is now at',
  institution: 'The institution page you were looking for is now at',
  feed: 'The feed you were looking for is now at',
};

const CSP = [
  "default-src 'none'",
  "style-src 'self'",
  "font-src 'self'",
  "img-src 'self' data:",
  "script-src 'self'",
  "base-uri 'none'",
  "form-action 'none'",
  "frame-ancestors 'none'",
].join('; ');

const SEPARATOR = '<b>·</b>';

export function isDirect(pathname) {
  if (DIRECT_FILES.test(pathname)) return true;
  return DIRECT_PREFIXES.some((p) => pathname === p || pathname.startsWith(`${p}/`));
}

function hasSkipCookie(cookieHeader) {
  return new RegExp(`(?:^|;\\s*)${SKIP_COOKIE}=1(?:;|$)`).test(cookieHeader || '');
}

// Serve an asset, answer robots.txt, redirect, or show the page. GET and HEAD
// redirects use 301, which every feed reader understands; other methods get
// 308 so a POST arrives as a POST. Visitors who ticked "skip this page next
// time" get a 302, since that is their preference rather than a permanent fact.
export function decide(request) {
  const url = new URL(request.url);
  const { pathname } = url;
  if (pathname.startsWith(ASSET_PREFIX)) return { kind: 'asset' };
  if (pathname === '/robots.txt') return { kind: 'robots' };

  const location = NEW_ORIGIN + pathname + url.search;
  if (request.method !== 'GET' && request.method !== 'HEAD') {
    return { kind: 'redirect', status: 308, location };
  }
  if (isDirect(pathname)) return { kind: 'redirect', status: 301, location };
  if (hasSkipCookie(request.headers.get('Cookie'))) {
    return { kind: 'redirect', status: 302, location };
  }
  return { kind: 'page', location, url };
}

// The line above the new address, naming what the visitor was after.
export function describe(url) {
  const path = url.pathname.replace(/\/+$/, '') || '/';
  const go = 'Go to the new address';

  if (path === '/') {
    const q = (url.searchParams.get('q') || '').trim();
    if (q) return { label: `Your search for “${clip(q, 80)}” is now at`, button: go };
    if (url.search) return { label: 'The page you were looking for is now at', button: go };
    return { label: 'Pinakes is now at', button: 'Go to Pinakes' };
  }

  const [section, ...rest] = path.slice(1).split('/');
  if (section === 'author' && rest.length) {
    const name = clip(decodePart(rest.join('/')).trim(), 90);
    if (name) return { label: `The author page for ${name} is now at`, button: go };
  }
  if (ITEM_PAGES[section] && rest.length) return { label: ITEM_PAGES[section], button: go };
  if (NAMED_PAGES[path]) return { label: `${NAMED_PAGES[path]} is now at`, button: go };
  return { label: 'The page you were looking for is now at', button: go };
}

// The part of the new address after the domain, decoded so it reads as text.
export function displayPath(url) {
  if (url.pathname === '/' && !url.search) return '';
  const query = url.search ? `?${decodePart(url.search.slice(1).replace(/\+/g, ' '))}` : '';
  return clip(decodePart(url.pathname) + query, 140);
}

// The journal titles twice over, so the band loops without a visible seam.
export function bandHtml(journals) {
  const once = journals.map((title) => `<span>${escapeHtml(title)}</span>${SEPARATOR}`).join('');
  return once + once;
}

export function renderPage(template, decision, journals) {
  const { label, button } = describe(decision.url);
  const values = {
    TARGET: decision.location,
    LABEL: label,
    BUTTON: button,
    DOMAIN: NEW_HOST,
    PATH: displayPath(decision.url),
  };
  return template.replace(/\{\{([A-Z]+)\}\}/g, (whole, key) => {
    if (key === 'JOURNALS') return bandHtml(journals);
    return key in values ? escapeHtml(values[key]) : whole;
  });
}

export function pageResponse(request, decision, template, journals) {
  const headers = new Headers({
    'Content-Type': 'text/html; charset=utf-8',
    'Cache-Control': 'no-cache',
    Vary: 'Cookie',
    Link: `<${decision.location}>; rel="canonical"`,
    'Content-Security-Policy': CSP,
    'Referrer-Policy': 'strict-origin-when-cross-origin',
    'X-Content-Type-Options': 'nosniff',
  });
  const body = request.method === 'HEAD' ? null : renderPage(template, decision, journals);
  return new Response(body, { status: 200, headers });
}

export function redirectResponse({ status, location }) {
  return new Response(null, {
    status,
    headers: {
      Location: location,
      'Cache-Control': status === 302 ? 'no-store' : 'public, max-age=3600',
    },
  });
}

// Crawlers may read every page here; each one names its new address as canonical.
export function robotsResponse() {
  return new Response('User-agent: *\nAllow: /\n', {
    headers: {
      'Content-Type': 'text/plain; charset=utf-8',
      'Cache-Control': 'public, max-age=3600',
    },
  });
}

export function escapeHtml(value) {
  const entities = { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' };
  return String(value).replace(/[&<>"']/g, (c) => entities[c]);
}

function decodePart(text) {
  try {
    return decodeURIComponent(text);
  } catch {
    return text;
  }
}

function clip(text, max) {
  return text.length > max ? `${text.slice(0, max - 1)}…` : text;
}
