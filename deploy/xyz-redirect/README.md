# pinakes.xyz redirect

A Cloudflare Worker that answers for `pinakes.xyz` and `www.pinakes.xyz` now that Pinakes lives at
[pinakes.wacclearinghouse.org](https://pinakes.wacclearinghouse.org).

- People following an old link get the "Pinakes has moved" page. It names the page they wanted and
  links to its new address (same path, new domain). Nobody is forwarded automatically; ticking
  "skip this page next time" sets a cookie that sends that browser straight through afterwards.
- Feeds, API calls, exports, and emailed links (`*.xml`, `*.opml`, `/api/…`, `/export`, `/alerts/…`,
  `/redaction-request/…`, and the rest of `DIRECT_PREFIXES` in `src/render.js`) get a permanent
  redirect instead: 301 for GET and HEAD, 308 for anything else so a POST stays a POST.
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
