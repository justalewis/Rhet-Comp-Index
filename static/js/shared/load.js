// static/js/shared/load.js
//
// JSON fetch for the Explore viz loaders. A bare `fetch().json()` hands a
// 429 or 500 body to the renderer as if it were data, so a rate-limited
// request used to read as "no results" or hang on "Loading…". getJson()
// throws on any non-2xx status; loadErrorMessage() turns the error into the
// sentence the panel shows.
//
// (The Datastories modules use fetchJson() in common.js, which has its own
// error text and no-cache policy.)

export const RATE_LIMIT_MESSAGE = 'Too many requests — wait a minute and try again.';

export class LoadError extends Error {
  constructor(status, detail) {
    super('HTTP ' + status + (detail ? ': ' + detail : ''));
    this.status = status;
    this.detail = detail || '';   // the API's {"error": ...} text, if any
  }
}

export async function getJson(url, init) {
  const resp = await fetch(url, init);
  if (!resp.ok) {
    let detail = '';
    try { detail = (await resp.json()).error || ''; } catch (e) {}
    throw new LoadError(resp.status, detail);
  }
  return resp.json();
}

// Plain text for the panel: the rate-limit sentence on a 429, otherwise the
// caller's own failure message.
export function loadErrorMessage(err, fallback) {
  if (err && err.status === 429) return RATE_LIMIT_MESSAGE;
  return fallback || 'Could not load this view. Try again later.';
}

// The same message as an explore-hint paragraph, for container.innerHTML.
export function loadErrorHtml(err, fallback) {
  return '<p class="explore-hint">' + loadErrorMessage(err, fallback) + '</p>';
}
