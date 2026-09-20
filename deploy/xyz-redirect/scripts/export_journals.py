"""Write src/journals.json from journals.ALL_JOURNAL_NAMES.

The moving band on the redirect page lists every journal Pinakes indexes.
Re-run this after adding a journal, then redeploy:

    python scripts/export_journals.py

Descriptive subtitles ("Kairos: A Journal of ...") are dropped so the band
stays readable; subtitles that are part of the name ("WPA: Writing Program
Administration") are kept.
"""

import json
import sys
from pathlib import Path

PROJECT = Path(__file__).resolve().parents[1]
REPO = PROJECT.parents[1]
sys.path.insert(0, str(REPO))

from journals import ALL_JOURNAL_NAMES  # noqa: E402


def band_title(name):
    head, sep, tail = name.partition(": ")
    if sep and tail.startswith(("A ", "The ")):
        return head
    return name


def main():
    titles = []
    for name in ALL_JOURNAL_NAMES:
        title = band_title(name)
        if title not in titles:
            titles.append(title)
    out = PROJECT / "src" / "journals.json"
    out.write_text(json.dumps(titles, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"{len(titles)} journals -> {out.relative_to(PROJECT)}")


if __name__ == "__main__":
    main()
