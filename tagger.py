"""
tagger.py — Automatic subject-tag assignment for Rhet-Comp Index.

Tags are drawn from a curated controlled vocabulary of ~50 terms relevant
to rhetoric and composition studies. Matching is case-insensitive substring
search on article title + abstract combined.

The vocabulary is intentionally conservative: each trigger phrase is specific
enough to avoid false positives while broad enough to match common usage.

Usage:
    from tagger import auto_tag
    tags = auto_tag(title, abstract)   # returns "|tag1|tag2|" or None
    tags = auto_tag(title, None)       # works with title only (scraped articles)
"""

# ── Controlled vocabulary ──────────────────────────────────────────────────────
# Structure: "display tag" → [list of trigger phrases]
# Phrases are matched as substrings of lowercased (title + " " + abstract).
# Only one phrase needs to match to apply the tag.

VOCAB = {

    # ── Pedagogy & Teaching ───────────────────────────────────────────────────

    "first-year composition": [
        "first-year composition", "first year composition",
        "first-year writing", "first year writing",
        "fyc", "freshman composition", "introductory composition",
    ],
    "writing pedagogy": [
        "writing pedagogy", "teaching writing", "composition pedagogy",
        "pedagogy of writing", "writing instruction", "teach composition",
    ],
    "writing centers": [
        "writing center", "writing centre", "writing lab",
        "writing tutor", "tutoring", "tutor training", "peer tutoring",
    ],
    "basic writing": [
        "basic writing", "developmental writing", "remedial writing",
        "underprepared writers", "basic writers",
    ],
    "writing program administration": [
        "writing program administration", "wpa", "writing program director",
        "program administrator", "directed self-placement",
        "writing program assessment", "writing programs",
    ],
    "two-year college": [
        "two-year college", "two year college", "community college",
        "technical college", "2-year college",
    ],
    "assessment": [
        "writing assessment", "portfolio assessment", "evaluating student writing",
        "programmatic assessment", "large-scale assessment", "rubric",
        "outcomes assessment", "placement testing",
    ],
    "curriculum design": [
        "curriculum design", "course design", "course development",
        "learning outcomes", "program design", "course sequence",
    ],
    "service learning": [
        "service learning", "service-learning", "community-engaged writing",
        "civic writing", "community writing project",
    ],
    "graduate education": [
        "graduate education", "graduate students", "doctoral program",
        "phd program", "graduate writing", "ta training", "teaching assistants",
        "graduate seminar",
    ],

    # ── Rhetorical & Composition Theory ──────────────────────────────────────

    "rhetorical theory": [
        "rhetorical theory", "classical rhetoric", "aristotle",
        "cicero", "quintilian", "sophist", "epideictic",
        "deliberative rhetoric", "forensic rhetoric", "kenneth burke",
        "neo-aristotelian",
    ],
    "composition theory": [
        "composition theory", "writing theory", "expressivist",
        "expressivism", "social constructivist", "process theory",
        "post-process", "current-traditional",
    ],
    "genre theory": [
        "genre theory", "genre studies", "genre pedagogy",
        "activity theory", "uptake", "genre acquisition",
        "genre awareness", "genre-based",
    ],
    "discourse analysis": [
        "discourse analysis", "discourse community", "critical discourse",
        "discourse studies",
    ],
    "literacy studies": [
        "literacy studies", "new literacy studies", "multiliteracies",
        "academic literacy", "information literacy", "critical literacy",
        "vernacular literacy", "literacy sponsorship", "sponsorship of literacy",
    ],
    "transfer": [
        "transfer of learning", "transfer of writing", "knowledge transfer",
        "writing transfer", "transfer theory", "far transfer", "near transfer",
        "high road transfer", "dispositions for transfer",
    ],
    "threshold concepts": [
        "threshold concepts", "troublesome knowledge", "threshold concept",
        "liminal", "liminality",
    ],
    "writing across the curriculum": [
        "writing across the curriculum", "wac", "writing in the disciplines",
        "wid", "discipline-specific writing", "writing to learn",
        "writing in stem", "writing-intensive",
    ],

    # ── Research Methods ─────────────────────────────────────────────────────

    "archival research": [
        "archival research", "archival rhetoric", "archival work",
        "historical rhetoric", "rhetorical history", "recovery",
        "recovering voices",
    ],
    "empirical research": [
        "empirical research", "empirical study", "research methods",
        "mixed methods", "survey research", "longitudinal study",
    ],
    "qualitative research": [
        "qualitative research", "qualitative study", "ethnography",
        "case study", "think-aloud protocol", "grounded theory",
        "thematic analysis", "interview study",
    ],
    "quantitative research": [
        "quantitative research", "quantitative study", "statistical",
        "corpus analysis", "corpus study", "text analysis",
    ],

    # ── Digital & Multimodal ─────────────────────────────────────────────────

    "digital rhetoric": [
        "digital rhetoric", "digital writing", "digital literacy",
        "digital media", "digital composing", "networked writing",
        "computational rhetoric", "digital humanities",
    ],
    "multimodality": [
        "multimodal", "multimodality", "visual rhetoric",
        "visual communication", "image", "design", "affordance",
        "semiotic", "mode",
    ],
    "social media": [
        "social media", "twitter", "facebook", "instagram", "tiktok",
        "youtube", "platform rhetoric", "algorithmic", "viral",
        "online discourse",
    ],
    "online writing instruction": [
        "online writing", "distance learning", "online learning",
        "online course", "hybrid course", "asynchronous",
        "learning management", "lms", "canvas", "blackboard",
    ],
    "artificial intelligence": [
        "artificial intelligence", "ai writing", "generative ai",
        "chatgpt", "large language model", "llm", "machine learning",
        "automated writing", "automated feedback", "gpt",
    ],

    # ── Technical & Professional ─────────────────────────────────────────────

    "technical communication": [
        "technical communication", "technical writing", "technical documentation",
        "user experience", "ux writing", "information design",
        "usability",
    ],
    "professional writing": [
        "professional writing", "professional communication", "business writing",
        "workplace writing", "workplace communication", "organizational communication",
        "professional discourse",
    ],
    "scientific writing": [
        "scientific writing", "science communication", "stem writing",
        "research article", "imrad", "lab report",
    ],

    # ── Identity, Equity & Justice ───────────────────────────────────────────

    "race and writing": [
        "race", "racism", "antiracist", "anti-racist", "racial justice",
        "whiteness", "bipoc", "black students", "latinx", "chicanx",
        "indigenous", "decolonial", "white supremacy", "equity",
    ],
    "gender and writing": [
        "feminist rhetoric", "feminism", "women writers",
        "lgbtq", "queer rhetoric", "transgender", "nonbinary",
        "gender and writing",
    ],
    "disability studies": [
        "disability", "accessibility", "universal design for learning",
        "udl", "neurodiversity", "dyslexia", "accommodation",
        "ableism", "crip theory",
    ],
    "translingualism": [
        "translingualism", "translingual", "code-meshing",
        "world englishes", "linguistic diversity", "translanguaging",
        "language difference",
    ],
    "multilingual writers": [
        "multilingual", "second language writing", "l2 writing",
        "esl", "efl", "english language learners", "ell",
        "international students", "generation 1.5", "esol",
    ],

    # ── Public & Civic Rhetoric ───────────────────────────────────────────────

    "public rhetoric": [
        "public rhetoric", "public discourse", "civic rhetoric",
        "political rhetoric", "public sphere", "counterpublic",
        "rhetorical public",
    ],
    "community literacy": [
        "community literacy", "community-based writing",
        "community engagement", "neighborhood literacy",
    ],

    # ── Embodiment & Place ────────────────────────────────────────────────────

    "body and rhetoric": [
        "embodied rhetoric", "embodiment", "somatic", "body rhetoric",
        "material rhetoric",
    ],
    "place and space": [
        "sense of place", "spatial rhetoric", "landscape",
        "place-based writing", "geography of rhetoric",
    ],

    # ── Writing Process & Practice ────────────────────────────────────────────

    "revision": [
        "revision", "peer review", "peer response",
        "response to writing", "feedback on writing",
        "teacher feedback", "written feedback",
    ],
    "voice and style": [
        "voice", "style", "sentence-level", "prose style",
        "rhetorical style", "clarity", "sentence combining",
    ],
    "argument": [
        "argumentation", "toulmin", "stasis theory",
        "logos", "ethos", "pathos", "kairos", "claim and evidence",
    ],
    "creative writing": [
        "creative writing", "creative nonfiction", "creative composition",
        "imaginative writing", "flash fiction", "literary nonfiction",
    ],
    "affect and writing": [
        "affect", "writing anxiety", "writer's block",
        "motivation and writing", "mindset", "confidence",
        "emotion and writing",
    ],
}


# ── Tagger function ────────────────────────────────────────────────────────────

def auto_tag(title: str | None, abstract: str | None) -> str | None:
    """
    Match title and abstract against the controlled vocabulary.

    Returns a pipe-delimited string like "|transfer|genre theory|revision|"
    with leading and trailing pipes (for clean substring matching in SQL),
    or None if no terms matched.

    Tags are applied if any trigger phrase for that tag is found as a
    case-insensitive substring of the combined title + abstract text.
    """
    text = ((title or "") + " " + (abstract or "")).lower()
    if not text.strip():
        return None

    matched = []
    for tag, phrases in VOCAB.items():
        for phrase in phrases:
            if phrase in text:
                matched.append(tag)
                break  # only add each tag once regardless of how many phrases match

    if not matched:
        return None

    # Pipe-delimited with surrounding pipes: "|tag1|tag2|"
    # This format makes SQL matching clean:  tags LIKE '%|tag|%'
    return "|" + "|".join(matched) + "|"
