#!/usr/bin/env python3
"""Give every Reading and Writing question its task and its passage type in content/db.

    python3 scripts/classify_questions.py                      report what would change
    python3 scripts/classify_questions.py --apply              write content/db/
    python3 scripts/classify_questions.py --review-out FILE    list the passages still without a type
    python3 scripts/classify_questions.py --review-in FILE --apply   record types a reader decided

Two things are recorded, each with where it came from:

task (question.task_id, task_basis)
    What the question asks for within its skill: "main purpose" against
    "overall structure", a contrast transition against a causal one, a
    semicolon against a colon. It is read off the question itself:
      - the wording of the stem, which the test's editors keep to a few forms;
      - for transitions, the logical relation of the correct answer;
      - for punctuation and grammar, what the answer choices vary and which
        mark or form the correct one uses.
    Nothing here judges difficulty or meaning, so the rule gives the same
    answer every time. A stem or a set of choices can show that a question
    belongs to another skill than the one recorded for it (a pronoun question
    filed under punctuation): the task then follows the evidence, and the
    report lists those questions.

passage type (question.passage_type, passage_type_basis)
    What kind of text the question is about.
      source_intro  the passage says so itself: "The following text is from
                    Jane Austen's 1811 novel ..." gives fiction, poetry, drama
                    or nonfiction (memoir, essay, a nonfiction book);
      word_list     an informational passage whose vocabulary clearly belongs
                    to science, to history and social science, or to arts and
                    humanities (the lists below, with a required margin);
      reviewed      a reader decided, because the lists were not clear enough.
    A passage the lists are unsure about gets no type until it is reviewed,
    and the tests fail while a Reading and Writing question has none.

Reviewed rows are never changed by this script. Everything else is recomputed,
so running it again changes nothing. Math questions have neither value.
"""

from __future__ import annotations

import argparse
import collections
import html
import json
from pathlib import Path
import re
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent))
import content_db as db  # noqa: E402


SECTION = "reading_writing"

# ---------------------------------------------------------------- reference rows

PASSAGE_TYPES = [
    {"passage_type": "science", "family": "information", "name_zh": "自然科学", "position": 1},
    {"passage_type": "social", "family": "information", "name_zh": "历史与社会科学", "position": 2},
    {"passage_type": "humanities", "family": "information", "name_zh": "人文艺术", "position": 3},
    {"passage_type": "fiction", "family": "literature", "name_zh": "小说", "position": 4},
    {"passage_type": "poetry", "family": "literature", "name_zh": "诗歌", "position": 5},
    {"passage_type": "drama", "family": "literature", "name_zh": "戏剧", "position": 6},
    {"passage_type": "nonfiction", "family": "literature", "name_zh": "散文与回忆录", "position": 7},
]

# (task_id, skill name, Chinese name, English name), in the order they are shown.
TASKS = [
    ("wic_blank", "Words in Context", "选词填空", "Most logical and precise word"),
    ("wic_meaning", "Words in Context", "词义理解", "Meaning of a word in context"),
    ("tsp_purpose", "Text Structure and Purpose", "主要目的", "Main purpose"),
    ("tsp_structure", "Text Structure and Purpose", "整体结构", "Overall structure"),
    ("tsp_function", "Text Structure and Purpose", "句子功能", "Function of a sentence or portion"),
    ("ctc_response", "Cross-Text Connections", "观点回应", "How one text would respond to the other"),
    ("ctc_compare", "Cross-Text Connections", "异同比较", "Agreement or difference between the texts"),
    ("cid_main", "Central Ideas and Details", "主旨概括", "Main idea"),
    ("cid_detail", "Central Ideas and Details", "细节理解", "Detail"),
    ("coe_support", "Command of Evidence: Textual", "支持论点", "Evidence that supports a claim"),
    ("coe_weaken", "Command of Evidence: Textual", "削弱论点", "Evidence that weakens a claim"),
    ("coe_illustrate", "Command of Evidence: Textual", "引文例证", "Quotation that illustrates a claim"),
    ("coq_table", "Command of Evidence: Quantitative", "表格数据", "Data from a table"),
    ("coq_graph", "Command of Evidence: Quantitative", "图表数据", "Data from a graph"),
    ("inf_complete", "Inferences", "推断填空", "Most logical completion"),
    ("inf_suggest", "Inferences", "推断理解", "What the text suggests"),
    ("rs_compare", "Rhetorical Synthesis", "对比异同", "Compare or contrast"),
    ("rs_introduce", "Rhetorical Synthesis", "介绍概述", "Introduce or describe"),
    ("rs_emphasize", "Rhetorical Synthesis", "强调信息", "Emphasize a piece of information"),
    ("rs_explain", "Rhetorical Synthesis", "解释说明", "Explain"),
    ("rs_claim", "Rhetorical Synthesis", "提出观点", "Make a claim or generalization"),
    ("tr_contrast", "Transitions", "转折对比", "Contrast"),
    ("tr_cause", "Transitions", "因果总结", "Cause, result or conclusion"),
    ("tr_addition", "Transitions", "补充递进", "Addition or similarity"),
    ("tr_example", "Transitions", "举例具体", "Example or specification"),
    ("tr_emphasis", "Transitions", "强调重述", "Emphasis or restatement"),
    ("tr_sequence", "Transitions", "时间顺序", "Time or sequence"),
    ("tr_other", "Transitions", "其他短语", "Other words and longer phrases"),
    ("bd_period", "Boundaries", "句号断句", "Sentence boundary"),
    ("bd_semicolon", "Boundaries", "分号", "Semicolon"),
    ("bd_colon", "Boundaries", "冒号", "Colon"),
    ("bd_dash", "Boundaries", "破折号与括号", "Dash or parentheses"),
    ("bd_comma", "Boundaries", "逗号", "Comma"),
    ("bd_none", "Boundaries", "无需标点", "No punctuation"),
    ("fs_agreement", "Form, Structure, and Sense", "主谓一致", "Subject-verb agreement"),
    ("fs_tense", "Form, Structure, and Sense", "动词时态", "Verb tense"),
    ("fs_verbform", "Form, Structure, and Sense", "谓语与非谓语", "Finite and nonfinite verb forms"),
    ("fs_pronoun", "Form, Structure, and Sense", "代词", "Pronouns"),
    ("fs_possessive", "Form, Structure, and Sense", "所有格与复数", "Possessives and plurals"),
    ("fs_modifier", "Form, Structure, and Sense", "修饰语与句子结构", "Modifiers and sentence structure"),
]
TASK_SKILL = {task: skill for task, skill, _, _ in TASKS}

# ---------------------------------------------------------------- text helpers

TAG = re.compile(r"<[^>]*>")
MARKER = re.compile(r"\[/?(?:vocab|i|u|b)\]")


def plain(text: str | None) -> str:
    text = re.sub(r"</(?:p|div|tr|caption|li|h\d)>|<br\s*/?>", "\n", text or "", flags=re.IGNORECASE)
    text = MARKER.sub("", html.unescape(TAG.sub("", text)))
    return re.sub(r"[ \t ]+", " ", re.sub(r"\s*\n\s*", " ", text)).strip()


def words(text: str) -> list[str]:
    return re.findall(r"[a-z']+", text.lower().replace("’", "'"))


# ---------------------------------------------------------------- task: stems

# The stem forms that settle a skill by themselves, first match wins.
STEM_SKILLS = [
    ("Transitions", r"most logical transi?tion"),
    ("Words in Context", r"logical and precise word|most nearly mean"),
    ("Inferences", r"most logically completes? the text"),
    ("Rhetorical Synthesis", r"student wants to|from the given sentences|information from the notes"),
    ("Command of Evidence: Quantitative", r"data from the (?:table|graph|chart|figure|map)"),
    ("Cross-Text Connections", r"\btext 1\b|\btext 2\b|based on the texts|both texts|both authors"),
    ("Text Structure and Purpose", r"main purpose|overall structure|function of the underlined"),
    ("Central Ideas and Details", r"main idea|main topic"),
    ("Command of Evidence: Textual", r"quotation .{0,80}illustrat|illustrate the claim"),
]
STEM_TASKS = {
    "Words in Context": [("wic_meaning", r"most nearly mean"), ("wic_blank", r".")],
    "Text Structure and Purpose": [("tsp_function", r"function of|underlined"), ("tsp_structure", r"structure"), ("tsp_purpose", r".")],
    "Cross-Text Connections": [("ctc_compare", r"both|agree|difference|differ|similar|same |in common|share"), ("ctc_response", r".")],
    "Central Ideas and Details": [
        ("cid_main", r"main idea|main topic|main purpose|central idea|best describes what happens|best summarizes|summary of|primarily about|main point"),
        ("cid_detail", r".")],
    "Command of Evidence: Textual": [
        ("coe_illustrate", r"quotation|illustrat"),
        ("coe_weaken", r"weaken|undermine|challenge|cast doubt|call into question|contradict"),
        ("coe_support", r".")],
    "Command of Evidence: Quantitative": [("coq_graph", r"graph|chart|figure|map|diagram|plot"), ("coq_table", r".")],
    "Inferences": [("inf_complete", r"logically completes?"), ("inf_suggest", r".")],
}


def synthesis_task(stem: str) -> str:
    goal = re.search(r"(?:student wants to|writer wants to|given sentences to|notes to) (.+?)(?:\. which|\?|$)", stem)
    goal_text = goal.group(1) if goal else stem
    if re.search(r"differen|similar|compar|contrast|distinguish|in common", goal_text):
        return "rs_compare"
    if re.search(r"\bmake (?:and support )?an? |\b(?:argue|support|challenge|defend|persuade|convince|advocate|recommend|counter|refute)\b", goal_text):
        return "rs_claim"
    if re.search(r"\bexplain|account for", goal_text):
        return "rs_explain"
    if re.search(r"emphasi[sz]e|highlight|stress|call attention|underscore", goal_text):
        return "rs_emphasize"
    return "rs_introduce"


# ---------------------------------------------------------------- task: transitions

TRANSITIONS = {
    "tr_contrast": """however; nevertheless; nonetheless; still; yet; by contrast; in contrast; on the other hand; conversely;
        instead; rather; alternatively; that said; regardless; despite this; on the contrary; though; even so; by comparison;
        in comparison; then again; at the same time; but; alternately; otherwise; be that as it may; all the same; despite that;
        in spite of this; whereas; unlike; granted; admittedly; of course; to be sure; notwithstanding; even still; actually;
        on the other end of the spectrum; despite these; contrary to""",
    "tr_cause": """therefore; thus; hence; consequently; as a result; accordingly; for this reason; because of this; so; to that end;
        to these ends; to this end; as such; in conclusion; in sum; in summary; overall; to conclude; in short; ultimately; in the end;
        all in all; in turn; as a consequence; for that reason; that's why; this is why; thereby; in brief; to summarize; on the whole;
        after all; due to this; because of that; with this in mind; in effect; because; since; given that; in response; in this way;
        in so doing; in doing so; in light of this; given these""",
    "tr_addition": """moreover; furthermore; in addition; additionally; also; besides; what's more; further; similarly; likewise;
        in the same way; by the same token; in a similar vein; too; as well; along the same lines; in like manner; equally; and; plus;
        on top of that; more importantly; even more; what is more; in a similar way; in similar fashion; relatedly""",
    "tr_example": """for example; for instance; specifically; in particular; namely; particularly; to illustrate; as an illustration;
        in this case; more specifically; in one case; by way of example; such as; to cite one example; to take one""",
    "tr_emphasis": """in fact; indeed; in other words; that is; to put it another way; simply put; put differently; in reality; in truth;
        as a matter of fact; to be exact; more precisely; to be precise; in essence; certainly; undoubtedly; clearly; evidently;
        put another way; that is to say; in actuality; more simply; to clarify; fundamentally; essentially; truly""",
    "tr_sequence": """then; next; finally; first; second; third; afterward; afterwards; later; subsequently; previously; meanwhile;
        eventually; soon; currently; today; until then; earlier; lastly; in the meantime; at first; initially; originally; now;
        nowadays; since then; before; after; thereafter; by then; at that time; presently; to begin with; first of all; to start;
        from then on; in the future; at this point; once; recently; in time; at last; increasingly; simultaneously; immediately;
        ever since; last; secondly; at present; in recent years; more recently; over time; in the past; prior to this; at the time;
        concurrently; to date""",
}
TRANSITION_TASK: dict[str, str] = {}
for _task in ("tr_sequence", "tr_emphasis", "tr_example", "tr_addition", "tr_cause", "tr_contrast"):
    for _phrase in TRANSITIONS[_task].split(";"):
        TRANSITION_TASK[" ".join(_phrase.split())] = _task
TRANSITION_PHRASES = sorted(TRANSITION_TASK, key=len, reverse=True)


def transition_task(answer: str) -> str:
    key = " ".join(words(plain(answer)))
    if key in TRANSITION_TASK:
        return TRANSITION_TASK[key]
    for phrase in TRANSITION_PHRASES:
        if " " in phrase and (key.startswith(phrase + " ") or key.endswith(" " + phrase)):
            return TRANSITION_TASK[phrase]
    return "tr_other"


# ---------------------------------------------------------------- task: conventions

PRONOUNS = set("it its it's they them their theirs they're there he him his she her hers we us our ours you your yours i me my "
               "this that these those one ones who whom whose which what itself themselves himself herself each both either "
               "neither everyone anyone someone none all some any".split())
LINKING = set("and but so or yet nor for which that who because although while when".split())
NUMBER_PAIRS = [("is", "are"), ("was", "were"), ("has", "have"), ("does", "do")]
MARKS = ".;:,—–?!()"


def marks_of(text: str) -> str:
    return re.sub("[^" + re.escape(MARKS) + "]", "", text.replace("--", "—"))


def conventions_kind(choices: list[str]) -> str:
    """Whether the choices vary punctuation, word forms, or both."""
    values = [plain(choice).replace("’", "'") for choice in choices]
    letters = {re.sub(r"[^a-z0-9']", "", value.lower()) for value in values}
    if len(letters) == 1:
        return "punctuation"
    if len({marks_of(value) for value in values}) == 1:
        return "form"
    # Whole clauses that are worded differently are a structure question even when commas differ.
    groups = [words(value) for value in values]
    if all(len(group) >= 6 for group in groups) and len({tuple(sorted(group)) for group in groups}) == len(groups):
        return "form"
    # Clause links such as ", and" against "; " are still punctuation.
    cores = {tuple(word for word in group if word not in LINKING) for group in groups}
    return "punctuation" if len(cores) == 1 else "both"


def boundary_task(choices: list[str], answer: str) -> str:
    """The mark that sets the correct answer apart from the other choices."""
    shared = None
    for choice in choices:
        current = collections.Counter(marks_of(plain(choice)))
        shared = current if shared is None else shared & current
    mine = collections.Counter(marks_of(plain(answer)))
    own = mine - (shared or collections.Counter())
    pool = own if sum(own.values()) else mine
    for mark, task in ((".", "bd_period"), ("?", "bd_period"), ("!", "bd_period"), (";", "bd_semicolon"), (":", "bd_colon"),
                       ("—", "bd_dash"), ("–", "bd_dash"), ("(", "bd_dash"), (",", "bd_comma")):
        if pool[mark]:
            return task
    return "bd_none"


def nonfinite(form: list[str]) -> bool:
    return bool(form) and (form[0] in ("to", "having", "being") or (len(form) == 1 and form[0].endswith("ing")))


def number_twin(left: list[str], right: list[str]) -> bool:
    """Two verb forms that differ only in singular against plural."""
    if len(left) != len(right) or not left or left[1:] != right[1:]:
        return False
    first, other = left[0], right[0]
    if (first, other) in NUMBER_PAIRS or (other, first) in NUMBER_PAIRS:
        return True
    shorter, longer = sorted((first, other), key=len)
    return longer in (shorter + "s", shorter + "es") or (shorter.endswith("y") and longer == shorter[:-1] + "ies")


def form_task(choices: list[str], answer: str) -> str:
    values = [plain(choice).lower().replace("’", "'") for choice in choices]
    groups = [words(value) for value in values]
    correct = words(plain(answer))
    bare = [value.replace("'", "") for value in values]
    contractions = any(re.search(r"n't\b", value) for value in values)
    if any("'" in value for value in values) and not contractions and len(set(bare)) < len(bare):
        return "fs_possessive"
    flat = [word for group in groups for word in group]
    if flat and all(len(group) <= 3 for group in groups) and sum(word in PRONOUNS for word in flat) >= max(2, len(flat) * 0.6):
        return "fs_pronoun"
    if any("'" in value for value in values) and not contractions and all(len(group) <= 4 for group in groups):
        return "fs_possessive"
    if all(len(group) >= 5 for group in groups):
        return "fs_modifier"
    others = [group for group in groups if group != correct]
    if nonfinite(correct) or (others and all(nonfinite(group) for group in others)):
        return "fs_verbform"
    if any(number_twin(correct, group) for group in others):
        return "fs_agreement"
    return "fs_tense"


CONVENTIONS = ("Boundaries", "Form, Structure, and Sense")


def task_of(skill: str, prompt: str, choices: list[str], answer: str) -> str:
    """The task of a question whose recorded skill is `skill`."""
    stem = plain(prompt).lower()
    if skill in CONVENTIONS or re.search(r"conventions? of standard english", stem):
        kind = conventions_kind(choices)
        if kind == "both":
            kind = "punctuation" if skill == "Boundaries" else "form"
        return boundary_task(choices, answer) if kind == "punctuation" else form_task(choices, answer)
    # A stem form that belongs to one skill wins over the recorded skill.
    evident = next((name for name, pattern in STEM_SKILLS if re.search(pattern, stem)), None)
    skill = evident or skill
    if skill == "Transitions":
        return transition_task(answer)
    if skill == "Rhetorical Synthesis":
        return synthesis_task(stem)
    for task, pattern in STEM_TASKS[skill]:
        if re.search(pattern, stem):
            return task
    raise AssertionError(skill)


# ---------------------------------------------------------------- passage type

LITERARY = {
    "poem": "poetry", "poems": "poetry", "poetry": "poetry", "sonnet": "poetry", "ode": "poetry",
    "novel": "fiction", "novella": "fiction", "short story": "fiction", "story": "fiction",
    "short-story": "fiction", "tale": "fiction", "fable": "fiction",
    "play": "drama",
}
KIND = r"(poems?|poetry|sonnet|ode|novel(?:la)?|short[ -]story|story|tale|fable|play)"
# "The following text is [adapted] from <author>'s <year> <kind> ..." and the paired-text form.
INTRODUCTION = re.compile(
    r"^(?:Text 1\s*)?(?:The following text is|This text is|is)\b(?:(?!\bText 2\b)[^\n]){0,160}?\b" + KIND + r"\b", re.IGNORECASE)
# "<Title> is a[n] [year] <kind> by <author>."
TITLE = re.compile(r"^[^\n]{1,90}?\bis an? (?:\d{4} |[a-z-]+ ){0,3}" + KIND + r"\b[^\n.]{0,40}\bby\b", re.IGNORECASE)
# An excerpt of something that is not fiction, poetry or drama: a memoir, an essay, a nonfiction book.
NOT_FICTION = re.compile(r"\b(essay|article|book|memoirs?|autobiography|speech|letter|study|review|lecture|address|history|biography|nonfiction|work)\b",
                         re.IGNORECASE)
EXCERPT = re.compile(r"^(?:Text 1\s*)?(?:The following text is|This text is)\b[^\n.]{0,60}?\bfrom\b", re.IGNORECASE)


def literary_type(passage: str | None) -> str | None:
    """The type a passage states for itself, or None for an informational text."""
    # An editor's note in front of the passage is not part of its wording.
    text = re.sub(r"^校订说明：.*?。\s*(?=The following|This text|Text 1)", "", plain(passage))[:400]
    for pattern in (INTRODUCTION, TITLE):
        match = pattern.match(text)
        if match and not NOT_FICTION.search(match.group(0)):
            return LITERARY[match.group(1).lower()]
    if EXCERPT.match(text):
        return "nonfiction"
    return None


# Vocabulary of the three informational types. A word counts once per occurrence;
# the names of professions count three times, because a passage that introduces
# "the ecologist" or "the economist" has said what it is about. Entries of six
# letters or more also match as the beginning of a longer word.
WORD_LISTS = {
    "science": """biolog chemist chemical chemistry physic astronom astrophys planet planetary galax telescope nasa geolog fossil
        dinosaur species bacteri microb virus viral gene genes genetic genom dna rna protein enzyme molecul atom atomic particle
        quantum neuron neural cell cells organism ecosystem ecolog climate carbon ocean marine coral whale whales bird birds
        insect insects bee bees ant ants plant plants tree trees forest forests soil fungi fungus mammal mammals reptile predator
        prey evolution evolutionary mutation medicine medical disease patient patients drug vaccine clinical neuroscien engineer
        engineers engineering robot robots polymer battery batteries solar magnetic laser sensor sensors mathematic theorem
        earthquake volcano volcanic glacier glaciers atmosphere atmospheric temperature temperatures laboratory hypothesis
        hypothesized compound compounds mineral minerals crystal isotope oxygen hydrogen nitrogen photosynthesis
        pollinat habitat habitats wildlife animal animals fish fishes shark sharks frog frogs lizard beetle beetles butterfl moth
        moths spider spiders worm worms mouse mice rat rats primate monkey chimpanzee elephant elephants wolf wolves bear bears
        dolphin octopus squid crab tissue hormone immune antibiot infection cancer tumor metabol nutrient diet dietary
        physiolog anatom skeleton bone bones tooth teeth skull nerve brain brains cognition sleep
        asteroid comet comets meteor moon moons mars jupiter saturn venus exoplanet orbit orbits orbital stellar supernova
        nebula universe cosmic gravity gravitational spacecraft satellite satellites rover sediment sediments erosion tectonic
        seismic lava magma groundwater river rivers lake lakes wetland drought rainfall precipitation hurricane storm storms
        greenhouse emission emissions pollution pollutant microplastic renewable turbine electricity electron electrons photon
        wavelength thermal conductiv superconduct semiconductor nanoparticle nanotube graphene alloy fiber fibers
        algorithm algorithms computer computers computing software simulation specimen specimens
        biodiversity conservation extinct extinction paleontolog entomolog ornitholog botan zoolog herbivor carnivor
        larva larvae pollen seed seeds root roots leaf leaves flower flowers crop crops agricultur yeast algae plankton reef
        embryo fertiliz reproduc offspring nest nests nesting hibernat camouflage venom toxin toxic catalys
        reaction reactions acid velocity acceleration momentum friction pressure density voltage circuit
        pathogen epidemi biomedical bioengineer biochem geochem geophys hydrolog meteorolog oceanograph climatolog""",
    "social": """econom market markets trade trading consumer consumers price prices pricing labor worker workers wage wages
        voter voters election elections electoral political politics politician government governments policy policies law
        laws legal court courts congress senate president presidential constitution constitutional democracy democratic
        citizen citizens citizenship rights activist activists activism suffrage abolition abolitionist slavery
        enslaved war wars revolution revolutionary empire imperial colonial colony colonies colonization treaty treaties
        history historical historian historians archaeolog anthropolog sociolog psycholog participants survey surveys surveyed
        respondents social society societies community communities urban city cities town towns village
        education educational school schools teacher teachers linguist linguists linguistic language languages dialect
        indigenous nation nations national tribe tribal immigrant immigrants population populations demographic
        business businesses company companies firm firms manager managers employee employees employer industry industries
        union unions strike strikes parliament monarch king queen emperor dynasty ancient medieval
        civilization civilizations kingdom republic federal governor mayor legislat reform reforms reformer
        campaign campaigns protest protests boycott segregation civil equality inequality poverty wealth income incomes tax
        taxes bank banks banking currency inflation investment investors finance financial entrepreneur merchant merchants
        diplomat diplomacy military army navy soldier soldiers battle border borders territory territories settlement
        settlers settler inhabitants residents household households kinship gender
        customs ritual rituals religion religious church temple newspaper newspapers
        journalism journalist journalists advertising advertisements propaganda institutions organization
        organizations nonprofit volunteer volunteers negotiat incentive incentives productivity employment unemployment
        recession tariff tariffs excavation excavations artifact artifacts ruins burial tomb tombs pyramid inscription
        inscriptions census explorer explorers expedition voyage""",
    "humanities": """artist artists art arts artwork artworks painter painters painting paintings sculpt sculpture sculptures
        sculptor mural murals museum museums gallery galleries exhibit exhibition exhibitions photograph photographs
        photographer photography architect architects architecture architectural designer designers music musical
        musician musicians composer composers song songs album albums jazz blues opera orchestra symphony dance dancer
        dancers choreograph theater theatre film films filmmaker director directors cinema actor actors actress novel novels
        novelist novelists poet poets poem poems poetry literary literature author authors writer writers memoir essay
        essays essayist playwright playwrights fiction book books translator translation translations
        critic critics criticism philosophy philosopher philosophers aesthetic aesthetics genre genres
        textile textiles weaving weaver quilt quilts ceramics ceramic pottery potter fashion folklore myth myths mythology
        legend legends storytelling narrative narratives protagonist verse stanza
        rhyme lyric lyrics sonnet epic comedy tragedy stage performer performers
        singer singers guitarist pianist violin piano guitar melody rhythm harmony choir concert festival festivals
        illustrator illustration illustrations cartoon cartoonist comic comics animation animator printmaking
        engraving calligraphy collage portrait portraits canvas studio curator curators renaissance
        baroque modernist modernism surrealist surrealism impressionist harlem avant-garde
        editor editors anthology magazine magazines screenplay documentary television broadcast radio
        podcast typography handwriting carved carving jewelry beadwork basket baskets
        embroidery tapestry tapestries costume costumes cuisine chef recipe recipes cookbook culinary""",
}
PROFESSIONS = {
    "science": """biologist biologists chemist chemists physicist physicists astronomer astronomers astrophysicist geologist
        geologists ecologist ecologists neuroscientist neuroscientists engineer engineers botanist botanists zoologist
        paleontologist paleontologists entomologist ornithologist oceanographer climatologist geneticist geneticists
        microbiologist epidemiologist mathematician mathematicians physician physicians volcanologist seismologist
        meteorologist roboticist""",
    "social": """economist economists psychologist psychologists sociologist sociologists historian historians archaeologist
        archaeologists anthropologist anthropologists linguist linguists geographer geographers lawyer lawyers politician
        politicians activist activists journalist journalists educator educators diplomat""",
    "humanities": """artist artists painter painters poet poets novelist novelists composer composers musician musicians
        filmmaker filmmakers architect architects sculptor sculptors playwright playwrights choreographer choreographers
        photographer photographers singer singers dancer dancers illustrator designer designers critic critics
        philosopher philosophers ceramicist weaver essayist translator""",
}
MARGIN = 3
WEIGHT: dict[str, tuple[str, int]] = {}
for _kind, _text in WORD_LISTS.items():
    for _word in _text.split():
        WEIGHT.setdefault(_word, (_kind, 1))
for _kind, _text in PROFESSIONS.items():
    for _word in _text.split():
        WEIGHT[_word] = (_kind, 3)
STEMS = sorted((word for word in WEIGHT if len(word) >= 6), key=len, reverse=True)


def word_scores(passage: str | None) -> collections.Counter:
    found: collections.Counter = collections.Counter()
    for token in re.findall(r"[a-z][a-z\-]+", plain(passage).lower()):
        entry = WEIGHT.get(token)
        if entry is None:
            entry = next((WEIGHT[stem] for stem in STEMS if token.startswith(stem)), None)
        if entry:
            found[entry[0]] += entry[1]
    return found


def listed_type(passage: str | None) -> tuple[str | None, int]:
    """The informational type the word lists point to, and by how much it leads."""
    ranked = word_scores(passage).most_common()
    if not ranked:
        return None, 0
    return ranked[0][0], ranked[0][1] - (ranked[1][1] if len(ranked) > 1 else 0)


def passage_key(passage: str | None) -> str:
    """Questions about the same text share a key, however the text was typeset."""
    return re.sub(r"[\W_]+", "", plain(passage).lower())[:300]


def passage_type_of(passage: str | None) -> tuple[str | None, str | None]:
    stated = literary_type(passage)
    if stated:
        return stated, "source_intro"
    kind, margin = listed_type(passage)
    return (kind, "word_list") if kind and margin >= MARGIN else (None, None)


# ---------------------------------------------------------------- the tables

def reference_rows(tables: db.Tables) -> tuple[list[dict], list[dict]]:
    skill_ids = {row["name"]: row["skill_id"] for row in tables["skill"]}
    position: collections.Counter = collections.Counter()
    tasks = []
    for task_id, skill, name_zh, name in TASKS:
        position[skill] += 1
        tasks.append({"task_id": task_id, "skill_id": skill_ids[skill], "name_zh": name_zh, "name": name,
                      "position": position[skill]})
    return [dict(row) for row in PASSAGE_TYPES], tasks


def classify(tables: db.Tables, reviewed: dict[str, str] | None = None) -> dict:
    """Set task and passage type on every question row; returns what changed and what is left."""
    reviewed = reviewed or {}
    skills = {row["skill_id"]: row for row in tables["skill"]}
    domains = {row["domain_id"]: row for row in tables["domain"]}
    skill_of = {row["subtype_id"]: skills[row["skill_id"]] for row in tables["subtype"]}
    choices: dict[str, dict[str, str]] = collections.defaultdict(dict)
    for row in tables["choice"]:
        choices[row["question_id"]][row["label"]] = row["text"]
    tables["passage_type"], tables["task"] = reference_rows(tables)

    changed = 0
    counts: collections.Counter = collections.Counter()
    other_skill, pending = [], collections.defaultdict(list)
    for row in tables["question"]:
        skill = skill_of[row["subtype_id"]]
        before = tuple(row.get(key) for key in ("task_id", "task_basis", "passage_type", "passage_type_basis"))
        if domains[skill["domain_id"]]["section"] != SECTION:
            row.update(task_id=None, task_basis=None, passage_type=None, passage_type_basis=None)
        else:
            options = choices[row["question_id"]]
            if row.get("task_basis") != "reviewed":
                answer = options.get(row["correct_answer"] or "", "")
                row["task_id"] = task_of(skill["name"], row["prompt"], [options[label] for label in sorted(options)], answer)
                row["task_basis"] = "rule"
            if TASK_SKILL[row["task_id"]] != skill["name"]:
                other_skill.append((row["question_id"], skill["name"], row["task_id"], row["skill_basis"], row["status"]))
            key = passage_key(row["passage"])
            if key in reviewed:
                row["passage_type"], row["passage_type_basis"] = reviewed[key], "reviewed"
            elif row.get("passage_type_basis") != "reviewed":
                row["passage_type"], row["passage_type_basis"] = passage_type_of(row["passage"])
            if row["passage_type"] is None:
                pending[key].append(row)
            counts[("task", row["status"], row["task_id"])] += 1
            counts[("passage", row["status"], row["passage_type"] or "(none yet)", row["passage_type_basis"] or "-")] += 1
        changed += before != tuple(row.get(key) for key in ("task_id", "task_basis", "passage_type", "passage_type_basis"))
    return {"changed": changed, "counts": counts, "other_skill": other_skill, "pending": pending}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--apply", action="store_true", help="write the result to content/db")
    parser.add_argument("--review-out", type=Path, help="write the passages that still need a type, one JSON object per line")
    parser.add_argument("--review-in", type=Path, help="read decided types: lines of {\"key\": ..., \"passage_type\": ...}")
    parser.add_argument("--root", type=Path, default=db.ROOT)
    args = parser.parse_args(argv)

    tables, problems = db.read_tables(args.root)
    if problems:
        print("\n".join(problems), file=sys.stderr)
        return 1
    reviewed = {}
    if args.review_in:
        allowed = {row["passage_type"] for row in PASSAGE_TYPES}
        for line in args.review_in.read_text(encoding="utf-8").splitlines():
            if line.strip():
                entry = json.loads(line)
                if entry["passage_type"] not in allowed:
                    print(f"unknown passage type {entry['passage_type']!r}", file=sys.stderr)
                    return 1
                reviewed[entry["key"]] = entry["passage_type"]
    outcome = classify(tables, reviewed)

    for key, count in sorted(outcome["counts"].items(), key=lambda item: tuple(str(part) for part in item[0])):
        print(f"{count:6d}  " + "  ".join(str(part) for part in key))
    if outcome["other_skill"]:
        print(f"{len(outcome['other_skill'])} question(s) whose task belongs to another skill than the recorded one:")
        for entry in outcome["other_skill"]:
            print("   ", *entry)
    waiting = outcome["pending"]
    print(f"{sum(len(rows) for rows in waiting.values())} question(s) in {len(waiting)} passage(s) still need a passage type")
    if args.review_out:
        lines = []
        for key, rows in sorted(waiting.items()):
            kind, margin = listed_type(rows[0]["passage"])
            lines.append(json.dumps({"key": key, "questions": [row["question_id"] for row in rows], "guess": kind,
                                     "margin": margin, "text": plain(rows[0]["passage"])[:420]}, ensure_ascii=False))
        args.review_out.write_text("\n".join(lines) + ("\n" if lines else ""), encoding="utf-8")
        print("wrote", args.review_out)
    print(f"{outcome['changed']} question(s) " + ("changed" if args.apply else "would change"))
    if args.apply:
        db.load_sqlite(tables, args.root).close()
        for name in db.write_tables(tables, args.root):
            print("wrote", name)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
