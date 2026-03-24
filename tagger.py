"""
tagger.py — Automatic subject-tag assignment for Rhet-Comp Index.

Tags are drawn from a curated controlled vocabulary of 44 terms relevant
to rhetoric and composition studies. Matching is case-insensitive substring
search on article title + abstract combined.

Vocabulary design principles (v2, March 2026):
  - Every trigger phrase must be specific enough that its presence in a
    rhet/comp journal article is strong evidence for the tag.
  - Single common words ("image", "design", "mode", "race", "voice",
    "style") are NOT used as triggers — they appear in too many unrelated
    contexts. Compound phrases or disciplinary terms are required.
  - "peer review" is excluded from revision triggers: it appears in the
    methods sections of virtually every empirical paper.
  - "multilingual" alone is excluded: it triggers on "multilingual
    organizations" and "multilingual corporate settings." Compound forms
    with writing/student/composition are required.
  - Scope notes below each tag follow CompPile glossary conventions and
    the Bedford Bibliography taxonomy.

Usage:
    from tagger import auto_tag
    tags = auto_tag(title, abstract)   # returns "|tag1|tag2|" or None
    tags = auto_tag(title, None)       # works with title only
"""

# ── Controlled vocabulary ──────────────────────────────────────────────────────
# Structure: "display tag" → [list of trigger phrases]
# All phrases matched as substrings of lowercased (title + " " + abstract).
# Only one phrase needs to match to apply the tag.

VOCAB = {

    # ── Pedagogy & Teaching ───────────────────────────────────────────────────

    # Scope: Introductory college writing courses, typically taken in the
    # first year. Includes FYC program design, pedagogy, student outcomes.
    "first-year composition": [
        "first-year composition", "first year composition",
        "first-year writing", "first year writing",
        "fyc", "freshman composition", "introductory composition",
    ],

    # Scope: Approaches to teaching writing at any level. Includes
    # pedagogical theory, classroom practice, instructional design.
    "writing pedagogy": [
        "writing pedagogy", "teaching writing", "composition pedagogy",
        "pedagogy of writing", "writing instruction", "teach composition",
    ],

    # Scope: Writing center theory, administration, and practice. Includes
    # tutoring methodology, tutor training, center design.
    # NOTE: bare "tutoring" removed — too broad (math tutoring, sports coaching, etc.)
    "writing centers": [
        "writing center", "writing centre", "writing lab",
        "writing tutor", "tutor training", "peer tutoring",
        "writing center theory", "writing center administration",
    ],

    # Scope: Writing courses and programs for underprepared students.
    # Includes basic writing pedagogy, placement, and developmental programs.
    "basic writing": [
        "basic writing", "developmental writing", "remedial writing",
        "underprepared writers", "basic writers",
    ],

    # Scope: Administration of writing programs, including WPA roles,
    # labor practices, program assessment, and directed self-placement.
    # NOTE: bare "wpa" kept but bounded by longer alternatives to reduce ambiguity.
    "writing program administration": [
        "writing program administration", "writing program administrator",
        "writing program director", "directed self-placement",
        "writing program assessment", "wpa journal",
        "council of writing program", "program administrator",
    ],

    # Scope: Two-year and community colleges as sites of writing instruction.
    # Includes community college writing programs, transfer students, and
    # open-enrollment contexts.
    "two-year college": [
        "two-year college", "two year college", "community college",
        "technical college", "2-year college",
    ],

    # Scope: Evaluation of student writing and writing programs. Includes
    # portfolio assessment, rubrics, placement, and large-scale assessment.
    # NOTE: bare "assessment" removed — too broad for the full site.
    "assessment": [
        "writing assessment", "portfolio assessment", "evaluating student writing",
        "programmatic assessment", "large-scale assessment", "rubric",
        "outcomes assessment", "placement testing", "placement exam",
        "direct assessment", "indirect assessment", "holistic scoring",
    ],

    # Scope: Design of writing courses and programs. Includes curriculum
    # theory, course development, and sequencing.
    # NOTE: "learning outcomes" removed — appears in all assessment literature.
    "curriculum design": [
        "curriculum design", "course design", "course development",
        "program design", "course sequence", "syllabus design",
        "curriculum development", "course redesign",
    ],

    # Scope: Writing in community contexts for civic purposes. Includes
    # community-based writing courses, civic engagement projects.
    "service learning": [
        "service learning", "service-learning", "community-engaged writing",
        "civic writing", "community writing project",
    ],

    # Scope: Writing education at the graduate level, including doctoral
    # programs, TA preparation, and graduate writing seminars.
    # NOTE: "graduate students" removed — appears when used as study participants
    # in any research regardless of subject.
    "graduate education": [
        "graduate education", "doctoral program", "phd program",
        "graduate writing", "ta training", "teaching assistants",
        "graduate seminar", "graduate curriculum", "doctoral writing",
        "master's program", "graduate level writing",
    ],

    # ── Rhetorical & Composition Theory ──────────────────────────────────────

    # Scope: Theoretical frameworks in rhetoric, classical and contemporary.
    # Includes Aristotle, Burke, and rhetorical traditions.
    "rhetorical theory": [
        "rhetorical theory", "classical rhetoric", "aristotle",
        "cicero", "quintilian", "sophist", "epideictic",
        "deliberative rhetoric", "forensic rhetoric", "kenneth burke",
        "neo-aristotelian", "burkean", "rhetoric of inquiry",
    ],

    # Scope: Theoretical frameworks in composition studies. Includes
    # expressivism, social constructivism, process theory, and post-process.
    "composition theory": [
        "composition theory", "writing theory", "expressivist",
        "expressivism", "social constructivist", "process theory",
        "post-process", "current-traditional",
    ],

    # Scope: Genre as a social and rhetorical action. Includes genre-based
    # pedagogy, genre acquisition, and activity theory.
    "genre theory": [
        "genre theory", "genre studies", "genre pedagogy",
        "activity theory", "uptake", "genre acquisition",
        "genre awareness", "genre-based",
    ],

    # Scope: Analysis of discourse structures, communities, and practices.
    "discourse analysis": [
        "discourse analysis", "discourse community", "critical discourse",
        "discourse studies",
    ],

    # Scope: New Literacy Studies, multiple literacies, and literacy as
    # social practice. Distinct from general reading/writing instruction.
    # NOTE: "academic literacy" and "information literacy" removed — too broad;
    # information literacy is primarily library science.
    "literacy studies": [
        "literacy studies", "new literacy studies", "multiliteracies",
        "critical literacy", "vernacular literacy", "literacy sponsorship",
        "sponsorship of literacy", "literacy practices",
    ],

    # Scope: Transfer of writing knowledge and skills across contexts.
    # Includes high road/low road, far/near transfer frameworks.
    "transfer": [
        "transfer of learning", "transfer of writing", "knowledge transfer",
        "writing transfer", "transfer theory", "far transfer", "near transfer",
        "high road transfer", "dispositions for transfer",
    ],

    # Scope: Threshold concepts framework applied to writing studies.
    # Includes troublesome knowledge, conceptual threshold.
    # NOTE: "liminal"/"liminality" removed — appear in archival and historical
    # rhetoric without being about threshold concepts.
    "threshold concepts": [
        "threshold concepts", "troublesome knowledge", "threshold concept",
        "threshold concept theory",
    ],

    # Scope: Writing as a learning tool and writing across disciplines.
    # Includes WAC programs, WID, and writing-intensive courses.
    # NOTE: bare "wac" removed (too short); bare "writing to learn" removed
    # (appears in general education); bare "wid" removed (too short).
    "writing across the curriculum": [
        "writing across the curriculum", "writing in the disciplines",
        "wac program", "wac pedagogy", "wac curriculum", "wac initiative",
        "discipline-specific writing", "writing-intensive",
    ],

    # ── Research Methods ─────────────────────────────────────────────────────

    # Scope: Archival and historical methods in rhetoric and composition.
    # Includes recovery projects and rhetorical history.
    "archival research": [
        "archival research", "archival rhetoric", "archival work",
        "historical rhetoric", "rhetorical history", "recovery",
        "recovering voices", "archival methods",
    ],

    # Scope: Research using systematic evidence collection. Includes mixed
    # methods and survey-based studies of writing.
    # NOTE: "research methods" removed — appears in every methods section.
    "empirical research": [
        "empirical research", "empirical study", "mixed methods",
        "survey research", "longitudinal study",
    ],

    # Scope: Research using observation, interview, and interpretation.
    # Includes ethnography, think-aloud protocols, and case studies of writing.
    # NOTE: bare "case study" removed — too common across all fields.
    "qualitative research": [
        "qualitative research", "qualitative study", "ethnography",
        "think-aloud protocol", "grounded theory",
        "thematic analysis", "interview study", "qualitative methods",
        "qualitative case study",
    ],

    # Scope: Research using statistical analysis and corpus methods.
    # NOTE: "statistical" removed — appears in any empirical paper regardless
    # of subject. "text analysis" removed — too broad.
    "quantitative research": [
        "quantitative research", "quantitative study",
        "corpus analysis", "corpus study", "corpus linguistics",
        "computational analysis", "quantitative methods",
    ],

    # ── Digital & Multimodal ─────────────────────────────────────────────────

    # Scope: Rhetoric in digital environments, networked writing, digital
    # humanities applications to rhetoric.
    # NOTE: "digital media" removed — too broad (appears in media studies,
    # communications, marketing, etc.).
    "digital rhetoric": [
        "digital rhetoric", "digital writing", "digital literacy",
        "digital composing", "networked writing",
        "computational rhetoric", "digital humanities",
    ],

    # Scope: Communication using multiple modes (visual, audio, spatial,
    # gestural). Grounded in social semiotics and New London Group framework.
    # NOTE: "image", "design", "mode" removed — these are extremely common
    # substrings that generate thousands of false positives. Each was firing
    # on articles about "research design", "image processing", "model", etc.
    # "affordance" kept — it is a discipline-specific technical term.
    "multimodality": [
        "multimodal", "multimodality", "visual rhetoric",
        "visual communication", "affordance", "semiotic",
        "new media composing", "multimodal composition",
        "multimodal literacy", "multimodal pedagogy",
        "modes of communication",
    ],

    # Scope: Writing and rhetoric in social media environments. Includes
    # platform-specific studies and participatory culture.
    # NOTE: "algorithmic" removed — appears in CS/AI contexts broadly.
    "social media": [
        "social media", "twitter", "facebook", "instagram", "tiktok",
        "youtube", "platform rhetoric", "viral content",
        "online discourse", "social networking",
    ],

    # Scope: Teaching writing in fully online or hybrid course formats.
    # Includes asynchronous writing instruction, LMS-based pedagogy.
    "online writing instruction": [
        "online writing instruction", "online writing course",
        "online composition", "distance writing", "hybrid writing",
        "online writing pedagogy", "learning management system",
        "lms", "canvas lms", "blackboard",
    ],

    # Scope: AI tools and their applications in writing and writing pedagogy.
    # Includes generative AI, LLMs, ChatGPT, automated feedback.
    "artificial intelligence": [
        "artificial intelligence", "ai writing", "generative ai",
        "chatgpt", "large language model", "llm", "machine learning",
        "automated writing", "automated feedback", "gpt-",
    ],

    # ── Technical & Professional ─────────────────────────────────────────────

    # Scope: Communication in technical and scientific fields. Includes
    # technical documentation, UX writing, usability.
    "technical communication": [
        "technical communication", "technical writing", "technical documentation",
        "user experience", "ux writing", "information design",
        "usability",
    ],

    # Scope: Writing and communication in professional and workplace contexts.
    # NOTE: "organizational communication" removed — it is a distinct field
    # in communication studies covering management and org behavior, not writing.
    "professional writing": [
        "professional writing", "professional communication", "business writing",
        "workplace writing", "workplace communication",
        "professional discourse", "business communication",
    ],

    # Scope: Writing in scientific and STEM disciplines. Includes IMRaD
    # structure, science communication, and lab report pedagogy.
    "scientific writing": [
        "scientific writing", "science communication", "stem writing",
        "imrad", "lab report", "scientific communication",
        "science writing",
    ],

    # ── Identity, Equity & Justice ───────────────────────────────────────────

    # Scope: Race, racism, and anti-racist practice in writing, rhetoric,
    # and composition. Grounded in writing and literacy contexts.
    # NOTE: bare "race", "equity", "indigenous", "decolonial" removed —
    # these appear in articles from management, education, and policy contexts
    # that have nothing to do with writing or rhetoric. Compound phrases
    # with writing/rhetoric/composition now required.
    "race and writing": [
        "race and writing", "racism and writing", "antiracist writing",
        "anti-racist writing", "antiracist pedagogy", "anti-racist pedagogy",
        "racial justice in writing", "whiteness and writing",
        "bipoc students", "black students", "latinx students", "chicanx",
        "decolonial composition", "decolonial rhetoric",
        "racial literacy", "raciolinguistics",
        "critical race theory and writing", "equity in writing",
        "white supremacy and writing",
    ],

    # Scope: Gender, feminism, and sexuality in rhetoric and writing.
    # Includes feminist rhetorical theory, women's rhetoric.
    "gender and writing": [
        "feminist rhetoric", "feminist composition", "feminism and writing",
        "women writers", "women's rhetoric", "women and writing",
        "lgbtq", "queer rhetoric", "transgender", "nonbinary",
        "gender and writing",
    ],

    # Scope: Disability, accessibility, and neurodiversity in writing and
    # rhetoric. Includes UDL, crip theory, and disability rhetoric.
    # NOTE: bare "accommodation" removed — generic in education broadly.
    "disability studies": [
        "disability", "accessibility in writing", "universal design for learning",
        "udl", "neurodiversity", "dyslexia", "disability rhetoric",
        "ableism", "crip theory", "disability studies",
        "accessible writing",
    ],

    # Scope: Translingual orientation toward language difference in writing.
    # As distinct from multilingual writers (a population) — translingualism
    # is a theoretical framework embracing language variation as resource.
    "translingualism": [
        "translingualism", "translingual", "code-meshing",
        "world englishes", "linguistic diversity", "translanguaging",
        "language difference", "language diversity",
    ],

    # Scope: Writing research and pedagogy focused on writers composing
    # in a language other than their first. Includes L2 writing, ESL/EFL
    # composition, and heritage language writing.
    # As distinct from translingualism (a theoretical framework).
    # NOTE: bare "multilingual" removed — fires on "multilingual organizations",
    # "multilingual corporations", etc. Compound forms required.
    # "international students" removed — appears in unrelated contexts
    # (e.g., business school studies of international student workers).
    "multilingual writers": [
        "multilingual writer", "multilingual student", "multilingual composition",
        "multilingual writing", "second language writing", "l2 writing",
        "l2 writer", "esl writing", "efl writing",
        "english language learner", "generation 1.5", "esol",
        "english as a second language", "english as a foreign language",
        "heritage language writer", "nonnative english", "non-native english",
        "second-language writer",
    ],

    # ── Public & Civic Rhetoric ───────────────────────────────────────────────

    # Scope: Rhetoric in public life. Includes civic discourse, political
    # rhetoric, public sphere theory, and counterpublics.
    "public rhetoric": [
        "public rhetoric", "public discourse", "civic rhetoric",
        "political rhetoric", "public sphere", "counterpublic",
        "rhetorical public", "public argument",
    ],

    # Scope: Literacy practices in and with communities outside academic
    # settings. Grounded in community literacy theory.
    # NOTE: "community engagement" removed — appears in service learning,
    # higher education broadly, and is not specific to literacy/writing.
    "community literacy": [
        "community literacy", "community-based writing",
        "community literacy project", "neighborhood literacy",
        "community-based literacy",
    ],

    # ── Embodiment & Place ────────────────────────────────────────────────────

    # Scope: Rhetoric and writing as embodied, material practices.
    "body and rhetoric": [
        "embodied rhetoric", "embodiment", "somatic rhetoric",
        "body rhetoric", "material rhetoric", "corporeal rhetoric",
    ],

    # Scope: Place, space, and geography in rhetoric and writing.
    # Includes place-based pedagogies and spatial rhetoric.
    # NOTE: "landscape" removed — too broad (nature writing, environmental
    # science, urban planning all use this term).
    "place and space": [
        "sense of place", "spatial rhetoric", "place-based writing",
        "geography of rhetoric", "rhetorical geography",
        "spatial turn", "place and writing", "place-based pedagogy",
    ],

    # ── Writing Process & Practice ────────────────────────────────────────────

    # Scope: Revision practices, peer response, and feedback on writing.
    # NOTE: "peer review" removed — it appears in the methods section of
    # virtually every empirical research article regardless of subject,
    # generating thousands of false positives.
    "revision": [
        "revision", "peer response", "response to writing",
        "feedback on writing", "teacher feedback", "written feedback",
        "revision strategies", "revision process", "responding to writing",
        "reader response",
    ],

    # Scope: Authorial voice, prose style, and sentence-level concerns.
    # NOTE: bare "voice" and bare "style" removed — both are among the
    # most common words in English and generate massive false-positive rates.
    "voice and style": [
        "writer's voice", "voice in writing", "prose style",
        "sentence-level", "rhetorical style", "sentence combining",
        "authorial voice", "stylistic choices", "voice and style",
        "stylistic analysis",
    ],

    # Scope: Argumentation, argument structure, and rhetorical appeals.
    "argument": [
        "argumentation", "toulmin", "stasis theory",
        "logos", "ethos", "pathos", "kairos", "claim and evidence",
        "argumentative writing", "argument structure",
    ],

    # Scope: Creative writing as practice and pedagogy. Includes creative
    # nonfiction, flash fiction, and literary writing courses.
    "creative writing": [
        "creative writing", "creative nonfiction", "creative composition",
        "imaginative writing", "flash fiction", "literary nonfiction",
    ],

    # Scope: Emotional and motivational dimensions of writing. Includes
    # writing anxiety, affect theory applied to writing, and motivation.
    # NOTE: "mindset" removed — growth mindset literature is broadly used
    # in education and not specific to writing. "confidence" removed — too
    # generic. "affect" kept — it is a rhet/comp technical term.
    "affect and writing": [
        "affect", "writing anxiety", "writer's block",
        "motivation and writing", "writing motivation",
        "emotion and writing", "emotional writing", "writing confidence",
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
