"""The 11 passes. Each checks one thing and files findings.

Passes that need the AI say so; the rest run on rules alone (and in demo mode).
Nothing here changes the manuscript. Findings are suggestions the writer accepts or rejects.
"""
import json
import math
import re
import statistics
from collections import Counter, defaultdict

import requests

from . import archivist, db
from . import textutil as tu
from .llm import LLMError

PASSES = [
    {"key": "structure", "name": "Structure", "agent": "Story Editor", "scope": "book", "model": True,
     "checks": "Story beats, act breaks, where the midpoint lands, scenes that don't move the plot"},
    {"key": "continuity", "name": "Continuity", "agent": "Archivist", "scope": "any", "model": False,
     "checks": "Facts that contradict the Story Bible"},
    {"key": "storylines", "name": "Storylines", "agent": "Archivist", "scope": "book", "model": False,
     "checks": "Dropped subplots, payoffs with no setup, threads quiet too long"},
    {"key": "pacing", "name": "Pacing", "agent": "Story Editor", "scope": "any", "model": False,
     "checks": "Chapter and scene lengths, dialogue balance, flat tension, early climax"},
    {"key": "characterization", "name": "Characterization", "agent": "Character Editor",
     "scope": "any", "model": False, "checks": "Voice drift and characters acting out of character"},
    {"key": "repetition", "name": "Repetition", "agent": "Concordance", "scope": "any", "model": False,
     "checks": "Repeated passages, pet phrases, scenes that cover the same beat"},
    {"key": "flow", "name": "Flow", "agent": "Copy Editor", "scope": "chapter", "model": False,
     "checks": "Transitions, sentence rhythm, POV and tense slips"},
    {"key": "line_edit", "name": "Line edit", "agent": "Copy Editor", "scope": "chapter", "model": False,
     "checks": "Filter words, adverbs, clichés, weak openers, word echoes, long sentences"},
    {"key": "grammar", "name": "Grammar and spelling", "agent": "Copy Editor", "scope": "chapter",
     "model": False, "checks": "Grammar, spelling, homophones (LanguageTool if running, else the AI)"},
    {"key": "punctuation", "name": "Punctuation", "agent": "Copy Editor", "scope": "chapter",
     "model": False, "checks": "Dialogue punctuation, dashes, ellipses, quote marks, spacing, consistency"},
    {"key": "factcheck", "name": "Fact check", "agent": "Reference Librarian", "scope": "any",
     "model": False, "checks": "Real-world claims worth checking (you approve what gets looked up)"},
]
PASS_KEYS = [p["key"] for p in PASSES]
BY_KEY = {p["key"]: p for p in PASSES}

PRESETS = {
    "first_read": {"name": "First read", "passes": ["structure", "continuity", "storylines", "pacing"],
                   "for": "A fresh draft"},
    "revision": {"name": "Revision", "passes": ["continuity", "characterization", "repetition", "flow"],
                 "for": "A second draft"},
    "polish": {"name": "Polish", "passes": ["line_edit", "grammar", "punctuation"],
               "for": "A near-final chapter"},
    "final": {"name": "Final check", "passes": ["continuity", "grammar", "punctuation", "factcheck"],
              "for": "Before it goes out"},
}

FRAMEWORKS = {
    "7-point": ["Hook", "Plot Turn 1", "Pinch Point 1", "Midpoint", "Pinch Point 2", "Plot Turn 2",
                "Resolution"],
    "three-act": ["Setup", "Inciting Incident", "First Plot Point", "Midpoint", "Second Plot Point",
                  "Climax", "Resolution"],
    "hero's journey": ["Ordinary World", "Call to Adventure", "Refusal of the Call", "Meeting the Mentor",
                       "Crossing the Threshold", "Tests, Allies, Enemies", "Approach",
                       "The Ordeal", "Reward", "The Road Back", "Resurrection", "Return with the Elixir"],
    "save the cat": ["Opening Image", "Theme Stated", "Set-up", "Catalyst", "Debate",
                     "Break into Two", "B Story", "Fun and Games", "Midpoint", "Bad Guys Close In",
                     "All Is Lost", "Dark Night of the Soul", "Break into Three", "Finale",
                     "Final Image"],
}
# Rough healthy ranges (share of the book) for beats that have one.
BEAT_RANGES = {"Plot Turn 1": (0.0, 0.40), "Midpoint": (0.35, 0.65), "Plot Turn 2": (0.60, 0.95),
               "First Plot Point": (0.15, 0.35), "Second Plot Point": (0.65, 0.90),
               "Catalyst": (0.0, 0.20), "Break into Two": (0.15, 0.35), "All Is Lost": (0.65, 0.85)}

ISSUES_SCHEMA = {"type": "object", "properties": {"issues": {"type": "array", "items": {
    "type": "object", "properties": {
        "quote": {"type": "string"}, "problem": {"type": "string"}, "fix": {"type": "string"},
        "fact_id": {"type": "integer"}, "character": {"type": "string"},
        "severity": {"type": "string", "enum": ["high", "medium", "low"]}},
    "required": ["quote", "problem"]}}}, "required": ["issues"]}


# ---------------------------------------------------------------- context

class Ctx:
    def __init__(self, llm, book_id, chapter_ids, cfg, progress=None, stop=None):
        self.llm = llm
        self.cfg = cfg
        self.has_model = getattr(llm, "name", "") != "demo"
        self.progress = progress or (lambda *a: None)
        self.stop = stop or (lambda: False)
        self.found = []
        self.notes = []
        with db.tx() as c:
            self.book = db.row(c.execute("SELECT * FROM books WHERE id=?", (book_id,)))
            self.series_id = self.book["series_id"]
            self.all_chapters = db.rows(c.execute(
                "SELECT * FROM chapters WHERE book_id=? ORDER BY position", (book_id,)))
            for ch in self.all_chapters:
                ch["scenes"] = db.rows(c.execute(
                    "SELECT * FROM scenes WHERE chapter_id=? ORDER BY position", (ch["id"],)))
        ids = set(chapter_ids or [])
        self.chapters = [ch for ch in self.all_chapters if not ids or ch["id"] in ids]
        self.whole_book = not ids or len(self.chapters) == len(self.all_chapters)
        self.scenes = [s for ch in self.chapters for s in ch["scenes"]]
        self.all_scenes = [s for ch in self.all_chapters for s in ch["scenes"]]
        self._names = None

    def scene_by_id(self, sid):
        return next((s for s in self.all_scenes if s["id"] == sid), None)

    def label(self, scene):
        with db.tx() as c:
            return archivist.where(c, scene["id"])

    def names(self):
        """Every character/place name and alias, lowercased (the writer's lexicon)."""
        if self._names is None:
            out = set()
            with db.tx() as c:
                for e in c.execute("SELECT name, aliases FROM entities WHERE series_id=?",
                                   (self.series_id,)):
                    for n in [e[0]] + db.jload(e[1], []):
                        for w in tu.words(n):
                            out.add(w.lower())
            self._names = out
        return self._names

    def add(self, scene, message, quote=None, start=None, end=None, suggestion=None,
            severity="medium", kind=None, confidence=0.7, fact_ids=None):
        if scene is not None and start is None and quote:
            span = tu.anchor(scene["text"], quote)
            if span:
                start, end = span
        if scene is not None and start is not None and not quote:
            quote = scene["text"][start:end]
        self.found.append({
            "scene_id": scene["id"] if scene else None,
            "chapter_id": scene["chapter_id"] if scene else None,
            "start": start, "end": end, "quote": (quote or "")[:500], "message": message,
            "suggestion": suggestion, "severity": severity, "kind": kind,
            "confidence": confidence, "fact_ids": fact_ids or []})

    def ask_model(self, system, user, schema=None):
        """One model call. Returns parsed JSON (or text), or None if it failed."""
        try:
            if schema is not None:
                return self.llm.json(system, user, schema)
            return self.llm.text(system, user)
        except LLMError as e:
            self.notes.append(str(e)[:200])
            return None


def _model_issues(ctx, scene, system, user, kind, default_sev="medium", confidence=0.65):
    data = ctx.ask_model(system, user, ISSUES_SCHEMA)
    for it in (data or {}).get("issues", [])[:15]:
        q = (it.get("quote") or "").strip()
        if not q or not tu.anchor(scene["text"], q):
            continue  # the model must point at real words in the scene
        ctx.add(scene, it.get("problem", "").strip() or "Check this", quote=q,
                suggestion=(it.get("fix") or "").strip() or None,
                severity=it.get("severity") or default_sev, kind=kind, confidence=confidence,
                fact_ids=[it["fact_id"]] if it.get("fact_id") else None)


# ---------------------------------------------------------------- 1 structure

def p_structure(ctx):
    beats = FRAMEWORKS.get(ctx.cfg.get("framework", "7-point"), FRAMEWORKS["7-point"])
    if not ctx.has_model:
        ctx.notes.append("Structure needs the AI model to label beats; it was skipped in demo mode.")
        return
    schema = {"type": "object", "properties": {"scenes": {"type": "array", "items": {
        "type": "object", "properties": {
            "n": {"type": "integer"}, "beat": {"type": "string"},
            "moves_plot": {"type": "boolean"}, "why": {"type": "string"}},
        "required": ["n", "beat", "moves_plot"]}}}, "required": ["scenes"]}
    system = ("You are a story editor. For each numbered scene summary, name which story beat it "
              f"carries, choosing from: {', '.join(beats)}, or \"none\". Most scenes are \"none\". "
              "Say whether the scene moves the plot (something changes), and why in a few words.")
    total_words = sum(s["word_count"] or 0 for s in ctx.all_scenes) or 1
    pos = 0
    positions = {}
    for ci, ch in enumerate(ctx.all_chapters):
        if ctx.stop():
            return
        ctx.progress(ci, len(ctx.all_chapters), f"Structure: {ch['title']}")
        lines = []
        for i, s in enumerate(ch["scenes"], 1):
            summ = s["summary"] or s["text"][:400]
            lines.append(f"{i}. {summ}")
        data = ctx.ask_model(system, f"Chapter {ch['position']}: {ch['title']}\n" + "\n".join(lines),
                             schema) or {}
        labels = {int(x.get("n", 0)): x for x in data.get("scenes", []) if isinstance(x, dict)}
        for i, s in enumerate(ch["scenes"], 1):
            x = labels.get(i, {})
            beat = x.get("beat") if x.get("beat") in beats else None
            moves = 1 if x.get("moves_plot", True) else 0
            with db.tx() as c:
                c.execute("UPDATE scenes SET beat=?, moves_plot=? WHERE id=?", (beat, moves, s["id"]))
            s["beat"], s["moves_plot"] = beat, moves
            share = (pos + (s["word_count"] or 0) / 2) / total_words
            if beat and beat not in positions:
                positions[beat] = (share, s)
            if not moves and x:
                ctx.add(s, "This scene may not move the plot. " + (x.get("why") or ""),
                        severity="low", kind="static_scene", confidence=0.55)
            pos += s["word_count"] or 0
    # Missing beats, order, and placement
    for b in beats:
        if b not in positions:
            ctx.add(None, f"No scene carries the “{b}” beat.", severity="medium",
                    kind="missing_beat", confidence=0.6)
    order = [b for b in beats if b in positions]
    for a, b in zip(order, order[1:]):
        if positions[b][0] < positions[a][0]:
            ctx.add(positions[b][1], f"“{b}” comes before “{a}”, out of the usual order.",
                    severity="medium", kind="beat_order", confidence=0.6)
    for b, (lo, hi) in BEAT_RANGES.items():
        if b in positions:
            share, s = positions[b]
            if not lo <= share <= hi:
                ctx.add(s, f"“{b}” lands at {share:.0%} of the book. It usually sits "
                           f"between {lo:.0%} and {hi:.0%}.", severity="low", kind="beat_position",
                        confidence=0.6)


# ---------------------------------------------------------------- 2 continuity

def p_continuity(ctx):
    scene_ids = {s["id"] for s in ctx.scenes}
    with db.tx() as c:
        # Conflicts the Bible already knows about, pinned to the lines in this book.
        for r in db.rows(c.execute(
                "SELECT f.id fid, f.value, f.field, e.name, fs.scene_id, fs.quote FROM facts f "
                "JOIN entities e ON e.id=f.entity_id JOIN fact_sources fs ON fs.fact_id=f.id "
                "WHERE e.series_id=? AND f.status='conflict' AND fs.kind='text'", (ctx.series_id,))):
            if r["scene_id"] in scene_ids:
                ctx.add(ctx.scene_by_id(r["scene_id"]),
                        f"{r['name']}'s {r['field']} is “{r['value']}” here, but the text says "
                        "otherwise elsewhere. Settle it at the Reference Desk.",
                        quote=r["quote"], severity="high", kind="bible_conflict", fact_ids=[r["fid"]],
                        confidence=0.9)
        for r in db.rows(c.execute("SELECT * FROM fixes WHERE series_id=? AND status='open'",
                                   (ctx.series_id,))):
            if r["scene_id"] in scene_ids:
                ctx.add(ctx.scene_by_id(r["scene_id"]), r["note"], quote=r["quote"], severity="high",
                        kind="canon_fix", confidence=0.95)
    if not ctx.has_model:
        ctx.notes.append("Continuity checked the Bible's known conflicts. The AI line-by-line check "
                         "was skipped in demo mode.")
        return
    system = ("You check a novel scene for continuity errors against the Story Bible facts given. "
              "Report only sentences that clearly CONTRADICT a listed fact (not things that are merely "
              "new). quote = the exact words from the scene. fact_id = the fact number. "
              "problem = what disagrees. fix = how to change the sentence to match. "
              "If nothing contradicts, return an empty list.")
    for i, s in enumerate(ctx.scenes):
        if ctx.stop():
            return
        ctx.progress(i, len(ctx.scenes), f"Continuity: {ctx.label(s)}")
        facts = _facts_for_scene(ctx, s)
        if not facts:
            continue
        notes = "\n".join(f"[{f['id']}] {f['name']} · {f['field']}: {f['value']} ({f['status']})"
                          for f in facts)
        _model_issues(ctx, s, system, f"STORY BIBLE FACTS:\n{notes}\n\nSCENE:\n{s['text']}",
                      "contradiction", "high", 0.7)


def _facts_for_scene(ctx, s, limit=40):
    with db.tx() as c:
        ents = [r[0] for r in c.execute("SELECT entity_id FROM appearances WHERE scene_id=?",
                                        (s["id"],))]
        low = s["text"].lower()
        for e in db.rows(c.execute("SELECT id, name, aliases FROM entities WHERE series_id=?",
                                   (ctx.series_id,))):
            if e["id"] not in ents and any(
                    n.lower() in low for n in [e["name"]] + db.jload(e["aliases"], []) if len(n) > 2):
                ents.append(e["id"])
        if not ents:
            return []
        q = ",".join("?" * len(ents))
        return db.rows(c.execute(
            f"SELECT f.id, f.field, f.value, f.status, e.name FROM facts f JOIN entities e "
            f"ON e.id=f.entity_id WHERE f.entity_id IN ({q}) AND f.status IN ('canon','found') "
            f"AND f.field NOT IN ('location','holder','status','mood','condition','whereabouts') "
            f"AND (f.book_id IS NULL OR f.book_id=? OR f.field NOT IN ({','.join('?' * len(archivist.PER_BOOK_FIELDS))})) "
            f"ORDER BY CASE f.status WHEN 'canon' THEN 0 ELSE 1 END LIMIT ?",
            (*ents, ctx.book["id"], *archivist.PER_BOOK_FIELDS, limit)))


# ---------------------------------------------------------------- 3 storylines

def p_storylines(ctx):
    matrix = archivist.subplot_matrix(ctx.book["id"])
    quiet = {r["thread"]: r["longest_quiet"] for r in matrix["rows"]}
    for t in archivist.storylines(ctx.series_id):
        beats = t["beats"]
        in_book = [b for b in beats if ctx.scene_by_id(b["scene_id"])]
        if not in_book:
            continue
        if beats[0]["role"] == "resolves" and len(beats) == 1:
            ctx.add(ctx.scene_by_id(beats[0]["scene_id"]),
                    f"“{t['name']}” is resolved here, but it's never set up earlier.",
                    severity="medium", kind="payoff_no_setup", confidence=0.6)
        if t["status"] == "open":
            last = in_book[-1]
            ctx.add(ctx.scene_by_id(last["scene_id"]),
                    f"“{t['name']}” last comes up here and is never resolved. If it's meant "
                    "for Book 3, mark it as planned at the Reference Desk.",
                    severity="medium", kind="open_thread", confidence=0.6)
        q = quiet.get(t["name"], 0)
        if q >= 6:
            ctx.add(ctx.scene_by_id(in_book[-1]["scene_id"]),
                    f"“{t['name']}” goes quiet for {q} chapters in this book. Readers may "
                    "forget it.", severity="low", kind="quiet_thread", confidence=0.6)


# ---------------------------------------------------------------- 4 pacing

def p_pacing(ctx):
    lengths = [ch["word_count"] or 0 for ch in ctx.all_chapters if ch["word_count"]]
    if len(lengths) >= 3:
        med = statistics.median(lengths)
        for ch in ctx.chapters:
            if not ch["scenes"]:
                continue
            w = ch["word_count"] or 0
            if w > 2.2 * med:
                ctx.add(ch["scenes"][0], f"This chapter runs {w:,} words, more than twice the book's "
                        f"typical {int(med):,}. Consider splitting it.", severity="low",
                        kind="long_chapter", confidence=0.7)
            elif w < 0.35 * med:
                ctx.add(ch["scenes"][0], f"This chapter is only {w:,} words against a typical "
                        f"{int(med):,}.", severity="low", kind="short_chapter", confidence=0.6)
    # Long stretches with almost no dialogue
    run = []
    for s in ctx.scenes + [None]:
        if s is not None and tu.dialogue_ratio(s["text"]) < 0.04 and (s["word_count"] or 0) > 300:
            run.append(s)
            continue
        if len(run) >= 3:
            words = sum(x["word_count"] or 0 for x in run)
            ctx.add(run[0], f"{len(run)} scenes in a row ({words:,} words) with almost no dialogue, "
                    f"starting here. Check that the pace holds.", severity="low",
                    kind="no_dialogue_run", confidence=0.6)
        run = []
    # Scenes where nothing is recorded as happening
    with db.tx() as c:
        with_events = {r[0] for r in c.execute(
            "SELECT DISTINCT scene_id FROM events WHERE book_id=?", (ctx.book["id"],))}
    if with_events:
        run = []
        for s in ctx.scenes + [None]:
            if s is not None and s["id"] not in with_events and s["read_fingerprint"]:
                run.append(s)
                continue
            if len(run) >= 3:
                ctx.add(run[0], f"Nothing that matters later happens for {len(run)} scenes, "
                        "starting here.", severity="medium", kind="no_events", confidence=0.55)
            run = []
    if not ctx.has_model:
        return
    # Tension curve from the scene summaries
    schema = {"type": "object", "properties": {"scenes": {"type": "array", "items": {
        "type": "object", "properties": {"n": {"type": "integer"}, "tension": {"type": "integer"}},
        "required": ["n", "tension"]}}}, "required": ["scenes"]}
    system = ("Rate the tension of each numbered scene from 1 (calm) to 5 (highest stakes), "
              "judging from the summary only.")
    for ci, ch in enumerate(ctx.chapters):
        if ctx.stop():
            return
        ctx.progress(ci, len(ctx.chapters), f"Pacing: {ch['title']}")
        lines = [f"{i}. {s['summary'] or s['text'][:300]}" for i, s in enumerate(ch["scenes"], 1)]
        data = ctx.ask_model(system, "\n".join(lines), schema) or {}
        for x in data.get("scenes", []):
            i = int(x.get("n", 0)) - 1
            if 0 <= i < len(ch["scenes"]):
                t = max(1, min(5, int(x.get("tension", 3))))
                ch["scenes"][i]["tension"] = t
                with db.tx() as c:
                    c.execute("UPDATE scenes SET tension=? WHERE id=?", (t, ch["scenes"][i]["id"]))
    tens = [s for s in ctx.scenes if s.get("tension")]
    run = []
    for s in tens + [None]:
        if s is not None and s["tension"] <= 2:
            run.append(s)
            continue
        if len(run) >= 4:
            ctx.add(run[0], f"Tension stays low for {len(run)} scenes, starting here.",
                    severity="medium", kind="flat_tension", confidence=0.55)
        run = []
    if ctx.whole_book and len(tens) >= 8:
        peak = max(range(len(tens)), key=lambda i: tens[i]["tension"])
        share = peak / len(tens)
        if share < 0.6:
            ctx.add(tens[peak], f"The highest-tension scene comes {share:.0%} of the way in. "
                    "The climax usually builds later.", severity="low", kind="early_peak",
                    confidence=0.5)


# ---------------------------------------------------------------- 5 characterization

def attribute_dialogue(text, names):
    """[(start, end, speaker or None)] for each quoted line. Looks for 'X said' near the quote."""
    out = []
    tag = re.compile(rf"\b(?:([A-Z][\w'’-]+)\s+(?:{tu.TAG_VERBS})|(?:{tu.TAG_VERBS})\s+([A-Z][\w'’-]+))\b")
    last = None
    for s, e in tu.dialogue_spans(text):
        window = text[e:e + 60] + " " + text[max(0, s - 60):s]
        speaker = None
        for m in tag.finditer(window):
            cand = m.group(1) or m.group(2)
            if cand and cand.lower() in names:
                speaker = cand
                break
        if speaker is None and last and s - last[1] < 400:
            speaker = None  # alternating speakers are too risky to guess
        out.append((s, e, speaker))
        last = (s, e)
    return out


def _voice_stats(lines):
    text = " ".join(lines)
    ws = tu.words(text)
    if len(ws) < 30:
        return None
    sents = [x for x in re.split(r"[.!?]+", text) if x.strip()]
    return {"avg_len": len(ws) / max(1, len(sents)),
            "contractions": sum(1 for w in ws if "'" in w or "’" in w) / len(ws),
            "exclaim": text.count("!") / max(1, len(sents)), "words": len(ws)}


def p_characterization(ctx):
    names = ctx.names()
    book_lines = defaultdict(list)
    scope_lines = defaultdict(lambda: defaultdict(list))  # chapter -> speaker -> lines
    scope_ids = {s["id"] for s in ctx.scenes}
    for s in ctx.all_scenes:
        for a, b, who in attribute_dialogue(s["text"], names):
            if who:
                book_lines[who].append(s["text"][a:b])
                if s["id"] in scope_ids:
                    scope_lines[s["chapter_id"]][who].append((s, a, b))
    for ch_id, speakers in scope_lines.items():
        for who, items in speakers.items():
            base = _voice_stats(book_lines[who])
            here = _voice_stats([s["text"][a:b] for s, a, b in items])
            if not base or not here or base["words"] < 300:
                continue
            notes = []
            if here["avg_len"] > base["avg_len"] * 1.8:
                notes.append("speaks in much longer sentences than usual")
            if base["contractions"] > 0.02 and here["contractions"] < base["contractions"] * 0.3:
                notes.append("almost stops using contractions (sounds more formal)")
            if here["exclaim"] > base["exclaim"] * 3 and here["exclaim"] > 0.3:
                notes.append("exclaims far more than usual")
            if notes:
                s, a, b = items[0]
                ctx.add(s, f"{who} {', and '.join(notes)} in this chapter.", start=a, end=b,
                        severity="low", kind="voice_drift", confidence=0.5)
    if not ctx.has_model:
        return
    system = ("You are a character editor. Using the character notes, flag dialogue lines that don't "
              "sound like that character, and actions that go against their stated goals or fears "
              "with no reason given. quote = exact words from the scene. character = who. "
              "problem = why it feels off. fix = a suggested rewrite of the quoted words. "
              "Flag only clear cases; return an empty list if none.")
    for i, s in enumerate(ctx.scenes):
        if ctx.stop():
            return
        speakers = {w for _, _, w in attribute_dialogue(s["text"], names) if w}
        if not speakers:
            continue
        ctx.progress(i, len(ctx.scenes), f"Characterization: {ctx.label(s)}")
        cards = []
        for card in archivist.bible(ctx.series_id, "character"):
            if card["name"].split()[0] in speakers or any(a in speakers for a in card["aliases"]):
                facts = [f"{f['field']}: {f['value']}" for f in card["facts"]
                         if f["status"] in ("canon", "found")][:14]
                cards.append(f"{card['name']}: " + "; ".join(facts))
        if cards:
            _model_issues(ctx, s, system, "CHARACTERS:\n" + "\n".join(cards) +
                          f"\n\nSCENE:\n{s['text']}", "out_of_character", "medium", 0.6)


# ---------------------------------------------------------------- 6 repetition

def _shingles(text, n=5):
    ws = [w.lower() for w in tu.words(text)]
    return {" ".join(ws[i:i + n]) for i in range(len(ws) - n + 1)}


def p_repetition(ctx):
    paras = []  # (scene, start, end, shingles)
    for s in ctx.all_scenes:
        for a, b, t in tu.paragraphs(s["text"]):
            if tu.word_count(t) >= 30:
                paras.append((s, a, b, _shingles(t)))
    index = defaultdict(list)
    for i, p in enumerate(paras):
        for sh in p[3]:
            index[sh].append(i)
    pairs = Counter()
    for ids in index.values():
        if 1 < len(ids) <= 20:
            for x in range(len(ids)):
                for y in range(x + 1, len(ids)):
                    pairs[(ids[x], ids[y])] += 1
    scope_ids = {s["id"] for s in ctx.scenes}
    flagged = set()
    for (i, j), shared in pairs.items():
        if shared < 4:
            continue
        a, b = paras[i], paras[j]
        jac = shared / len(a[3] | b[3])
        if jac < 0.3 or b[0]["id"] not in scope_ids or j in flagged:
            continue
        flagged.add(j)
        ctx.add(b[0], f"This paragraph repeats much of one in {ctx.label(a[0])}: "
                f"“{a[0]['text'][a[1]:a[1] + 90].strip()}…”", start=b[1], end=b[2],
                severity="medium", kind="repeated_passage", confidence=min(0.95, 0.5 + jac))
    # Pet phrases: 4-word phrases used again and again
    grams = Counter()
    where = defaultdict(list)
    for s in ctx.all_scenes:
        low = s["text"]
        toks = [(m.group(0).lower(), m.start(), m.end()) for m in tu.WORD_RE.finditer(low)]
        for k in range(len(toks) - 3):
            g = tuple(t[0] for t in toks[k:k + 4])
            if sum(1 for w in g if w not in tu.STOPWORDS) < 2:
                continue
            key = " ".join(g)
            grams[key] += 1
            where[key].append((s, toks[k][1], toks[k + 3][2]))
    for key, n in grams.most_common(20):
        if n < 4:
            break
        for s, a, b in where[key][2:]:
            if s["id"] in scope_ids:
                ctx.add(s, f"“{key}” appears {n} times in this book.", start=a, end=b,
                        severity="low", kind="pet_phrase", confidence=0.6)
    # Scenes that may cover the same beat (needs the embedding model)
    if ctx.has_model and hasattr(ctx.llm, "embed"):
        summed = [s for s in ctx.all_scenes if s.get("summary")]
        if len(summed) >= 4:
            try:
                vecs = ctx.llm.embed([s["summary"] for s in summed])
            except LLMError as e:
                ctx.notes.append(f"Scene-similarity check skipped: {e}")
                return
            for i in range(len(summed)):
                for j in range(i + 2, len(summed)):
                    if summed[j]["id"] not in scope_ids:
                        continue
                    sim = _cos(vecs[i], vecs[j])
                    if sim >= 0.92:
                        ctx.add(summed[j], f"This scene may repeat the beat of {ctx.label(summed[i])}: "
                                f"“{summed[i]['summary'][:120]}”", severity="low",
                                kind="similar_scene", confidence=round(sim - 0.3, 2))


def _cos(a, b):
    dot = sum(x * y for x, y in zip(a, b))
    na, nb = math.sqrt(sum(x * x for x in a)), math.sqrt(sum(y * y for y in b))
    return dot / (na * nb) if na and nb else 0.0


# ---------------------------------------------------------------- 7 flow

PAST = {"was", "were", "had", "did"}
PRESENT = {"is", "are", "has", "does", "am"}


def p_flow(ctx):
    narr_all = " ".join(tu.narration(s["text"]) for s in ctx.all_scenes)
    first = len(re.findall(r"\bI\b", narr_all))
    third = len(re.findall(r"\b(?:he|she|they)\b", narr_all, re.I))
    third_person = third > first * 3
    for s in ctx.scenes:
        text, narr = s["text"], tu.narration(s["text"])
        counts = Counter()
        ptense = []
        for a, b, _ in tu.paragraphs(narr):
            ws = [w.lower() for w in tu.words(narr[a:b])]
            pa, pr = sum(w in PAST for w in ws), sum(w in PRESENT for w in ws)
            counts["past"] += pa
            counts["present"] += pr
            ptense.append((a, b, pa, pr, len(ws)))
        main = "past" if counts["past"] >= counts["present"] else "present"
        for a, b, pa, pr, n in ptense:
            if n < 15:
                continue
            if main == "past" and pr >= 3 and pa == 0:
                ctx.add(s, "This paragraph slips into present tense; the scene is in past tense.",
                        start=a, end=min(b, a + 160), severity="medium", kind="tense_slip",
                        confidence=0.55)
            elif main == "present" and pa >= 3 and pr == 0:
                ctx.add(s, "This paragraph slips into past tense; the scene is in present tense.",
                        start=a, end=min(b, a + 160), severity="medium", kind="tense_slip",
                        confidence=0.55)
        if third_person:
            for m in re.finditer(r"\bI\b", narr):
                ctx.add(s, "“I” appears in narration, but the book is told in third person. "
                        "POV slip, or a thought that needs italics?", start=m.start(), end=m.end(),
                        severity="low", kind="pov_slip", confidence=0.45)
                break
        sents = tu.sentences(text)
        # Same opener three times in a row
        for k in range(len(sents) - 2):
            ops = [tu.words(x[2])[:1] for x in sents[k:k + 3]]
            if all(ops) and len({o[0].lower() for o in ops}) == 1 and ops[0][0].lower() not in ("i",):
                ctx.add(s, f"Three sentences in a row start with “{ops[0][0]}”.",
                        start=sents[k][0], end=sents[k + 2][1], severity="low", kind="same_opener",
                        confidence=0.6)
        # Monotone rhythm: 5 sentences of nearly the same length
        lens = [tu.word_count(x[2]) for x in sents]
        for k in range(len(lens) - 4):
            w = lens[k:k + 5]
            if min(w) >= 6 and max(w) - min(w) <= 3:
                ctx.add(s, "Five sentences in a row of nearly the same length. Vary the rhythm.",
                        start=sents[k][0], end=sents[k + 4][1], severity="low", kind="rhythm",
                        confidence=0.5)
                break
    if not ctx.has_model:
        return
    system = ("You are a copy editor checking flow in fiction. Flag clunky transitions between "
              "paragraphs or scenes, confusing jumps in time or place, point-of-view slips, and "
              "sentences that are hard to read. quote = exact words. problem = what's wrong. "
              "fix = a smoother version of the quoted words. Flag only clear problems.")
    for i, s in enumerate(ctx.scenes):
        if ctx.stop():
            return
        ctx.progress(i, len(ctx.scenes), f"Flow: {ctx.label(s)}")
        _model_issues(ctx, s, system, f"SCENE:\n{s['text']}", "flow", "low", 0.55)


# ---------------------------------------------------------------- 8 line edit

def p_line_edit(ctx):
    names = ctx.names()
    filt = re.compile(rf"\b(?:he|she|they|I|we|[A-Z][a-z]+)\s+(?:{tu.FILTER_VERBS})\b")
    tagadv = re.compile(rf"\b({tu.TAG_VERBS})\s+([a-z]+ly)\b")
    for s in ctx.scenes:
        text, narr = s["text"], tu.narration(s["text"])
        for m in filt.finditer(narr):
            ctx.add(s, "Filter word: it puts a layer between the reader and the moment. "
                    "Try showing what's seen, heard or felt directly.", start=m.start(), end=m.end(),
                    severity="low", kind="filter_word", confidence=0.5)
        for m in tagadv.finditer(text):
            ctx.add(s, f"Adverb on a dialogue tag. Let the line carry the tone.", start=m.start(),
                    end=m.end(), suggestion=m.group(1), severity="low", kind="tag_adverb",
                    confidence=0.6)
        for m in tu.CLICHE_RE.finditer(text):
            ctx.add(s, "Cliché. A fresher image will land harder.", start=m.start(), end=m.end(),
                    severity="low", kind="cliche", confidence=0.7)
        for m in re.finditer(r"\bsuddenly\b,?\s*", narr, re.I):
            ctx.add(s, "“Suddenly” usually slows the surprise down. Try cutting it.",
                    start=m.start(), end=m.end(), suggestion="", severity="low", kind="suddenly",
                    confidence=0.6)
        for m in re.finditer(r"\bvery\s+(\w+)", narr, re.I):
            ctx.add(s, "“Very” weakens the word after it. Use a stronger word instead.",
                    start=m.start(), end=m.end(), suggestion=m.group(1), severity="low",
                    kind="very", confidence=0.5)
        for m in re.finditer(r"(?:^|(?<=[.!?]\s))(There (?:was|were|is|are)|It was)\b", narr):
            ctx.add(s, "Weak opener. Start with the thing itself.", start=m.start(1), end=m.end(1),
                    severity="low", kind="weak_opener", confidence=0.45)
        for a, b, t in tu.sentences(text):
            n = tu.word_count(t)
            if n > 45:
                ctx.add(s, f"Long sentence ({n} words). Consider breaking it up.", start=a, end=b,
                        severity="low", kind="long_sentence", confidence=0.6)
        # Word echoes: the same word twice within 30 words (narration only)
        toks = [(m.group(0), m.start(), m.end()) for m in tu.WORD_RE.finditer(narr)]
        seen, echoes = {}, 0
        for k, (w, a, b) in enumerate(toks):
            lw = w.lower()
            if len(lw) < 5 or lw in tu.STOPWORDS or lw in names or w[0].isupper():
                continue
            if lw in seen and k - seen[lw] <= 30:
                ctx.add(s, f"“{w}” is used twice close together.", start=a, end=b,
                        severity="low", kind="echo", confidence=0.55)
                echoes += 1
                if echoes >= 5:
                    break
            seen[lw] = k


# ---------------------------------------------------------------- 9 grammar

HEURISTIC_GRAMMAR = [
    (re.compile(r"\b(\w+)\s+\1\b", re.I), "The word “{0}” is doubled.", lambda m: m.group(1)),
    (re.compile(r"\b(could|should|would|must|might) of\b", re.I),
     "“{0} of” should be “{0} have”.", lambda m: f"{m.group(1)} have"),
    (re.compile(r"\balot\b", re.I), "“alot” should be “a lot”.", lambda m: "a lot"),
    (re.compile(r"\b(more|less|better|worse|rather|other|bigger|smaller|older|younger|colder|warmer|"
                r"faster|slower|harder|easier|longer|shorter|taller|higher|lower|darker|brighter|louder|"
                r"quieter|stronger|weaker|closer|further|farther|sooner|greater|worse) then\b", re.I),
     "After a comparison, use “than”.", lambda m: f"{m.group(1)} than"),
    (re.compile(r"\b(its) (a|an|the|been|going|not|time)\b"), "“its” here should probably be "
     "“it's” (it is).", lambda m: f"it's {m.group(2)}"),
    (re.compile(r"\ba ([aeiouAEIOU]\w+)"), "Use “an” before a vowel sound.",
     lambda m: f"an {m.group(1)}"),
    (re.compile(r"\b(definately|seperate|recieve|occured|untill|wierd|beleive|thier|truely|alright)\b",
                re.I), "Spelling: “{0}”.", None),
]
SPELL_FIX = {"definately": "definitely", "seperate": "separate", "recieve": "receive",
             "occured": "occurred", "untill": "until", "wierd": "weird", "beleive": "believe",
             "thier": "their", "truely": "truly", "alright": "all right"}


def _lt_url(ctx):
    url = (ctx.cfg.get("languagetool_url") or "").rstrip("/")
    if not url:
        return None
    try:
        requests.get(f"{url}/v2/languages", timeout=3)
        return url
    except requests.RequestException:
        return None


def p_grammar(ctx):
    names = ctx.names()
    lt = _lt_url(ctx)
    for i, s in enumerate(ctx.scenes):
        if ctx.stop():
            return
        text = s["text"]
        for rx, msg, fix in HEURISTIC_GRAMMAR:
            for m in rx.finditer(text):
                word = m.group(1)
                if rx.pattern.startswith(r"\b(\w+)\s+\1") and word.lower() in ("had", "that", "is"):
                    continue
                if rx.pattern.startswith(r"\ba (") and word.lower().startswith(("uni", "use", "eu",
                                                                                  "one", "u")):
                    continue
                sugg = fix(m) if fix else SPELL_FIX.get(word.lower())
                ctx.add(s, msg.format(word), start=m.start(), end=m.end(), suggestion=sugg,
                        severity="medium", kind="grammar_rule", confidence=0.7)
        if lt:
            ctx.progress(i, len(ctx.scenes), f"Grammar (LanguageTool): {ctx.label(s)}")
            try:
                r = requests.post(f"{lt}/v2/check", data={
                    "text": text, "language": ctx.cfg.get("language", "en-US")}, timeout=60)
                matches = r.json().get("matches", [])
            except (requests.RequestException, ValueError):
                matches = []
            for m in matches:
                cat = m.get("rule", {}).get("category", {}).get("id", "")
                if cat in ("PUNCTUATION", "TYPOGRAPHY", "STYLE", "REDUNDANCY", "CASING"):
                    continue
                a, n = m.get("offset", 0), m.get("length", 0)
                word = text[a:a + n]
                if cat == "TYPOS" and word.lower() in names:
                    continue  # the writer's own names and invented words
                reps = [x["value"] for x in m.get("replacements", [])[:1]]
                ctx.add(s, m.get("message", "Check this"), start=a, end=a + n,
                        suggestion=reps[0] if reps else None, severity="medium",
                        kind="languagetool", confidence=0.75)
        elif ctx.has_model:
            ctx.progress(i, len(ctx.scenes), f"Grammar (AI): {ctx.label(s)}")
            system = ("You proofread fiction for grammar and spelling errors only (not style, not "
                      "dialect, not invented words or names, not deliberate fragments). "
                      f"Names and invented words to leave alone: {', '.join(sorted(names)[:80])}. "
                      "quote = the exact wrong words. fix = the corrected words. problem = the rule.")
            _model_issues(ctx, s, system, f"SCENE:\n{text}", "grammar_ai", "medium", 0.6)
    if not lt:
        ctx.notes.append("LanguageTool isn't running, so grammar used built-in rules"
                         + (" and the AI." if ctx.has_model else " only."))


# ---------------------------------------------------------------- 10 punctuation

def p_punctuation(ctx):
    book = "\n".join(s["text"] for s in ctx.all_scenes)
    curly = book.count("“") + book.count("”")
    straight = book.count('"')
    uni_ell, dots = book.count("…"), len(re.findall(r"\.\.\.", book))
    # Hyphenation variants: "half-elf" vs "half elf" vs "halfelf"
    toks = [w.lower() for w in re.findall(r"[A-Za-z]+(?:-[A-Za-z]+)?", book)]
    uni = Counter(toks)
    bi = Counter(zip(toks, toks[1:]))
    variants = {}
    for h, n in uni.items():
        if "-" not in h or h.count("-") != 1:
            continue
        a, b = h.split("-", 1)
        joined, spaced = uni.get(a + b, 0), bi.get((a, b), 0)
        if joined + spaced:
            forms = {h: n, a + b: joined, f"{a} {b}": spaced}
            variants[h] = max(forms, key=forms.get)
    tag = rf"(?:{tu.TAG_VERBS})"
    rules = [
        (re.compile(rf"\.([”\"])(\s+(?:he|she|they|I|we|[A-Z][a-z]+)\s+{tag}\b)"),
         "Use a comma, not a period, before a dialogue tag.", lambda m: "," + m.group(1) + m.group(2)),
        (re.compile(r"(?<=\S)  +(?=\S)"), "Double space.", lambda m: " "),
        (re.compile(r"(?<=\w) +([,.;:!?])(?=\s|$)"), "Space before punctuation.", lambda m: m.group(1)),
        (re.compile(r"(?<=\w)--(?=\w|\s)|(?<=\s)--(?=\s)"), "Use an em dash (—) instead of two hyphens.",
         lambda m: "—"),
        (re.compile(r"([!?]){2,}|!\?|\?!"), "Repeated punctuation. One mark is usually enough.",
         lambda m: m.group(0)[0]),
        (re.compile(r",\s*,"), "Doubled comma.", lambda m: ","),
    ]
    for s in ctx.scenes:
        text = s["text"]
        for rx, msg, fix in rules:
            for m in rx.finditer(text):
                ctx.add(s, msg, start=m.start(), end=m.end(), suggestion=fix(m), severity="low",
                        kind="punct_rule", confidence=0.75)
        if curly and straight:
            minority, fix = ('"', None) if curly >= straight else ("“", '"')
            for m in re.finditer(re.escape(minority) if minority == '"' else "[“”]", text):
                ctx.add(s, "Quote marks are mixed in this book (straight and curly). Pick one style.",
                        start=m.start(), end=m.end(), severity="low", kind="quote_style",
                        confidence=0.6)
                break
        if uni_ell and dots:
            if uni_ell >= dots:
                for m in re.finditer(r"\.\.\.", text):
                    ctx.add(s, "Ellipses are mixed. This book mostly uses …",
                            start=m.start(), end=m.end(), suggestion="…", severity="low",
                            kind="ellipsis_style", confidence=0.65)
            else:
                for m in re.finditer("…", text):
                    ctx.add(s, "Ellipses are mixed. This book mostly uses ...",
                            start=m.start(), end=m.end(), suggestion="...", severity="low",
                            kind="ellipsis_style", confidence=0.65)
        for a, b, p in tu.paragraphs(text):
            if p.count("“") > p.count("”") and not p.rstrip().endswith(("”",)):
                nxt = text[b:].lstrip()
                if not nxt.startswith("“"):
                    ctx.add(s, "A quote opens in this paragraph but never closes.", start=a,
                            end=min(b, a + 120), severity="medium", kind="unclosed_quote",
                            confidence=0.6)
        for h, best in variants.items():
            a_, b_ = h.split("-", 1)
            for form in (h, a_ + b_, f"{a_} {b_}"):
                if form == best:
                    continue
                for m in re.finditer(rf"\b{re.escape(form)}\b", text, re.I):
                    ctx.add(s, f"Spelled “{m.group(0)}” here but “{best}” "
                            "elsewhere in the book.", start=m.start(), end=m.end(),
                            suggestion=best, severity="low", kind="style_sheet", confidence=0.6)


# ---------------------------------------------------------------- 11 fact check

CLAIM_RE = re.compile(r"[^.!?]*\b\d[\d,.]*\s*(?:miles?|kilometers?|km|feet|foot|meters?|yards?|"
                      r"pounds?|kilograms?|kg|degrees?|years?|centuries|days?|hours?|mph|percent|%)"
                      r"\b[^.!?]*[.!?]")


def p_factcheck(ctx):
    if not ctx.has_model:
        for s in ctx.scenes:
            for m in CLAIM_RE.finditer(tu.narration(s["text"])):
                if re.search(r"years? old|\bago\b", m.group(0)):
                    continue  # ages and story time aren't real-world claims
                ctx.add(s, "Real-world claim worth checking: " + m.group(0).strip()[:160],
                        start=m.start(), end=m.end(), severity="low", kind="claim", confidence=0.4)
        ctx.notes.append("Fact check used a simple number-and-unit finder in demo mode.")
        return
    schema = {"type": "object", "properties": {"claims": {"type": "array", "items": {
        "type": "object", "properties": {"quote": {"type": "string"}, "claim": {"type": "string"}},
        "required": ["quote", "claim"]}}}, "required": ["claims"]}
    system = ("Find statements in this fiction scene that make claims about the REAL world that a "
              "reader could check: history, science, geography, medicine, weapons, how things work, "
              "distances and speeds. Ignore invented places, magic and the story's own facts. "
              "quote = exact words. claim = the claim as a short plain statement.")
    for i, s in enumerate(ctx.scenes):
        if ctx.stop():
            return
        ctx.progress(i, len(ctx.scenes), f"Fact check: {ctx.label(s)}")
        data = ctx.ask_model(system, f"SCENE:\n{s['text']}", schema) or {}
        for c in data.get("claims", [])[:10]:
            if c.get("quote") and tu.anchor(s["text"], c["quote"]):
                ctx.add(s, "Real-world claim worth checking: " + c.get("claim", "").strip(),
                        quote=c["quote"], severity="low", kind="claim", confidence=0.6)


RUNNERS = {"structure": p_structure, "continuity": p_continuity, "storylines": p_storylines,
           "pacing": p_pacing, "characterization": p_characterization, "repetition": p_repetition,
           "flow": p_flow, "line_edit": p_line_edit, "grammar": p_grammar,
           "punctuation": p_punctuation, "factcheck": p_factcheck}


# ---------------------------------------------------------------- running

def run_passes(llm, cfg, book_id, keys, chapter_ids=None, progress=None, stop=None):
    """Run passes in the big-to-small order. Returns {pass: findings filed}."""
    keys = [k for k in PASS_KEYS if k in set(keys)]
    progress = progress or (lambda *a: None)
    summary, notes = {}, []
    for n, key in enumerate(keys):
        if stop and stop():
            break
        p = BY_KEY[key]
        scope = None if p["scope"] == "book" else chapter_ids
        ctx = Ctx(llm, book_id, scope, cfg,
                  lambda i, t, msg, n=n: progress(n, len(keys), msg), stop)
        progress(n, len(keys), f"{p['name']}…")
        RUNNERS[key](ctx)
        summary[key] = _save(ctx, key, p)
        notes += ctx.notes
    progress(len(keys), len(keys), "Done")
    return {"filed": summary, "notes": notes}


def _save(ctx, key, p):
    scene_ids = [s["id"] for s in ctx.scenes]
    with db.tx() as c:
        run_id = c.execute("INSERT INTO pass_runs(book_id,pass,chapter_ids,started,note) "
                           "VALUES(?,?,?,?,?)",
                           (ctx.book["id"], key, json.dumps([ch["id"] for ch in ctx.chapters]),
                            db.now(), "; ".join(ctx.notes)[:500])).lastrowid
        # Replace this pass's open findings in scope. Accepted/rejected ones stay.
        if ctx.whole_book:
            c.execute("DELETE FROM findings WHERE book_id=? AND pass=? AND status IN ('open','stale')",
                      (ctx.book["id"], key))
        elif scene_ids:
            q = ",".join("?" * len(scene_ids))
            c.execute(f"DELETE FROM findings WHERE pass=? AND status IN ('open','stale') "
                      f"AND scene_id IN ({q})", (key, *scene_ids))
        rejected = {(r[0], r[1], r[2]) for r in c.execute(
            "SELECT scene_id, quote, message FROM findings WHERE book_id=? AND pass=? "
            "AND status='rejected'", (ctx.book["id"], key))}
        seen, n = set(), 0
        for f in ctx.found:
            sig = (f["scene_id"], f["quote"], f["message"])
            if sig in rejected or (sig, f["start"]) in seen:
                continue
            seen.add((sig, f["start"]))
            c.execute(
                "INSERT INTO findings(book_id,chapter_id,scene_id,pass,agent,kind,severity,start,"
                "\"end\",quote,message,suggestion,confidence,fact_ids,status,run_id,created) "
                "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,'open',?,?)",
                (ctx.book["id"], f["chapter_id"], f["scene_id"], key, p["agent"], f["kind"],
                 f["severity"], f["start"], f["end"], f["quote"], f["message"], f["suggestion"],
                 f["confidence"], json.dumps(f["fact_ids"]), run_id, db.now()))
            n += 1
        c.execute("UPDATE pass_runs SET finished=?, found=? WHERE id=?", (db.now(), n, run_id))
    return n
