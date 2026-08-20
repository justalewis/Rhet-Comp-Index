<?xml version="1.0" encoding="utf-8"?>
<!--
  feed.xsl — what a person sees when they click a feed link in a browser.

  A feed URL is meant for software, but people click them, and a wall of raw
  XML tells them nothing about what to do next. Browsers apply this stylesheet
  and render a short page explaining what the URL is for; feed readers ignore
  the xml-stylesheet processing instruction entirely and parse the Atom
  underneath, so nothing about the actual feed changes.

  Deliberately free of JavaScript and external assets: the page has to work
  under the site's Content-Security-Policy in a document the browser built by
  transforming XML, which is a stranger environment than an ordinary page.

  If a browser ever drops XSLT support, this degrades to the raw XML that was
  showing before — the same behaviour, no worse.
-->
<xsl:stylesheet version="1.0"
                xmlns:xsl="http://www.w3.org/1999/XSL/Transform"
                xmlns:atom="http://www.w3.org/2005/Atom"
                exclude-result-prefixes="atom">

  <xsl:output method="html" encoding="utf-8" indent="yes"
              doctype-system="about:legacy-compat"/>

  <xsl:template match="/">
    <html lang="en">
      <head>
        <meta charset="utf-8"/>
        <meta name="viewport" content="width=device-width, initial-scale=1"/>
        <title><xsl:value-of select="/atom:feed/atom:title"/> — Pinakes feed</title>
        <style>
          :root { color-scheme: light dark; }
          body {
            font-family: Georgia, 'Times New Roman', serif;
            font-size: 17px; line-height: 1.55;
            max-width: 40rem; margin: 0 auto; padding: 2.5rem 1.25rem 4rem;
            color: #1a1a1a; background: #fdfdfb;
          }
          a { color: #7a2e2e; }
          .kicker {
            font-family: system-ui, -apple-system, sans-serif;
            font-size: 0.72rem; letter-spacing: 0.09em; text-transform: uppercase;
            color: #8a7f70; margin: 0 0 0.4rem;
          }
          h1 { font-size: 1.5rem; line-height: 1.25; margin: 0 0 1.2rem; }
          .lede { margin: 0 0 1.4rem; }
          .url-box {
            border: 1px solid #ddd6c9; border-radius: 4px;
            padding: 0.85rem 1rem; margin: 0 0 1.4rem; background: #fff;
          }
          .url-box p {
            font-family: system-ui, -apple-system, sans-serif;
            font-size: 0.72rem; letter-spacing: 0.09em; text-transform: uppercase;
            color: #8a7f70; margin: 0 0 0.35rem;
          }
          .url-box code {
            font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
            font-size: 0.92rem; word-break: break-all; user-select: all;
          }
          .steps { padding-left: 1.15rem; margin: 0 0 1.6rem; }
          .steps li { margin-bottom: 0.4rem; }
          hr { border: 0; border-top: 1px solid #e5ded1; margin: 2rem 0 1.4rem; }
          h2 {
            font-family: system-ui, -apple-system, sans-serif;
            font-size: 0.78rem; letter-spacing: 0.08em; text-transform: uppercase;
            color: #8a7f70; margin: 0 0 0.9rem;
          }
          .entry { margin: 0 0 1.3rem; }
          .entry-title { font-weight: 600; margin: 0 0 0.15rem; }
          .entry-title a { text-decoration: none; }
          .entry-title a:hover { text-decoration: underline; }
          .entry-meta {
            font-family: system-ui, -apple-system, sans-serif;
            font-size: 0.82rem; color: #6b6257; margin: 0;
          }
          footer {
            margin-top: 2.5rem; font-size: 0.85rem; color: #6b6257;
            font-family: system-ui, -apple-system, sans-serif;
          }
          @media (prefers-color-scheme: dark) {
            body { color: #e8e3d9; background: #16150f; }
            a { color: #d99b9b; }
            .url-box { background: #1f1e16; border-color: #38352a; }
            hr { border-top-color: #38352a; }
          }
        </style>
      </head>
      <body>

        <p class="kicker">Pinakes feed</p>
        <h1><xsl:value-of select="/atom:feed/atom:title"/></h1>

        <p class="lede">
          This page is a feed. It is meant to be read by a feed reader, which
          checks it on a schedule and shows you new articles as they are
          indexed. You are seeing it in a browser, which is why it looks like
          this.
        </p>

        <div class="url-box">
          <p>Feed address</p>
          <code><xsl:value-of select="/atom:feed/atom:link[@rel='self']/@href"/></code>
        </div>

        <ol class="steps">
          <li>Copy the address above.</li>
          <li>Open your feed reader and look for "Add", "Subscribe", or "+".</li>
          <li>Paste the address.</li>
        </ol>

        <p>
          No reader yet? <a href="https://feedly.com">Feedly</a> and
          <a href="https://www.inoreader.com">Inoreader</a> run in a browser
          and are free to start.
          <a href="https://netnewswire.com">NetNewsWire</a> is a free Mac and
          iPhone app. Thunderbird handles feeds alongside email.
        </p>

        <p>
          <a href="https://pinakes.xyz/feeds">All Pinakes feeds</a> — one for
          each journal in the index, plus one for everything.
        </p>

        <hr/>

        <h2>Most recent in this feed</h2>

        <xsl:for-each select="/atom:feed/atom:entry">
          <div class="entry">
            <p class="entry-title">
              <a href="{atom:link[@rel='alternate']/@href}">
                <xsl:value-of select="atom:title"/>
              </a>
            </p>
            <p class="entry-meta">
              <xsl:if test="atom:author/atom:name">
                <xsl:value-of select="atom:author/atom:name"/>
              </xsl:if>
              <xsl:if test="atom:category/@term">
                <xsl:if test="atom:author/atom:name"> · </xsl:if>
                <xsl:value-of select="atom:category/@term"/>
              </xsl:if>
            </p>
          </div>
        </xsl:for-each>

        <footer>
          <a href="https://pinakes.xyz">Pinakes</a> — an index of rhetoric,
          composition, and writing studies scholarship.
        </footer>

      </body>
    </html>
  </xsl:template>

</xsl:stylesheet>
