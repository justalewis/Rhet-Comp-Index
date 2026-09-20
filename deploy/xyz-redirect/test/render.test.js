import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';

import {
  NEW_ORIGIN, decide, describe, displayPath, pageResponse, redirectResponse, renderPage,
} from '../src/render.js';

const OLD = 'https://pinakes.xyz';
const request = (path, init) => new Request(OLD + path, init);
const TEMPLATE = '<a href="{{TARGET}}">{{LABEL}}|{{BUTTON}}|{{DOMAIN}}{{PATH}}</a><div>{{JOURNALS}}</div>';

test('feeds, API calls, exports, and emailed links get a 301 to the same path', () => {
  const paths = [
    '/feed.xml',
    '/feed.opml',
    '/feed/college-english.xml',
    '/feed/group/technical-communication.opml',
    '/feed/select/ab12cd.xml',
    '/api/articles?page=2',
    '/export?format=ris&ids=4,5',
    '/alerts/unsubscribe/tok123',
    '/redaction-request/verify/tok456',
    '/redaction-request/orcid/callback?code=abc',
    '/jwa/',
    '/datastories/login',
    '/health/ready',
  ];
  for (const path of paths) {
    const d = decide(request(path));
    assert.equal(d.kind, 'redirect', path);
    assert.equal(d.status, 301, path);
    assert.equal(d.location, NEW_ORIGIN + path, path);
  }
});

test('other methods keep their method with a 308', () => {
  const d = decide(request('/fetch', { method: 'POST' }));
  assert.deepEqual([d.kind, d.status, d.location], ['redirect', 308, `${NEW_ORIGIN}/fetch`]);
  assert.equal(decide(request('/alerts/subscribe', { method: 'POST' })).status, 308);
});

test('pages people read get the moved page, with the new address carried through', () => {
  const paths = ['/', '/atlas', '/article/48213', '/author/Justin%20Lewis',
    '/?q=threshold+concepts', '/feed/college-english', '/anything/at/all'];
  for (const path of paths) {
    const d = decide(request(path));
    assert.equal(d.kind, 'page', path);
    assert.equal(d.location, NEW_ORIGIN + path, path);
  }
});

test('the skip cookie sends a visitor straight through with a 302', () => {
  const d = decide(request('/atlas', { headers: { Cookie: 'theme=dark; pinakes_skip=1' } }));
  assert.deepEqual([d.kind, d.status], ['redirect', 302]);
  assert.equal(decide(request('/atlas', { headers: { Cookie: 'pinakes_skip=0' } })).kind, 'page');
});

test('assets and robots.txt are answered here', () => {
  assert.equal(decide(request('/_moved/moved.css')).kind, 'asset');
  assert.equal(decide(request('/robots.txt')).kind, 'robots');
});

test('the label names what the visitor was after', () => {
  const label = (path) => describe(new URL(OLD + path)).label;
  assert.equal(label('/'), 'Pinakes is now at');
  assert.equal(describe(new URL(`${OLD}/`)).button, 'Go to Pinakes');
  assert.equal(label('/author/Justin%20Lewis'), 'The author page for Justin Lewis is now at');
  assert.equal(label('/?q=threshold+concepts'), 'Your search for “threshold concepts” is now at');
  assert.equal(label('/atlas'), 'Atlas of the Field is now at');
  assert.equal(label('/new/'), 'What’s New is now at');
  assert.equal(label('/article/48213'), 'The article you were looking for is now at');
  assert.equal(label('/book/77'), 'The book you were looking for is now at');
  assert.equal(label('/nowhere'), 'The page you were looking for is now at');
  assert.equal(label('/?journal=College+English'), 'The page you were looking for is now at');
});

test('the displayed address is decoded for reading', () => {
  assert.equal(displayPath(new URL(`${OLD}/`)), '');
  assert.equal(displayPath(new URL(`${OLD}/author/Justin%20Lewis`)), '/author/Justin Lewis');
  assert.equal(displayPath(new URL(`${OLD}/?q=threshold+concepts`)), '/?q=threshold concepts');
  assert.equal(displayPath(new URL(`${OLD}/author/%E0%A4%A`)), '/author/%E0%A4%A');
});

test('anything taken from the address is escaped in the page', () => {
  const d = decide(request('/author/%3Cscript%3Ealert(1)%3C%2Fscript%3E?q=%22%3E%3Cimg'));
  const html = renderPage(TEMPLATE, d, ['A & B']);
  assert.ok(!html.includes('<script>'));
  assert.ok(!html.includes('"><img'));
  assert.ok(html.includes('&lt;script&gt;'));
  assert.ok(html.includes('<span>A &amp; B</span>'));
});

test('the band lists every journal twice so the loop has no seam', () => {
  const html = renderPage('{{JOURNALS}}', decide(request('/')), ['Kairos', 'Peitho']);
  assert.equal(html.match(/<span>Kairos<\/span>/g).length, 2);
});

test('the real template renders with no placeholders left', () => {
  const template = readFileSync(new URL('../src/page.html', import.meta.url), 'utf8');
  const journals = JSON.parse(readFileSync(new URL('../src/journals.json', import.meta.url), 'utf8'));
  const html = renderPage(template, decide(request('/author/Justin%20Lewis')), journals);
  assert.ok(!/\{\{[A-Z]+\}\}/.test(html));
  assert.match(html, /The author page for Justin Lewis is now at/);
  assert.match(html, /href="https:\/\/pinakes\.wacclearinghouse\.org\/author\/Justin%20Lewis"/);
});

test('the page response names its canonical address and sets a strict CSP', async () => {
  const req = request('/atlas');
  const res = pageResponse(req, decide(req), TEMPLATE, ['Kairos']);
  assert.equal(res.status, 200);
  assert.equal(res.headers.get('Link'), `<${NEW_ORIGIN}/atlas>; rel="canonical"`);
  assert.match(res.headers.get('Content-Security-Policy'), /script-src 'self'/);
  assert.match(await res.text(), /Atlas of the Field is now at/);
});

test('HEAD gets the headers without a body', async () => {
  const req = request('/atlas', { method: 'HEAD' });
  const res = pageResponse(req, decide(req), TEMPLATE, []);
  assert.equal(res.status, 200);
  assert.equal(await res.text(), '');
});

test('redirects are cacheable except the per-visitor skip', () => {
  assert.equal(redirectResponse({ status: 301, location: NEW_ORIGIN }).headers.get('Cache-Control'), 'public, max-age=3600');
  assert.equal(redirectResponse({ status: 302, location: NEW_ORIGIN }).headers.get('Cache-Control'), 'no-store');
});
