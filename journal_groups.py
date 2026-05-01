"""journal_groups.py — Field-group classification used by the Datastories tools.

Maps every journal name to one of:
  TPC        — Technical and Professional Communication
  RHET_COMP  — Rhetoric and Composition
  OTHER      — Adjacent fields (writing centers, WAC, international, digital presses)

Imported by db/datastories.py routines that need to bucket articles or edges
by sub-field. Kept separate from journals.py (which is the metadata source of
truth for ingestion) because the grouping is a research-axis classification,
not an ingestion concern.
"""

JOURNAL_GROUPS = {
    # ── TPC ──
    "Technical Communication Quarterly":                        "TPC",
    "Journal of Business and Technical Communication":          "TPC",
    "Journal of Technical Writing and Communication":           "TPC",
    "Business and Professional Communication Quarterly":        "TPC",
    "IEEE Transactions on Professional Communication":          "TPC",
    "Communication Design Quarterly":                           "TPC",
    "Communication Design Quarterly Review":                    "TPC",
    "Rhetoric of Health and Medicine":                          "TPC",
    "Double Helix":                                             "TPC",

    # ── Rhetoric & Composition ──
    "College Composition and Communication":                    "RHET_COMP",
    "College English":                                          "RHET_COMP",
    "Rhetoric Review":                                          "RHET_COMP",
    "Rhetoric Society Quarterly":                               "RHET_COMP",
    "Written Communication":                                    "RHET_COMP",
    "Research in the Teaching of English":                      "RHET_COMP",
    "Composition Studies":                                      "RHET_COMP",
    "Composition Forum":                                        "RHET_COMP",
    "Teaching English in the Two-Year College":                 "RHET_COMP",
    "Pedagogy":                                                 "RHET_COMP",
    "Kairos: A Journal of Rhetoric, Technology, and Pedagogy":  "RHET_COMP",
    "Computers and Composition Digital Press":                  "RHET_COMP",
    "Enculturation":                                            "RHET_COMP",
    "Philosophy & Rhetoric":                                    "RHET_COMP",
    "Rhetoric & Public Affairs":                                "RHET_COMP",
    "Assessing Writing":                                        "RHET_COMP",
    "Literacy in Composition Studies":                          "RHET_COMP",
    "Basic Writing e-Journal":                                  "RHET_COMP",
    "Advances in the History of Rhetoric":                      "RHET_COMP",
    "Peitho":                                                   "RHET_COMP",
    "Present Tense: A Journal of Rhetoric in Society":          "RHET_COMP",
    "KB Journal: The Journal of the Kenneth Burke Society":     "RHET_COMP",
    "Poroi":                                                    "RHET_COMP",
    "Pre/Text":                                                 "RHET_COMP",

    # ── OTHER ──
    "Writing Center Journal":                                   "OTHER",
    "Praxis: A Writing Center Journal":                         "OTHER",
    "Writing Lab Newsletter":                                   "OTHER",
    "The Peer Review":                                          "OTHER",
    "Across the Disciplines":                                   "OTHER",
    "The WAC Journal":                                          "OTHER",
    "Journal of Writing Analytics":                             "OTHER",
    "Prompt: A Journal of Academic Writing Assignments":        "OTHER",
    "Community Literacy Journal":                               "OTHER",
    "Reflections: A Journal of Community-Engaged Writing and Rhetoric": "OTHER",
    "Journal of Multimodal Rhetorics":                          "OTHER",
    "Writing on the Edge":                                      "OTHER",
    "Journal of Writing Research":                              "OTHER",
    "Journal of Academic Writing":                              "OTHER",
    "Writing and Pedagogy":                                     "OTHER",
    "Res Rhetorica":                                            "OTHER",
    "Rhetorica":                                                "OTHER",
    "Argumentation":                                            "OTHER",
}


def get_journal_group(journal_name):
    """Return 'TPC', 'RHET_COMP', or 'OTHER' for a given journal name."""
    return JOURNAL_GROUPS.get(journal_name, "OTHER")


GROUP_LABELS = {
    "TPC":       "Technical & Professional Communication",
    "RHET_COMP": "Rhetoric & Composition",
    "OTHER":     "Other / Adjacent",
}

GROUP_COLORS = {
    "TPC":       "#5a3e28",
    "RHET_COMP": "#3a5a28",
    "OTHER":     "#9c9890",
}


# ── Sidebar clusters (richer 7-way grouping used by the customization layer) ──

# Pulls the canonical sidebar grouping from journals.JOURNAL_GROUPS so a
# rename in one place propagates to both the sidebar nav and the Datastories
# filters. The slug form (lowercased, hyphenated) is what the JS filter
# component sends; the resolver below maps slug -> list of journal names.

def _slug(label):
    return (
        label.lower()
        .replace(" & ", "-and-")
        .replace(" / ", "-")
        .replace(" ", "-")
    )


def _build_clusters():
    """Lazy import of journals.JOURNAL_GROUPS to avoid circular-import issues
    at package load (journals.py is the source of truth for which journals
    belong to which sidebar cluster)."""
    from journals import JOURNAL_GROUPS as _SIDEBAR
    out = {}
    order = []
    for label, names in _SIDEBAR:
        slug = _slug(label)
        out[slug] = {"label": label, "journals": list(names)}
        order.append(slug)
    return out, order


_CLUSTERS_CACHE = None


def get_clusters():
    """Return ({slug: {label, journals}}, [ordered slugs]). Cached."""
    global _CLUSTERS_CACHE
    if _CLUSTERS_CACHE is None:
        _CLUSTERS_CACHE = _build_clusters()
    return _CLUSTERS_CACHE


def resolve_cluster(slug):
    """slug -> list of journal names, or None if slug is missing / empty."""
    if not slug:
        return None
    clusters, _ = get_clusters()
    entry = clusters.get(slug)
    return list(entry["journals"]) if entry else None


def cluster_label(slug):
    """slug -> human-readable label, or slug if unknown."""
    if not slug:
        return ""
    clusters, _ = get_clusters()
    entry = clusters.get(slug)
    return entry["label"] if entry else slug


def all_cluster_slugs():
    """Ordered list of cluster slugs, matching the sidebar order."""
    _, order = get_clusters()
    return list(order)
