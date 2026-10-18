# pinakes.xyz redirect

A Cloudflare Worker that answers for `pinakes.xyz` and `www.pinakes.xyz` now that Pinakes lives at
[pinakes.wacclearinghouse.org](https://pinakes.wacclearinghouse.org).

- People following an old link get the "Pinakes has moved" page. It names the page they wanted and
  links to its new address (same path and query, new domain). Nobody is forwarded automatically;
  ticking "Next time, skip this page and go straight there" sets a `pinakes_skip` cookie, and from
  then on that browser gets a 302 (sent `no-store`, since it is a preference rather than a
  permanent fact) straight to the new address.
- Feeds, API calls, exports, and emailed links get a permanent 301 instead of the page. That covers
  the paths in `DIRECT_PREFIXES` in `src/render.js` (`/api`, `/alerts`, `/redaction-request`,
  `/datastories`, `/jwa`, `/admin`, `/health`, `/static`, `/export`, `/fetch`) and any path ending
  in one of the `DIRECT_FILES` extensions (`.xml`, `.opml`, `.json`, `.bib`, `.ris`, `.csv`,
  `.txt`, `.ico`).
- Any request other than GET or HEAD, on any path, gets a 308 so a POST stays a POST.
- `/robots.txt` is answered by the Worker itself and allows everything, so crawlers can read each
  page and find its canonical link.
- Every page names its new address as canonical, so search engines move their listings over.

The design is Eunomia's Labors' Alexandrian Suite. `public/_moved/tokens.css` and the fonts are
copied from that site.

| Path | What it is |
|---|---|
| `src/render.js` | Routing, labels, and responses. Plain JS, tested under Node. |
| `src/worker.js` | Wires `render.js` to the Workers runtime. |
| `src/page.html` | The page template; `{{TARGET}}`, `{{LABEL}}`, and the rest are filled per request. |
| `src/journals.json` | Titles for the moving band, generated from `journals.py`. |
| `public/_moved/` | Styles, script, fonts, and favicon, served under `/_moved/`. |

## Commands

Run these from this folder after `npm install`:

```bash
npm test
```

```bash
npm run dev
```

`npm test` runs the routing and rendering tests under Node. `npm run dev` serves the Worker at
http://127.0.0.1:8787. After adding a journal to `journals.py`, `npm run journals` regenerates the
band. `npm run check` bundles without deploying, and `npm run deploy` publishes (after a one-time
`npx wrangler login`).

## Rolling back

The Worker takes over the domain through the two `routes` in `wrangler.toml`. While the old Fly app
is still running, deleting those routes in the Cloudflare dashboard (Workers Routes on the
`pinakes.xyz` zone), or running `npx wrangler delete`, puts `pinakes.xyz` back on Fly at once.

Think before doing that. `fly-deploy.yml` keeps Fly's code current, but Fly's database is its own
copy: the scheduled jobs that fed it stopped on 2026-09-19, and nothing done on the Clearinghouse
server since then reaches it. Rolling back would serve that stale copy, without the articles
fetched since the move and without any author-name removals processed on the server, so names
removed there would show again on `pinakes.xyz`.
