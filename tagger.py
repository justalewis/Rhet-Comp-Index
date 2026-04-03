"""
tagger.py — Automatic subject-tag assignment for Pinakes (Rhet-Comp Index).

Tags are drawn from a 58-term controlled vocabulary grounded in:
  - CompPile's disciplinary glossary (~6,000 terms, validated via keyword
    mapping and lemmatization across both passes, April 2026)
  - The Bedford Bibliography subject taxonomy
  - The existing Pinakes tag vocabulary (v2, March 2026)

Organized into 11 families:
  1. History & Theory of Rhetoric       (10 tags)
  2. Pedagogy & Curriculum              (11 tags)
  3. Writing Process & Practice          (6 tags)
  4. Assessment                          (3 tags)
  5. Writing Centers                     (2 tags)
  6. Institutional & Professional        (4 tags)
  7. Research Methods                    (4 tags)
  8. Digital & Multimodal                (5 tags)
  9. Language & Linguistics              (5 tags)
  10. Identity, Equity & Justice          (5 tags)
  11. Affect, Embodiment & Place          (3 tags)

Matching:
  - Case-insensitive, word-boundary–aware regex (\b) for single words
  - Substring phrase match for multi-word triggers
  - Applied to lowercased (title + " " + abstract)

Vocabulary design principles (v3, April 2026):
  - Every trigger must be specific enough that its presence in a rhet/comp
    journal article title or abstract is strong evidence for the tag.
  - Single common words are NOT used as bare triggers. Compound phrases or
    disciplinary terms are required — except where a word is so discipline-
    specific (e.g., "enthymeme", "translingualism") that false positives
    are essentially impossible.
  - Scope notes follow CompPile glossary conventions, Bedford Bibliography
    taxonomy, and WPA Outcomes Statement terminology.

Usage:
    from tagger import auto_tag
    tags = auto_tag(title, abstract)   # returns "|tag1|tag2|" or None
    tags = auto_tag(title, None)       # works with title only
"""

import re

# ── Controlled vocabulary ───────────────────────────────────────────────────────
# Structure: "display tag" → [list of trigger phrases]
#
# Phrase matching rules:
#   - Multi-word phrases: substring match on lowercased text (fast, accurate)
#   - Single words: wrapped with \b in the matcher for word-boundary precision
#
# Only one phrase needs to match to apply the tag.

VOCAB = {

    # ══════════════════════════════════════════════════════════════════════
    # Family 1: History & Theory of Rhetoric
    # ══════════════════════════════════════════════════════════════════════

    # Scope: Rhetoric in ancient Greece and Rome. Includes Aristotle,
    # Cicero, Quintilian, the sophists, progymnasmata, and the three
    # rhetorical genres (epideictic, deliberative, forensic). As distinct
    # from "modern rhetorical theory" (20th-century frameworks).
    "classical rhetoric": [
        "classical rhetoric", "ancient rhetoric", "greek rhetoric",
        "roman rhetoric", "aristotle", "cicero", "quintilian",
        "isocrates", "gorgias", "sophist", "sophistic",
        "epideictic", "deliberative rhetoric", "forensic rhetoric",
        "enthymeme", "progymnasmata", "stasis theory",
        "neo-aristotelian", "canons of rhetoric",
        "trivium", "ars rhetorica",
    ],

    # Scope: Rhetorical theory developed from the 20th century onward.
    # Includes Kenneth Burke, Chaïm Perelman, the rhetorical situation,
    # Habermas, Bakhtin, and post-structural rhetorical frameworks.
    # As distinct from "classical rhetoric" (pre-modern traditions) and
    # "rhetorical criticism" (applied method rather than theory).
    "modern rhetorical theory": [
        "kenneth burke", "burkean", "dramatism", "terministic screen",
        "identification", "consubstantiality", "symbolic action",
        "chaim perelman", "new rhetoric",
        "rhetorical situation", "rhetorical exigence",
        "jurgen habermas", "mikhail bakhtin",
        "modern rhetoric", "contemporary rhetoric",
        "rhetorical agency", "rhetorical ontology",
        "rhetoric of inquiry",
        "rhetorical theory",  # generic modern theory discussion
    ],

    # Scope: Methods for analyzing rhetorical artifacts, texts, and
    # speeches. Includes pentadic analysis, fantasy theme analysis,
    # narrative criticism, and feminist criticism of public address.
    # As distinct from "modern rhetorical theory" (theoretical frameworks)
    # and "discourse analysis" (linguistic/textual methods).
    "rhetorical criticism": [
        "rhetorical criticism", "rhetorical analysis",
        "critical rhetoric", "artifact analysis",
        "public address criticism", "speech criticism",
        "pentadic analysis", "fantasy theme analysis",
        "narrative criticism", "ideological criticism",
        "close reading of rhetoric",
        "rhetoric", "rhetorical",  # generic discussion of rhetoric/rhetorical concepts
    ],

    # Scope: Theoretical frameworks specific to composition studies.
    # Includes expressivism, social constructivism, process theory,
    # post-process theory, and expressivist vs. social-epistemic debate.
    "composition theory": [
        "composition theory", "writing theory", "expressivist",
        "expressivism", "social constructivist", "process theory",
        "post-process", "current-traditional",
        "social-epistemic", "expressivist rhetoric",
        "writing as process",
    ],

    # Scope: Genre as social action and rhetorical response to recurring
    # situations. Includes rhetorical genre studies, activity theory,
    # genre acquisition, uptake, and genre-based pedagogy.
    "genre theory": [
        "genre theory", "genre studies", "genre pedagogy",
        "activity theory", "uptake", "genre acquisition",
        "genre awareness", "genre-based",
        "rhetorical genre studies",
        "genre", "genres",  # generic genre discussion
    ],

    # Scope: Analysis of discourse structures, communities, and practices.
    # Includes critical discourse analysis, conversation analysis,
    # speech act theory, and register analysis.
    "discourse analysis": [
        "discourse analysis", "discourse community",
        "critical discourse analysis", "discourse studies",
        "conversation analysis", "speech act",
        "systemic functional", "register analysis",
        "pragmatics", "semantic analysis", "semantics",
        "text analysis", "textual analysis", "discourse pattern",
        "discourse",  # generic discourse discussion
    ],

    # Scope: Feminist traditions in rhetoric and writing. Includes
    # women's rhetorical history, feminist rhetorical theory, invitational
    # rhetoric, and rhetorical listening. As distinct from "gender and
    # writing" (which addresses gender in composition pedagogy broadly).
    "feminist rhetorics": [
        "feminist rhetoric", "feminist rhetorics",
        "women's rhetoric", "women rhetors",
        "feminist rhetorical theory", "feminist composition",
        "invitational rhetoric", "rhetorical listening",
        "feminist historiography of rhetoric",
        "recovering women's rhetoric",
    ],

    # Scope: African American rhetorical traditions, including signifying,
    # the Black church tradition, HBCU literacy education, and African-
    # centered rhetorical theory. Includes recovery of Black rhetorical
    # history. As distinct from "race and writing" (the broader composition
    # studies treatment of race, racism, and anti-racism in writing).
    "african american rhetorics": [
        "african american rhetoric", "african-american rhetoric",
        "black rhetoric", "signifying",
        "afrocentric", "afrocentrism", "afrocentricity",
        "hbcu", "double consciousness",
        "call and response rhetoric",
        "african american rhetorical tradition",
        "smitherman", "nommo",
    ],

    # Scope: Rhetoric and writing as culturally situated practices.
    # Includes indigenous rhetorics, vernacular rhetorics, ceremonial
    # rhetoric, and cross-cultural/intercultural rhetoric. Grounded in
    # cultural rhetorics as a field distinct from postcolonial theory.
    "cultural rhetorics": [
        "cultural rhetoric", "cultural rhetorics",
        "indigenous rhetoric", "vernacular rhetoric",
        "ceremonial rhetoric", "intercultural rhetoric",
        "cross-cultural rhetoric", "culturally situated",
        "indigenous composition",
    ],

    # Scope: Rhetoric and writing studies informed by decolonial and
    # postcolonial theory. Includes critique of settler colonialism,
    # decolonizing the curriculum, and Global South perspectives. As
    # distinct from "cultural rhetorics" (cultural situatedness) and
    # "race and writing" (anti-racist composition pedagogy).
    "decolonial rhetorics": [
        "decolonial", "decolonizing", "decolonization",
        "postcolonial rhetoric", "settler colonialism",
        "colonial rhetoric", "decolonizing the curriculum",
        "decolonial composition", "decolonial pedagogy",
        "global south rhetoric",
    ],


    # ══════════════════════════════════════════════════════════════════════
    # Family 2: Pedagogy & Curriculum
    # ══════════════════════════════════════════════════════════════════════

    # Scope: Introductory college writing courses typically taken in the
    # first year. Includes FYC program design, pedagogy, student outcomes,
    # and the general education writing requirement.
    "first-year composition": [
        "first-year composition", "first year composition",
        "first-year writing", "first year writing",
        "fyc", "freshman composition", "introductory composition",
        "english 101", "general education writing",
        "gateway writing course",
    ],

    # Scope: Approaches to teaching writing at any level. Includes
    # pedagogical theory, classroom practice, instructional design,
    # process pedagogy, and transfer of writing knowledge.
    "writing pedagogy": [
        "writing pedagogy", "teaching writing", "composition pedagogy",
        "pedagogy of writing", "writing instruction",
        "teach composition", "writing classroom",
        "process approach", "process pedagogy",
        "pedagogical approach to writing",
        "pedagogy", "pedagogical",  # generic pedagogical discussion
    ],

    # Scope: Writing courses and programs designed for underprepared
    # students. Includes basic writing pedagogy, the work of Mina
    # Shaughnessy, developmental writing, and placement into developmental
    # courses. As distinct from "assessment" (placement mechanisms).
    "basic writing": [
        "basic writing", "developmental writing", "remedial writing",
        "underprepared writers", "basic writers",
        "shaughnessy", "errors and expectations",
        "developmental composition", "struggling writers",
        "foundational writing", "foundational composition",
        "prep writing", "preparatory writing",
    ],

    # Scope: Upper-division writing courses and writing-intensive programs
    # for advanced students. Includes writing majors, writing minors, and
    # advanced rhetoric courses. As distinct from "first-year composition."
    "advanced composition": [
        "advanced composition", "advanced writing",
        "upper-division writing", "upper division writing",
        "advanced rhetoric course", "writing major",
        "writing minor", "honors composition", "honors writing",
        "accelerated writing", "accelerated composition",
        "upper-level writing", "elective writing", "seminar writing",
    ],

    # Scope: Creative writing as practice and pedagogy. Includes fiction,
    # poetry, creative nonfiction, flash fiction, and literary writing
    # courses. Includes the creative writing workshop model.
    "creative writing": [
        "creative writing", "creative nonfiction",
        "creative composition", "imaginative writing",
        "flash fiction", "literary nonfiction",
        "poetry writing", "fiction writing",
        "creative writing workshop",
    ],

    # Scope: Design of writing courses and programs. Includes curriculum
    # theory, course development, sequencing, and learning objectives.
    "curriculum design": [
        "curriculum design", "course design", "course development",
        "program design", "course sequence", "syllabus design",
        "curriculum development", "course redesign",
    ],

    # Scope: Writing as a learning tool and writing embedded across
    # academic disciplines. Includes WAC programs, WID, writing-intensive
    # courses, and writing-to-learn activities.
    "writing across the curriculum": [
        "writing across the curriculum", "writing in the disciplines",
        "wac program", "wac pedagogy", "wac curriculum",
        "wac initiative", "discipline-specific writing",
        "writing-intensive", "writing to learn",
    ],

    # Scope: Writing education at the graduate level. Includes doctoral
    # programs, TA preparation, graduate writing seminars, and the
    # teaching of graduate writing.
    "graduate education": [
        "graduate education", "doctoral program", "phd program",
        "graduate writing", "ta training", "teaching assistants",
        "graduate seminar", "graduate curriculum", "doctoral writing",
        "graduate level writing",
    ],

    # Scope: Writing instruction in two-year and community colleges.
    # Includes open-enrollment contexts, transfer students, and
    # community college writing programs.
    "two-year college": [
        "two-year college", "two year college", "community college",
        "technical college", "2-year college", "junior college",
        "open-enrollment", "transfer pathway",
        "articulation agreement", "community college system",
    ],

    # Scope: Writing and rhetoric in community contexts for civic purposes.
    # Includes community-based writing courses and civic engagement projects.
    "service learning": [
        "service learning", "service-learning",
        "community-engaged writing", "civic writing",
        "community writing project", "community-based learning",
        "public engagement", "experiential learning",
        "community-university partnership", "civic engagement",
    ],

    # Scope: Preparation and professional development of writing teachers.
    # Includes TA preparation programs, teacher research, pre-service
    # teacher education, and faculty development for writing instruction.
    # As distinct from "writing program administration" (programmatic/
    # administrative focus) and "graduate education" (degree programs).
    "teacher development": [
        "teacher development", "teacher preparation",
        "teacher education", "ta preparation",
        "teaching assistant preparation", "gta training",
        "preservice writing teacher", "writing teacher education",
        "teacher research", "practitioner research",
        "teacher as researcher",
    ],


    # ══════════════════════════════════════════════════════════════════════
    # Family 3: Writing Process & Practice
    # ══════════════════════════════════════════════════════════════════════

    # Scope: Revision practices, peer response, teacher feedback, and
    # responding to writing. Includes written commentary, conferencing,
    # and the role of feedback in the writing process.
    # NOTE: "peer review" is excluded — it appears in the methods sections
    # of virtually every empirical article regardless of subject.
    "revision": [
        "revision", "peer response", "response to writing",
        "feedback on writing", "teacher feedback", "written feedback",
        "revision strategies", "revision process",
        "responding to writing", "written commentary",
        "teacher response", "revision instruction",
    ],

    # Scope: Authorial voice, prose style, and sentence-level concerns.
    # Includes style guides, sentence combining, and stylistic analysis.
    # NOTE: bare "voice" and bare "style" excluded — both generate massive
    # false-positive rates across all content areas.
    "voice and style": [
        "writer's voice", "voice in writing", "prose style",
        "sentence-level", "rhetorical style", "sentence combining",
        "authorial voice", "stylistic choices", "voice and style",
        "stylistic analysis", "plain style",
    ],

    # Scope: Argumentation, argument structure, and rhetorical appeals.
    # Includes Toulmin, stasis theory, logos/ethos/pathos, and
    # argumentative writing pedagogy.
    "argument": [
        "argumentation", "toulmin", "stasis theory",
        "logos", "ethos", "pathos", "kairos",
        "claim and evidence", "argumentative writing",
        "argument structure", "rhetorical appeals",
        "making arguments",
    ],

    # Scope: Writing produced jointly by two or more authors. Includes
    # collaborative composing, group writing projects, co-authorship
    # practices, and collaborative writing pedagogy.
    "collaborative writing": [
        "collaborative writing", "collaborative composing",
        "group writing", "co-authorship", "co-writing",
        "collaborative composition", "collaborative authorship",
        "team writing", "writing collaboration",
    ],

    # Scope: Transfer of writing knowledge and skills across contexts.
    # Includes high road/low road, far/near transfer, dispositions for
    # transfer, and threshold concept theory as it relates to transfer.
    "transfer": [
        "transfer of learning", "transfer of writing",
        "knowledge transfer", "writing transfer", "transfer theory",
        "far transfer", "near transfer", "high road transfer",
        "dispositions for transfer",
    ],

    # Scope: Threshold concepts framework applied to writing studies.
    # Includes troublesome knowledge and conceptual threshold.
    "threshold concepts": [
        "threshold concepts", "troublesome knowledge",
        "threshold concept", "threshold concept theory",
        "liminal knowledge", "transformative learning",
        "threshold", "liminality", "conceptual change",
        "meyer and sinclair",
    ],


    # ══════════════════════════════════════════════════════════════════════
    # Family 4: Assessment
    # ══════════════════════════════════════════════════════════════════════

    # Scope: Evaluation of student writing and writing programs. Includes
    # rubrics, holistic scoring, large-scale assessment, outcomes
    # assessment, and placement. As distinct from "portfolios" (one
    # specific assessment format) and "placement testing."
    "assessment": [
        "writing assessment", "evaluating student writing",
        "programmatic assessment", "large-scale assessment",
        "outcomes assessment", "placement testing", "placement exam",
        "directed self-placement", "holistic scoring",
        "analytic scoring", "writing evaluation",
        "assessing writing",
        "assessment", "evaluation",  # generic assessment/evaluation discussion
    ],

    # Scope: Portfolio-based assessment and instruction. Includes
    # e-portfolios, course portfolios, and portfolio-based grading.
    # As distinct from "assessment" broadly.
    "portfolios": [
        "portfolio", "eportfolio", "e-portfolio",
        "writing portfolio", "portfolio assessment",
        "portfolio-based", "portfolio grading",
        "digital portfolio",
    ],

    # Scope: Administration of writing programs, including WPA roles,
    # labor practices, program assessment, and directed self-placement.
    "writing program administration": [
        "writing program administration", "writing program administrator",
        "writing program director", "writing program assessment",
        "wpa journal", "council of writing program",
        "program administrator", "writing administrator",
        "writing program governance",
    ],


    # ══════════════════════════════════════════════════════════════════════
    # Family 5: Writing Centers
    # ══════════════════════════════════════════════════════════════════════

    # Scope: Writing center theory, administration, and practice. Includes
    # writing center design, administration, and center-based pedagogy.
    # As distinct from "peer tutoring" (the tutoring relationship itself).
    "writing centers": [
        "writing center", "writing centre", "writing lab",
        "writing center theory", "writing center administration",
        "writing center research", "writing center practice",
        "iwca",
    ],

    # Scope: The theory and practice of peer writing tutoring. Includes
    # one-on-one writing conferences, peer consultant training, and
    # the tutor-writer relationship. As distinct from "writing centers"
    # (institutional and programmatic focus).
    "peer tutoring": [
        "peer tutor", "peer tutoring", "peer consultant",
        "writing tutor", "tutor training",
        "tutoring session", "tutoring conference",
        "one-on-one writing", "writing consultation",
        "peer writing center",
    ],


    # ══════════════════════════════════════════════════════════════════════
    # Family 6: Institutional & Professional
    # ══════════════════════════════════════════════════════════════════════

    # Scope: Communication in technical and scientific fields. Includes
    # technical documentation, UX writing, usability, and information
    # design. Grounded in the field of technical and professional
    # communication (TPC).
    "technical communication": [
        "technical communication", "technical writing",
        "technical documentation", "user experience writing",
        "ux writing", "information design", "usability",
        "technical communicator",
    ],

    # Scope: Writing and communication in professional and workplace
    # contexts. Includes business writing, professional discourse,
    # and workplace communication.
    "professional writing": [
        "professional writing", "professional communication",
        "business writing", "workplace writing",
        "workplace communication", "professional discourse",
        "business communication", "grant writing", "proposal writing",
        "business proposal", "technical report", "business report",
        "memo writing", "documentation writing", "organizational communication",
        "corporate writing", "professional genres",
    ],

    # Scope: Writing in scientific and STEM disciplines. Includes
    # the IMRaD structure, science communication, and lab report
    # pedagogy. As distinct from "technical communication" (applied
    # professional context) and "writing across the curriculum"
    # (writing embedded in all disciplines).
    "scientific writing": [
        "scientific writing", "science communication",
        "stem writing", "imrad", "lab report",
        "scientific communication", "science writing",
        "writing in stem",
    ],

    # Scope: Employment conditions, labor practices, and institutional
    # politics affecting writing teachers. Includes contingent faculty
    # issues, adjunctification, academic capitalism, and the academic
    # job market in composition/rhetoric.
    "labor and working conditions": [
        "contingent faculty", "adjunct faculty",
        "adjunct instructor", "non-tenure track",
        "casualization", "academic labor",
        "adjunctification", "part-time faculty",
        "faculty working conditions", "academic capitalism",
        "labor in higher education", "academic job market",
        "contingent labor",
    ],


    # ══════════════════════════════════════════════════════════════════════
    # Family 7: Research Methods
    # ══════════════════════════════════════════════════════════════════════

    # Scope: Archival and historical methods in rhetoric and composition.
    # Includes recovery projects, rhetorical history, and archival
    # methodology.
    "archival research": [
        "archival research", "archival rhetoric", "archival work",
        "historical rhetoric", "rhetorical history", "recovery",
        "recovering voices", "archival methods",
        "historiography", "historical recovery",
    ],

    # Scope: Research using systematic evidence collection. Includes
    # mixed methods, survey-based studies, and longitudinal studies
    # of writing.
    "empirical research": [
        "empirical research", "empirical study", "mixed methods",
        "survey research", "longitudinal study", "experimental design",
        "randomized trial", "randomized control trial", "rct",
        "within-subject design", "between-subject design", "comparative study",
        "empirically grounded", "data-driven", "evidence-based",
    ],

    # Scope: Research using observation, interview, and interpretation.
    # Includes ethnography, think-aloud protocols, and case studies.
    "qualitative research": [
        "qualitative research", "qualitative study",
        "ethnography", "think-aloud protocol", "grounded theory",
        "thematic analysis", "interview study",
        "qualitative methods", "qualitative case study",
        "participant observation",
    ],

    # Scope: Research using statistical analysis and corpus methods.
    "quantitative research": [
        "quantitative research", "quantitative study",
        "corpus analysis", "corpus study", "corpus linguistics",
        "computational analysis", "quantitative methods",
        "statistical analysis of writing",
    ],


    # ══════════════════════════════════════════════════════════════════════
    # Family 8: Digital & Multimodal
    # ══════════════════════════════════════════════════════════════════════

    # Scope: Rhetoric in digital environments and networked writing.
    # Includes digital humanities, computational rhetoric, and the
    # rhetorical dimensions of digital technologies.
    # NOTE: "digital media" excluded — too broad across fields.
    "digital rhetoric": [
        "digital rhetoric", "digital writing", "digital literacy",
        "digital composing", "networked writing",
        "computational rhetoric", "digital humanities",
        "internet rhetoric", "hypertext rhetoric",
    ],

    # Scope: Communication using multiple modes (visual, audio, spatial,
    # gestural). Grounded in social semiotics and New London Group
    # framework.
    # NOTE: "image", "design", "mode" excluded — extremely common false
    # positives firing on "research design", "image processing", etc.
    "multimodality": [
        "multimodal", "multimodality", "visual rhetoric",
        "visual communication", "affordance", "semiotic",
        "new media composing", "multimodal composition",
        "multimodal literacy", "multimodal pedagogy",
        "modes of communication",
    ],

    # Scope: Writing and rhetoric in social media environments. Includes
    # platform-specific studies and participatory culture.
    "social media": [
        "social media", "twitter", "facebook", "instagram",
        "tiktok", "youtube", "platform rhetoric", "viral content",
        "online discourse", "social networking",
    ],

    # Scope: Teaching writing in fully online or hybrid course formats.
    # Includes asynchronous writing instruction and LMS-based pedagogy.
    "online writing instruction": [
        "online writing instruction", "online writing course",
        "online composition", "distance writing", "hybrid writing",
        "online writing pedagogy", "learning management system",
        "lms", "canvas lms", "blackboard",
    ],

    # Scope: AI tools and their applications in writing and writing
    # pedagogy. Includes generative AI, LLMs, ChatGPT, and automated
    # feedback systems.
    "artificial intelligence": [
        "artificial intelligence", "ai writing", "generative ai",
        "chatgpt", "large language model", "llm",
        "machine learning", "automated writing",
        "automated feedback", "gpt-",
    ],


    # ══════════════════════════════════════════════════════════════════════
    # Family 9: Language & Linguistics
    # ══════════════════════════════════════════════════════════════════════

    # Scope: Writing research and pedagogy focused on writers composing
    # in a language other than their first. Includes L2 writing, ESL/EFL
    # composition, and heritage language writing. As distinct from
    # "translingualism" (a theoretical framework) and "second language
    # acquisition" (language acquisition theory).
    "multilingual writers": [
        "multilingual writer", "multilingual student",
        "multilingual composition", "multilingual writing",
        "second language writing", "l2 writing", "l2 writer",
        "esl writing", "efl writing",
        "english language learner", "generation 1.5",
        "esol", "english as a second language",
        "heritage language writer", "nonnative english",
        "non-native english", "second-language writer",
        "language development", "language learning", "language acquisition",
    ],

    # Scope: Translingual orientation toward language difference in
    # writing. A theoretical framework embracing language variation as
    # resource. As distinct from "multilingual writers" (a population)
    # and "second language acquisition" (acquisition theory).
    "translingualism": [
        "translingualism", "translingual", "code-meshing",
        "world englishes", "linguistic diversity",
        "translanguaging", "language difference",
        "language diversity",
    ],

    # Scope: Theory and research on how people acquire additional
    # languages. Includes SLA theory, input hypothesis, interlanguage,
    # and the implications of acquisition theory for writing pedagogy.
    # As distinct from "multilingual writers" (composition pedagogy
    # focus) and "translingualism" (framework for language difference).
    "second language acquisition": [
        "second language acquisition", "sla research",
        "language acquisition theory", "input hypothesis",
        "krashen", "interlanguage", "fossilization",
        "language acquisition research",
    ],

    # Scope: Grammar, sentence-level correctness, and mechanical
    # conventions in writing. Includes error analysis, error correction,
    # grammatical instruction, and debates over teaching grammar.
    "grammar and mechanics": [
        "grammar instruction", "error correction", "error analysis",
        "sentence-level error", "grammatical error",
        "grammatical instruction", "teaching grammar",
        "mechanical errors", "correctness in writing",
        "error patterns", "syntactic complexity",
    ],

    # Scope: New Literacy Studies, multiple literacies, and literacy as
    # social practice. As distinct from general reading/writing instruction.
    "literacy studies": [
        "literacy studies", "new literacy studies",
        "multiliteracies", "critical literacy",
        "vernacular literacy", "literacy sponsorship",
        "sponsorship of literacy", "literacy practices",
        "literacy as social practice",
    ],


    # ══════════════════════════════════════════════════════════════════════
    # Family 10: Identity, Equity & Justice
    # ══════════════════════════════════════════════════════════════════════

    # Scope: Race, racism, and anti-racist practice in writing, rhetoric,
    # and composition. Grounded in writing and literacy contexts.
    # NOTE: bare "race," "equity," "indigenous," "decolonial" excluded —
    # appear in management, policy, and education broadly. Compound
    # phrases with writing/rhetoric/composition required.
    # As distinct from "african american rhetorics" (specific tradition)
    # and "decolonial rhetorics" (postcolonial/decolonial theory).
    "race and writing": [
        "race and writing", "racism and writing",
        "antiracist writing", "anti-racist writing",
        "antiracist pedagogy", "anti-racist pedagogy",
        "racial justice in writing", "whiteness and writing",
        "bipoc students", "latinx students", "chicanx",
        "racial literacy", "raciolinguistics",
        "critical race theory and writing",
        "equity in writing", "white supremacy and writing",
    ],

    # Scope: Gender and sexuality in writing and rhetoric. Includes
    # queer rhetoric, transgender identities in writing, and gender
    # in composition pedagogy. As distinct from "feminist rhetorics"
    # (the rhetorical tradition specifically).
    "gender and writing": [
        "feminist composition", "feminism and writing",
        "women writers", "women and writing",
        "lgbtq", "queer rhetoric", "transgender",
        "nonbinary", "gender and writing",
        "gender in writing",
    ],

    # Scope: Disability, accessibility, and neurodiversity in writing
    # and rhetoric. Includes UDL, crip theory, and disability rhetoric.
    "disability studies": [
        "disability", "accessibility in writing",
        "universal design for learning", "udl",
        "neurodiversity", "dyslexia", "disability rhetoric",
        "ableism", "crip theory", "disability studies",
        "accessible writing",
    ],

    # Scope: Rhetoric in public life. Includes civic discourse, political
    # rhetoric, public sphere theory, and counterpublics.
    "public rhetoric": [
        "public rhetoric", "public discourse", "civic rhetoric",
        "political rhetoric", "public sphere", "counterpublic",
        "rhetorical public", "public argument",
        "deliberative democracy",
    ],

    # Scope: Literacy practices in community contexts outside academic
    # settings. Grounded in community literacy theory.
    "community literacy": [
        "community literacy", "community-based writing",
        "community literacy project", "neighborhood literacy",
        "community-based literacy", "community literacies",
        "situated literacy", "local literacy",
        "grassroots literacy", "literacy practices",
    ],


    # ══════════════════════════════════════════════════════════════════════
    # Family 11: Affect, Embodiment & Place
    # ══════════════════════════════════════════════════════════════════════

    # Scope: Emotional and motivational dimensions of writing. Includes
    # writing anxiety, affect theory applied to writing, and motivation.
    "affect and writing": [
        "affect", "writing anxiety", "writer's block",
        "motivation and writing", "writing motivation",
        "emotion and writing", "emotional writing",
        "writing confidence",
    ],

    # Scope: Rhetoric and writing as embodied, material practices.
    "body and rhetoric": [
        "embodied rhetoric", "embodiment", "somatic rhetoric",
        "body rhetoric", "material rhetoric", "corporeal rhetoric",
        "sensory rhetoric", "sensory composition", "gestural rhetoric",
        "kinetic rhetoric", "haptic rhetoric", "visceral rhetoric",
        "embodied experience", "physical space and rhetoric",
    ],

    # Scope: Place, space, and geography in rhetoric and writing.
    # Includes place-based pedagogies and spatial rhetoric.
    "place and space": [
        "sense of place", "spatial rhetoric", "place-based writing",
        "geography of rhetoric", "rhetorical geography",
        "spatial turn", "place and writing", "place-based pedagogy",
    ],

}


# ── Matcher ────────────────────────────────────────────────────────────────────

def _compile_vocab(vocab: dict) -> dict:
    """
    Pre-compile each trigger phrase into a regex pattern.

    Single-word triggers → word-boundary pattern (\b word \b) so that
    e.g. "grammar" doesn't fire on "programmatic" and "black" doesn't
    fire on "blackboard".

    Multi-word triggers → literal phrase search (no boundary issues since
    surrounding spaces already anchor the match).
    """
    compiled = {}
    for tag, phrases in vocab.items():
        patterns = []
        for phrase in phrases:
            if " " in phrase or "-" in phrase:
                # Phrase: match as a literal substring (case-insensitive)
                patterns.append(re.compile(re.escape(phrase), re.IGNORECASE))
            else:
                # Single token: word-boundary–aware
                patterns.append(re.compile(r'\b' + re.escape(phrase) + r'\b', re.IGNORECASE))
        compiled[tag] = patterns
    return compiled


_COMPILED_VOCAB = _compile_vocab(VOCAB)


def auto_tag(title: str | None, abstract: str | None) -> str | None:
    """
    Match title and abstract against the controlled vocabulary.

    Returns a pipe-delimited string like "|transfer|genre theory|revision|"
    with leading and trailing pipes (for clean substring matching in SQL),
    or None if no terms matched.
    """
    text = ((title or "") + " " + (abstract or ""))
    if not text.strip():
        return None

    matched = []
    for tag, patterns in _COMPILED_VOCAB.items():
        for pat in patterns:
            if pat.search(text):
                matched.append(tag)
                break  # only add each tag once

    if not matched:
        return None

    return "|" + "|".join(matched) + "|"
