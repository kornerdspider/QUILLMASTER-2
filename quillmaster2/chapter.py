"""Chapter mode: the Reading Room's workbenches, applying changes, backups and export.

Text changes happen only when the writer accepts a finding, edits a scene, or applies
a replacement. Every change marks the scene for re-reading so the Story Bible stays current.
"""
import json
import re
import time

from . import archivist, config, db
from . import textutil as tu
from .importer import fingerprint
from .llm import LLMError
from .passes import attribute_dialogue, _voice_stats

PLACE_WORDS = re.compile(r"\b(room|hall|street|road|forest|wood|woods|river|sea|shore|beach|ship|"
                         r"boat|kitchen|bedroom|house|castle|tower|village|town|city|market|inn|"
                         r"tavern|field|hill|mountain|pass|gate|door|garden|cave|camp|tent|office|"
                         r"car|train|station|church|temple|courtyard|square|harbour|harbor|dock|"
                         r"bridge|valley|desert|lake|archive|library|stairs|corridor)\b", re.I)


# ---------------------------------------------------------------- views

def chapter_view(chapter_id):
    with db.tx() as c:
        ch = db.row(c.execute("SELECT * FROM chapters WHERE id=?", (chapter_id,)))
        if not ch:
            return None
        book = db.row(c.execute("SELECT * FROM books WHERE id=?", (ch["book_id"],)))
        scenes = db.rows(c.execute(
            "SELECT id, position, text, word_count, summary, pov, location, time_text, beat, "
            "moves_plot, goal, conflict, outcome, tension, read_fingerprint=fingerprint AS is_read "
            "FROM scenes WHERE chapter_id=? ORDER BY position", (chapter_id,)))
        ids = [s["id"] for s in scenes]
        q = ",".join("?" * len(ids)) or "NULL"
        findings = db.rows(c.execute(
            f"SELECT * FROM findings WHERE scene_id IN ({q}) AND status='open' "
            f"ORDER BY scene_id, start", ids)) if ids else []
        book_level = db.rows(c.execute(
            "SELECT * FROM findings WHERE book_id=? AND scene_id IS NULL AND status='open'",
            (book["id"],)))
        ent_ids = [r[0] for r in c.execute(
            f"SELECT DISTINCT entity_id FROM appearances WHERE scene_id IN ({q})", ids)] if ids else []
        chapters = db.rows(c.execute("SELECT id, position, title FROM chapters WHERE book_id=? "
                                     "ORDER BY position", (book["id"],)))
    for f in findings + book_level:
        f["fact_ids"] = db.jload(f["fact_ids"], [])
    cards = [x for x in archivist.bible(book["series_id"]) if x["id"] in ent_ids]
    low = " ".join(s["text"] for s in scenes).lower()
    for x in archivist.bible(book["series_id"]):  # places and objects named in the text
        if x["id"] not in ent_ids and x["type"] in ("place", "object") and \
                any(n.lower() in low for n in [x["name"]] + x["aliases"] if len(n) > 3):
            cards.append(x)
    return {"chapter": ch, "book": book, "scenes": scenes, "findings": findings,
            "book_findings": book_level, "cards": cards, "chapters": chapters}


def dialogue_view(chapter_id):
    view = chapter_view(chapter_id)
    book = view["book"]
    with db.tx() as c:
        names = set()
        for e in c.execute("SELECT name, aliases FROM entities WHERE series_id=? AND type='character'",
                           (book["series_id"],)):
            for n in [e[0]] + db.jload(e[1], []):
                names |= {w.lower() for w in tu.words(n)}
        all_scenes = db.rows(c.execute("SELECT id, text FROM scenes WHERE book_id=?", (book["id"],)))
    book_lines, lines, chapter_lines = {}, [], {}
    scene_ids = {s["id"] for s in view["scenes"]}
    for s in all_scenes:
        for a, b, who in attribute_dialogue(s["text"], names):
            if who:
                book_lines.setdefault(who, []).append(s["text"][a:b])
            if s["id"] in scene_ids:
                lines.append({"scene_id": s["id"], "start": a, "end": b, "speaker": who,
                              "text": s["text"][a:b]})
                if who:
                    chapter_lines.setdefault(who, []).append(s["text"][a:b])
    speakers = []
    for who, ls in sorted(chapter_lines.items(), key=lambda x: -len(x[1])):
        here, base = _voice_stats(ls), _voice_stats(book_lines.get(who, []))
        speakers.append({"name": who, "lines": len(ls), "words": sum(tu.word_count(x) for x in ls),
                         "here": here, "book": base})
    unattributed = sum(1 for x in lines if not x["speaker"])
    flagged = [f for f in view["findings"] if f["pass"] == "characterization"]
    return {"speakers": speakers, "lines": lines, "unattributed": unattributed, "flagged": flagged}


def setting_view(chapter_id):
    view = chapter_view(chapter_id)
    place_names = [n.lower() for x in archivist.bible(view["book"]["series_id"], "place")
                   for n in [x["name"]] + x["aliases"]]
    out = []
    for s in view["scenes"]:
        counts = {k: 0 for k in tu.SENSES}
        for w in tu.words(s["text"]):
            k = tu.SENSE_WORDS.get(w.lower())
            if k:
                counts[k] += 1
        paras = tu.paragraphs(s["text"])
        opening = " ".join(p[2] for p in paras[:3])
        has_place = bool(PLACE_WORDS.search(opening)) or any(
            p in opening.lower() for p in place_names if len(p) > 2) or bool(
            s["location"] and s["location"].lower() in opening.lower())
        has_time = bool(tu.TIME_WORDS.search(opening))
        notes = []
        if not has_place:
            notes.append(f"The first {min(3, len(paras))} paragraphs don't say where we are.")
        if not has_time:
            notes.append("No time of day or time passing is mentioned at the start.")
        missing = [k for k, v in counts.items() if v == 0]
        present = [k for k in counts if counts[k]]
        if not present:
            notes.append("No sense details yet. Try adding what we see or hear.")
        elif len(missing) >= 3:
            notes.append("Only " + " and ".join(present) + " come through. Try adding "
                         + " or ".join(missing[:2]) + ".")
        out.append({"scene_id": s["id"], "position": s["position"], "senses": counts,
                    "grounded_place": has_place, "grounded_time": has_time,
                    "location": s["location"], "time": s["time_text"], "notes": notes,
                    "words": s["word_count"]})
    return out


def analyze_beats(llm, chapter_id):
    """Goal, conflict and outcome for each scene in a chapter (needs the AI)."""
    if getattr(llm, "name", "") == "demo":
        raise LLMError("Scene beats need the AI model. Switch Reader to Ollama in Settings.")
    schema = {"type": "object", "properties": {
        "goal": {"type": "string"}, "conflict": {"type": "string"}, "outcome": {"type": "string"},
        "turns": {"type": "boolean"}}, "required": ["goal", "conflict", "outcome", "turns"]}
    system = ("You are a story editor. For this scene, say in a few words: the point-of-view "
              "character's goal, what stands in the way (conflict), and how it ends (outcome). "
              "turns = does something change by the end (a value shifts, a decision, a reveal)?")
    view = chapter_view(chapter_id)
    for s in view["scenes"]:
        data = llm.json(system, f"SCENE:\n{s['text']}", schema)
        with db.tx() as c:
            c.execute("UPDATE scenes SET goal=?, conflict=?, outcome=?, moves_plot=? WHERE id=?",
                      (data.get("goal", ""), data.get("conflict", ""), data.get("outcome", ""),
                       1 if data.get("turns", True) else 0, s["id"]))
    return chapter_view(chapter_id)["scenes"]


# ---------------------------------------------------------------- changing the text

def _set_scene_text(c, scene_id, text):
    s = db.row(c.execute("SELECT * FROM scenes WHERE id=?", (scene_id,)))
    c.execute("UPDATE scenes SET text=?, word_count=?, fingerprint=? WHERE id=?",
              (text, tu.word_count(text), fingerprint(text), scene_id))
    words = c.execute("SELECT COALESCE(SUM(word_count),0) FROM scenes WHERE chapter_id=?",
                      (s["chapter_id"],)).fetchone()[0]
    c.execute("UPDATE chapters SET word_count=? WHERE id=?", (words, s["chapter_id"]))
    bw = c.execute("SELECT COALESCE(SUM(word_count),0) FROM scenes WHERE book_id=?",
                   (s["book_id"],)).fetchone()[0]
    c.execute("UPDATE books SET word_count=?, edited=? WHERE id=?", (bw, db.now(), s["book_id"]))
    return s


def replace_span(scene_id, start, end, new, accept_id=None):
    """Replace text[start:end] in one scene and keep every other finding pinned to its words."""
    with db.tx() as c:
        s = db.row(c.execute("SELECT text FROM scenes WHERE id=?", (scene_id,)))
        if not s:
            raise ValueError("No such scene")
        text = s["text"]
        if not (0 <= start <= end <= len(text)):
            raise ValueError("That part of the text has changed. Re-run the pass.")
        new_text = text[:start] + new + text[end:]
        delta = len(new) - (end - start)
        _set_scene_text(c, scene_id, new_text)
        for f in db.rows(c.execute("SELECT id, start, \"end\" FROM findings WHERE scene_id=? "
                                   "AND status='open' AND start IS NOT NULL", (scene_id,))):
            if f["id"] == accept_id:
                continue
            if f["end"] <= start:
                continue
            if f["start"] >= end:
                c.execute("UPDATE findings SET start=?, \"end\"=? WHERE id=?",
                          (f["start"] + delta, f["end"] + delta, f["id"]))
            else:
                c.execute("UPDATE findings SET status='stale' WHERE id=?", (f["id"],))
        if accept_id:
            c.execute("UPDATE findings SET status='accepted', suggestion=? WHERE id=?",
                      (new, accept_id))
    return new_text


def accept_finding(finding_id, replacement=None):
    with db.tx() as c:
        f = db.row(c.execute("SELECT * FROM findings WHERE id=?", (finding_id,)))
    if not f:
        raise ValueError("No such finding")
    new = replacement if replacement is not None else f["suggestion"]
    if new is None or f["scene_id"] is None or f["start"] is None:
        # Nothing to change in the text: just mark it handled.
        with db.tx() as c:
            c.execute("UPDATE findings SET status='accepted' WHERE id=?", (finding_id,))
        return {"changed": False}
    with db.tx() as c:
        text = c.execute("SELECT text FROM scenes WHERE id=?", (f["scene_id"],)).fetchone()[0]
    start, end = f["start"], f["end"]
    if f["quote"] and text[start:end] != f["quote"]:
        span = tu.anchor(text, f["quote"], near=start)
        if not span:
            with db.tx() as c:
                c.execute("UPDATE findings SET status='stale' WHERE id=?", (finding_id,))
            raise ValueError("The words this finding points at have changed. Re-run the pass.")
        start, end = span
    replace_span(f["scene_id"], start, end, new, accept_id=finding_id)
    return {"changed": True, "scene_id": f["scene_id"]}


def reject_finding(finding_id):
    with db.tx() as c:
        c.execute("UPDATE findings SET status='rejected' WHERE id=?", (finding_id,))


def edit_scene(scene_id, new_text):
    with db.tx() as c:
        _set_scene_text(c, scene_id, new_text)
        c.execute("UPDATE findings SET status='stale' WHERE scene_id=? AND status='open'", (scene_id,))


def condense(llm, scene_id, mode="truncate", target_pct=15):
    if getattr(llm, "name", "") == "demo":
        raise LLMError("Condensing needs the AI model. Switch Reader to Ollama in Settings.")
    with db.tx() as c:
        s = db.row(c.execute("SELECT text, beat, word_count FROM scenes WHERE id=?", (scene_id,)))
    target = max(20, int((s["word_count"] or 0) * (100 - int(target_pct)) / 100))
    if mode == "summary":
        system = "Summarize this scene in one short paragraph, present tense, naming characters."
        user = s["text"]
    else:
        keep = f" It carries the “{s['beat']}” story beat; keep that." if s["beat"] else ""
        system = ("You are the Abridger. Cut this scene to about the target length. Keep the "
                  "author's voice, wording and every plot point; remove repetition and padding. "
                  "Return only the shortened scene text, with paragraphs separated by blank lines.")
        user = f"Target: about {target} words.{keep}\n\nSCENE:\n{s['text']}"
    out = llm.text(system, user)
    return {"text": out, "before_words": s["word_count"], "after_words": tu.word_count(out)}


# ---------------------------------------------------------------- export and backup

def _book_chapters(book_id):
    with db.tx() as c:
        book = db.row(c.execute("SELECT * FROM books WHERE id=?", (book_id,)))
        chs = db.rows(c.execute("SELECT id, title FROM chapters WHERE book_id=? ORDER BY position",
                                (book_id,)))
        for ch in chs:
            ch["scenes"] = [r[0] for r in c.execute(
                "SELECT text FROM scenes WHERE chapter_id=? ORDER BY position", (ch["id"],))]
    return book, chs


INLINE = re.compile(r"(\*\*[^*\n]+\*\*|\*[^*\n]+\*)")


def _strip_marks(t):
    return re.sub(r"\*\*([^*\n]+)\*\*|\*([^*\n]+)\*", lambda m: m.group(1) or m.group(2), t)


def export_book(book_id, fmt, path):
    book, chs = _book_chapters(book_id)
    if fmt in ("md", "txt"):
        out = []
        for ch in chs:
            out.append(f"# {ch['title']}" if fmt == "md" else ch["title"].upper())
            out.append("")
            out.append(("\n\n***\n\n" if fmt == "md" else "\n\n* * *\n\n").join(ch["scenes"]))
            out.append("")
        text = "\n".join(out)
        if fmt == "txt":
            text = _strip_marks(text)
        path.write_text(text, encoding="utf-8")
    elif fmt == "docx":
        import docx
        from docx.enum.text import WD_ALIGN_PARAGRAPH
        d = docx.Document()
        for ci, ch in enumerate(chs):
            if ci:
                d.add_page_break()
            d.add_heading(ch["title"], level=1)
            for si, scene in enumerate(ch["scenes"]):
                if si:
                    br = d.add_paragraph("* * *")
                    br.alignment = WD_ALIGN_PARAGRAPH.CENTER
                for para in scene.split("\n\n"):
                    p = d.add_paragraph()
                    for part in INLINE.split(para):
                        if not part:
                            continue
                        if part.startswith("**") and part.endswith("**") and len(part) > 4:
                            p.add_run(part[2:-2]).bold = True
                        elif part.startswith("*") and part.endswith("*") and len(part) > 2:
                            p.add_run(part[1:-1]).italic = True
                        else:
                            p.add_run(part)
        d.save(path)
    elif fmt == "odt":
        from odf.opendocument import OpenDocumentText
        from odf.style import Style, TextProperties
        from odf.text import H, P, Span
        o = OpenDocumentText()
        ital = Style(name="Ital", family="text")
        ital.addElement(TextProperties(fontstyle="italic"))
        bold = Style(name="Bold", family="text")
        bold.addElement(TextProperties(fontweight="bold"))
        o.automaticstyles.addElement(ital)
        o.automaticstyles.addElement(bold)
        for ch in chs:
            o.text.addElement(H(outlinelevel=1, text=ch["title"]))
            for si, scene in enumerate(ch["scenes"]):
                if si:
                    o.text.addElement(P(text="* * *"))
                for para in scene.split("\n\n"):
                    p = P()
                    for part in INLINE.split(para):
                        if not part:
                            continue
                        if part.startswith("**") and part.endswith("**") and len(part) > 4:
                            p.addElement(Span(stylename=bold, text=part[2:-2]))
                        elif part.startswith("*") and part.endswith("*") and len(part) > 2:
                            p.addElement(Span(stylename=ital, text=part[1:-1]))
                        else:
                            p.addText(part)
                    o.text.addElement(p)
        o.save(str(path))
    else:
        raise ValueError("Format must be docx, odt, md or txt")
    return path


def backup_book(book_id):
    book, _ = _book_chapters(book_id)
    safe = re.sub(r"[^\w\- ]", "", book["title"]).strip() or f"book{book_id}"
    stamp = time.strftime("%Y-%m-%d_%H-%M-%S")
    path = config.data_dir() / "backups" / f"{safe} backup {stamp}.docx"
    export_book(book_id, "docx", path)
    with db.tx() as c:
        c.execute("UPDATE books SET last_backup=? WHERE id=?", (db.now(), book_id))
    return path


def findings_summary(book_id):
    with db.tx() as c:
        rows = db.rows(c.execute(
            "SELECT pass, status, COUNT(*) n FROM findings WHERE book_id=? GROUP BY pass, status",
            (book_id,)))
        runs = db.rows(c.execute(
            "SELECT pass, MAX(finished) finished, note FROM pass_runs WHERE book_id=? GROUP BY pass",
            (book_id,)))
    out = {}
    for r in rows:
        out.setdefault(r["pass"], {})[r["status"]] = r["n"]
    return {"counts": out, "runs": {r["pass"]: r for r in runs}}


def findings_list(book_id, pass_key=None, status="open", chapter_id=None, limit=500):
    sql = ("SELECT f.*, ch.position cp, s.position sp FROM findings f "
           "LEFT JOIN chapters ch ON ch.id=f.chapter_id LEFT JOIN scenes s ON s.id=f.scene_id "
           "WHERE f.book_id=? AND f.status=?")
    args = [book_id, status]
    if pass_key:
        sql += " AND f.pass=?"
        args.append(pass_key)
    if chapter_id:
        sql += " AND f.chapter_id=?"
        args.append(chapter_id)
    sql += " ORDER BY CASE f.severity WHEN 'high' THEN 0 WHEN 'medium' THEN 1 ELSE 2 END, " \
           "ch.position, s.position, f.start LIMIT ?"
    args.append(limit)
    with db.tx() as c:
        rows = db.rows(c.execute(sql, args))
    for r in rows:
        r["fact_ids"] = json.loads(r["fact_ids"] or "[]")
    return rows
