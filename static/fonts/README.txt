These woff2 files are copied from Eunomia's Labors (eunomiaslabors.xyz/static/fonts/),
which fetches them from the upstream font projects with its tools/fetch_fonts.py.
All four families are licensed under the SIL Open Font License 1.1
(https://openfontlicense.org):

  GFS Didot              Greek Font Society
  Literata               TypeTogether, for Google
  EB Garamond            Georg Duffner and Octavio Pardo
  Atkinson Hyperlegible  Braille Institute of America

23 faces, covering latin, latin-ext, greek, and greek-ext (polytonic) subsets;
fonts.css maps them by unicode-range so a reader only downloads what a page
actually sets. Do not add faces here by hand — regenerate upstream and copy.
