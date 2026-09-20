// pinakes.xyz and www.pinakes.xyz: the "Pinakes has moved" page for people,
// real redirects for feeds, scripts, and emailed links. Logic lives in
// render.js; this file only wires it to the Workers runtime.

import PAGE from './page.html';
import JOURNALS from './journals.json';
import { decide, pageResponse, redirectResponse, robotsResponse } from './render.js';

export default {
  async fetch(request, env) {
    const decision = decide(request);
    if (decision.kind === 'asset') return env.ASSETS.fetch(request);
    if (decision.kind === 'robots') return robotsResponse();
    if (decision.kind === 'redirect') return redirectResponse(decision);
    return pageResponse(request, decision, PAGE, JOURNALS);
  },
};
