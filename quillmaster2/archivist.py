"""The Archivist: reads books scene by scene and keeps the Story Bible.

Rules it follows:
- It records what the text says. Anything it pulls out is "found", never "canon".
- Only the writer makes canon: by answering a question or editing by hand.
- Two places that disagree become a "conflict" and a question at the Reference Desk.
"""
import json
import re

from . import db

# Fields that are expected to change as the story moves (no conflict ever).
STATE_FIELDS = {"location", "holder", "status", "condition", "mood", "whereabouts"}
# Fields that may change between books, but not inside one book.
PER_BOOK_FIELDS = {"age", "title", "occupation", "relationship", "home", "goal",
                   "fear", "skill", "appearance", "build"}
MAX_GAP_QUESTIONS_PER_BOOK = 40
# A name that is only a title ("the Captain") is never merged on its own: ask.
TITLE_WORDS = {"captain", "lord", "lady", "king", "queen", "prince", "princess", "sir",
               "doctor", "dr", "father", "mother", "master", "mistress", "general",
               "commander", "sergeant", "old", "young", "uncle", "aunt"}

_NUM = {w: i for i, w in enumerate(
    "zero one two three four five six seven eight nine ten eleven twelve thirteen "
    "fourteen fifteen sixteen seventeen eighteen nineteen".split())}
_TENS = {"twenty": 20, "thirty": 30, "forty": 40, "fifty": 50, "sixty": 60,
         "seventy": 70, "eighty": 80, "ninety": 90}


def _words_to_num(s):
    parts = re.split(r"[\s-]+", s)
    if len(parts) == 1 and parts[0] in _NUM:
        return str(_NUM[parts[0]])
    if parts and parts[0] in _TENS:
        n = _TENS[parts[0]] + (_NUM.get(parts[1], 0) if len(parts) > 1 else 0)
        return str(n)
    return s


def norm_value(v):
    v = str(v).lower().strip()
    v = re.sub(r"^(the|a|an)\s+", "", v)
    v = re.sub(r"[^\w\s-]", "", v)
    v = re.sub(r"\s+", " ", v).strip()
    v = re.sub(r"\s*years?\s*old$", "", v)
    return _words_to_num(v)


def norm_name(n):
    n = re.sub(r"\s+", " ", str(n)).strip()
    return re.sub(r"^(the)\s+", "", n, flags=re.I).lower()


def norm_field(f):
    f = re.sub(r"[^a-z_ ]", "", str(f).lower()).strip().replace(" ", "_")
    return {"eye_color": "eyes", "eye_colour": "eyes", "hair_color": "hair",
            "hair_colour": "hair", "job": "occupation", "role": "occupation",
            "owner": "holder", "location_now": "location"}.get(f, f) or "note"


def _quote_ok(quote, text):
    if not quote:
        return ""
    q = re.sub(r"\s+", " ", quote).strip().strip('"“”')
    t = re.sub(r"\s+", " ", text)
    return q if q and q.lower() in t.lower() else ""


# ---------------------------------------------------------------- entities

def resolve_entity(c, series_id, name, typ):
    """Find the Bible entry for a name, or make one."""
    name = re.sub(r"\s+", " ", str(name)).strip()
    if not name:
        return None
    key = norm_name(name)
    ents = db.rows(c.execute("SELECT * FROM entities WHERE series_id=?", (series_id,)))
    for e in ents:
        names = [norm_name(e["name"])] + [norm_name(a) for a in db.jload(e["aliases"], [])]
        if key in names and (e["type"] == typ or typ == "character" or e["type"] == "character"):
            return e["id"]
    if typ == "character":
        # "Mara" matches "Mara Vell" when only one character shares that word.
        tokens = set(key.split()) - TITLE_WORDS or set(key.split())
        cands = [e for e in ents if e["type"] == "character" and (
            tokens & set(norm_name(e["name"]).split()))]
        is_title_only = set(key.split()) <= TITLE_WORDS
        auto = len(cands) == 1 and not is_title_only and (
            len(key.split()) == 1
            or set(norm_name(cands[0]["name"]).split()) <= set(key.split()))
        if auto:
            e = cands[0]
            aliases = db.jload(e["aliases"], [])
            if len(key.split()) > len(norm_name(e["name"]).split()):
                # The longer name becomes the main name.
                aliases.append(e["name"])
                c.execute("UPDATE entities SET name=?, aliases=? WHERE id=?",
                          (name, json.dumps(aliases), e["id"]))
            else:
                aliases.append(name)
                c.execute("UPDATE entities SET aliases=? WHERE id=?",
                          (json.dumps(sorted(set(aliases))), e["id"]))
            return e["id"]
        new_id = c.execute(
            "INSERT INTO entities(series_id,type,name,aliases,created) VALUES(?,?,?,?,?)",
            (series_id, typ, name, "[]", db.now())).lastrowid
        for e in cands:  # more than one possible match: ask the writer
            _ask_same_entity(c, series_id, e["id"], new_id)
        return new_id
    return c.execute(
        "INSERT INTO entities(series_id,type,name,aliases,created) VALUES(?,?,?,?,?)",
        (series_id, typ, name, "[]", db.now())).lastrowid


def known_names(c, series_id, limit=80):
    return [r[0] for r in c.execute(
        "SELECT e.name FROM entities e LEFT JOIN facts f ON f.entity_id=e.id "
        "WHERE e.series_id=? AND e.type!='note' GROUP BY e.id ORDER BY COUNT(f.id) DESC LIMIT ?",
        (series_id, limit))]


# ---------------------------------------------------------------- questions

def _add_question(c, series_id, kind, text, priority, payload, key):
    existing = db.row(c.execute("SELECT id, status FROM questions WHERE dedupe_key=?", (key,)))
    if existing:
        if existing["status"] == "open":
            c.execute("UPDATE questions SET text=?, payload=?, priority=? WHERE id=?",
                      (text, json.dumps(payload), priority, existing["id"]))
        return existing["id"]
    return c.execute(
        "INSERT INTO questions(series_id,kind,text,priority,payload,created,dedupe_key)"
        " VALUES(?,?,?,?,?,?,?)",
        (series_id, kind, text, priority, json.dumps(payload), db.now(), key)).lastrowid


def _ask_same_entity(c, series_id, a, b):
    ea = db.row(c.execute("SELECT name FROM entities WHERE id=?", (a,)))
    eb = db.row(c.execute("SELECT name FROM entities WHERE id=?", (b,)))
    lo, hi = sorted((a, b))
    _add_question(c, series_id, "same_entity",
                  f"Is “{eb['name']}” the same character as “{ea['name']}”?",
                  2, {"a": a, "b": b, "choices": ["Yes, same person", "No, different people"]},
                  f"same:{lo}:{hi}")


def where(c, scene_id):
    """'Book 1 · Ch 3 · Sc 2' for a scene."""
    r = db.row(c.execute(
        "SELECT b.position bp, b.title bt, ch.position cp, ch.title ct, s.position sp "
        "FROM scenes s JOIN chapters ch ON ch.id=s.chapter_id JOIN books b ON b.id=s.book_id "
        "WHERE s.id=?", (scene_id,)))
    if not r:
        return ""
    return f"Book {r['bp']} · Ch {r['cp']} · Sc {r['sp']}"


def _fact_where(c, fact_id):
    out = []
    for s in db.rows(c.execute(
            "SELECT kind, scene_id, quote FROM fact_sources WHERE fact_id=?", (fact_id,))):
        if s["kind"] == "text":
            out.append({"where": where(c, s["scene_id"]), "quote": s["quote"] or "",
                        "scene_id": s["scene_id"]})
        else:
            out.append({"where": "Your answer" if s["kind"] == "qa" else "Your edit",
                        "quote": s["quote"] or ""})
    return out


def _refresh_conflict_question(c, series_id, entity_id, field, book_id):
    """Group the clashing values for one entity+field into one question."""
    q = "SELECT * FROM facts WHERE entity_id=? AND field=? AND status IN ('canon','found','conflict')"
    args = [entity_id, field]
    if field in PER_BOOK_FIELDS:
        q += " AND book_id=?"
        args.append(book_id)
    facts = db.rows(c.execute(q, args))
    groups = {}
    for f in facts:
        groups.setdefault(norm_value(f["value"]), []).append(f)
    if len(groups) < 2:
        for f in facts:
            if f["status"] == "conflict":
                c.execute("UPDATE facts SET status='found' WHERE id=?", (f["id"],))
        return
    for f in facts:
        if f["status"] == "found":
            c.execute("UPDATE facts SET status='conflict' WHERE id=?", (f["id"],))
    ent = db.row(c.execute("SELECT name FROM entities WHERE id=?", (entity_id,)))
    options = []
    for g in groups.values():
        srcs = [w for f in g for w in _fact_where(c, f["id"])]
        options.append({"value": g[0]["value"], "fact_ids": [f["id"] for f in g],
                        "canon": any(f["status"] == "canon" for f in g), "sources": srcs[:4]})
    parts = [f"{o['value']}" + (" (canon)" if o["canon"] else "") +
             (f" in {o['sources'][0]['where']}" if o["sources"] else "") for o in options]
    text = (f"The text disagrees about {ent['name']}'s {field.replace('_', ' ')}: "
            + "; ".join(parts) + ". Which is right?")
    scope = f"b{book_id}" if field in PER_BOOK_FIELDS else "s"
    key = "conflict:%d:%s:%s:%s" % (entity_id, field, scope,
                                     "|".join(sorted(groups.keys())))
    _add_question(c, series_id, "conflict", text, 1,
                  {"entity_id": entity_id, "field": field, "options": options,
                   "choices": [o["value"] for o in options]}, key)


# ---------------------------------------------------------------- facts

def add_fact(c, series_id, book_id, scene_id, entity_id, field, value, quote,
             confidence=0.7, status="found", source_kind="text", question_id=None):
    field = norm_field(field)
    value = str(value).strip()
    if not value or entity_id is None:
        return None
    nv = norm_value(value)
    same = None
    for f in db.rows(c.execute(
            "SELECT * FROM facts WHERE entity_id=? AND field=? AND status!='retired'",
            (entity_id, field))):
        if norm_value(f["value"]) != nv:
            continue
        if field in PER_BOOK_FIELDS and f["book_id"] not in (book_id, None):
            continue
        if field in STATE_FIELDS and f["scene_id"] != scene_id:
            continue
        same = f
        break
    if same:
        fid = same["id"]
        if status == "canon" and same["status"] != "canon":
            c.execute("UPDATE facts SET status='canon', updated=? WHERE id=?", (db.now(), fid))
    else:
        fid = c.execute(
            "INSERT INTO facts(entity_id,field,value,status,confidence,book_id,scene_id,created,updated)"
            " VALUES(?,?,?,?,?,?,?,?,?)",
            (entity_id, field, value, status, confidence, book_id, scene_id, db.now(), db.now())
        ).lastrowid
    c.execute("INSERT INTO fact_sources(fact_id,kind,scene_id,quote,question_id,created)"
              " VALUES(?,?,?,?,?,?)",
              (fid, source_kind, scene_id, quote, question_id, db.now()))
    if field not in STATE_FIELDS and source_kind == "text":
        _refresh_conflict_question(c, series_id, entity_id, field, book_id)
    return fid


# ---------------------------------------------------------------- reading

def read_scene(llm, scene_id):
    """Read one scene with the model and file what it finds."""
    with db.tx() as c:
        s = db.row(c.execute(
            "SELECT s.*, b.series_id, b.title bt, b.position bp, ch.title ct, ch.position cp "
            "FROM scenes s JOIN books b ON b.id=s.book_id JOIN chapters ch ON ch.id=s.chapter_id "
            "WHERE s.id=?", (scene_id,)))
        names = known_names(c, s["series_id"])
    context = f"Book {s['bp']}: {s['bt']}. Chapter {s['cp']}: {s['ct']}. Scene {s['position']}."
    data = llm.extract(s["text"], names, context)  # slow part, done outside the lock

    with db.tx() as c:
        from .importer import forget_scene
        forget_scene(c, scene_id)  # re-reading replaces what this scene said before
        series_id, book_id = s["series_id"], s["book_id"]
        for n in data.get("characters_present") or []:
            eid = resolve_entity(c, series_id, n, "character")
            if eid:
                c.execute("INSERT OR IGNORE INTO appearances(scene_id,entity_id) VALUES(?,?)",
                          (scene_id, eid))
        for f in data.get("facts") or []:
            typ = f.get("type") if f.get("type") in ("character", "place", "object", "rule") \
                else "character"
            eid = resolve_entity(c, series_id, f.get("entity", ""), typ)
            quote = _quote_ok(f.get("quote", ""), s["text"])
            add_fact(c, series_id, book_id, scene_id, eid, f.get("field", "note"),
                     f.get("value", ""), quote, confidence=0.8 if quote else 0.5)
            if typ == "character" and eid:
                c.execute("INSERT OR IGNORE INTO appearances(scene_id,entity_id) VALUES(?,?)",
                          (scene_id, eid))
        order_base = (s["bp"] * 1_000_000) + (s["cp"] * 1000) + s["position"]
        for i, e in enumerate(data.get("events") or []):
            if e.get("description"):
                c.execute("INSERT INTO events(series_id,book_id,scene_id,description,when_text,story_order)"
                          " VALUES(?,?,?,?,?,?)",
                          (series_id, book_id, scene_id, e["description"], e.get("when", ""),
                           order_base * 10 + i))
        for t in data.get("threads") or []:
            name = (t.get("name") or "").strip()
            if not name:
                continue
            th = db.row(c.execute("SELECT id FROM threads WHERE series_id=? AND lower(name)=?",
                                  (series_id, name.lower())))
            tid = th["id"] if th else c.execute(
                "INSERT INTO threads(series_id,name) VALUES(?,?)", (series_id, name)).lastrowid
            role = t.get("role") if t.get("role") in (
                "opens", "rises", "turns", "resolves", "mentions") else "mentions"
            c.execute("INSERT INTO thread_beats(thread_id,scene_id,book_id,chapter_id,role,note)"
                      " VALUES(?,?,?,?,?,?)",
                      (tid, scene_id, book_id, s["chapter_id"], role, t.get("note", "")))
        gaps = c.execute("SELECT COUNT(*) FROM questions WHERE series_id=? AND kind='gap' "
                         "AND status='open'", (series_id,)).fetchone()[0]
        for u in (data.get("unclear") or [])[:2]:
            if gaps >= MAX_GAP_QUESTIONS_PER_BOOK or not u.get("question"):
                break
            eid = resolve_entity(c, series_id, u["entity"], "character") if u.get("entity") else None
            field = norm_field(u.get("field") or "note")
            # Already known? Then there's nothing to ask.
            if eid and field != "note" and c.execute(
                    "SELECT 1 FROM facts WHERE entity_id=? AND field=? AND status!='retired'",
                    (eid, field)).fetchone():
                continue
            key = (f"gap:{eid}:{field}" if eid and field != "note"
                   else f"gap:{norm_value(u['question'])[:60]}")
            _add_question(c, series_id, "gap", u["question"], 3,
                          {"entity_id": eid, "field": field,
                           "scene_id": scene_id, "where": where(c, scene_id)}, key)
            gaps += 1
        c.execute("UPDATE scenes SET summary=?, pov=?, location=?, time_text=?, "
                  "read_fingerprint=fingerprint, read_at=? WHERE id=?",
                  (data.get("summary", ""), data.get("pov", ""), data.get("location", ""),
                   data.get("time", ""), db.now(), scene_id))


def scenes_to_read(book_id):
    with db.tx() as c:
        return [r[0] for r in c.execute(
            "SELECT s.id FROM scenes s JOIN chapters ch ON ch.id=s.chapter_id "
            "WHERE s.book_id=? AND (s.read_fingerprint IS NULL OR s.read_fingerprint!=s.fingerprint) "
            "ORDER BY ch.position, s.position", (book_id,))]


def full_read(llm, book_id, progress=lambda done, total, msg: None, stop=lambda: False):
    """Read every new or changed scene of one book, then roll up summaries.
    Returns True if finished, False if stopped part-way (safe to resume)."""
    todo = scenes_to_read(book_id)
    with db.tx() as c:
        c.execute("UPDATE books SET status='reading' WHERE id=?", (book_id,))
        book = db.row(c.execute("SELECT * FROM books WHERE id=?", (book_id,)))
    changed_chapters = set()
    for i, sid in enumerate(todo):
        if stop():
            progress(i, len(todo), "Paused")
            return False
        with db.tx() as c:
            label = where(c, sid)
            changed_chapters.add(c.execute("SELECT chapter_id FROM scenes WHERE id=?",
                                           (sid,)).fetchone()[0])
        progress(i, len(todo), f"Reading {label}")
        read_scene(llm, sid)
    progress(len(todo), len(todo), "Writing summaries")
    rollup(llm, book_id, changed_chapters)
    with db.tx() as c:
        thread_questions(c, book["series_id"])
        c.execute("UPDATE books SET status='read' WHERE id=?", (book_id,))
    progress(len(todo), len(todo), "Done")
    return True


def rollup(llm, book_id, changed_chapters=None):
    with db.tx() as c:
        chapters = db.rows(c.execute(
            "SELECT * FROM chapters WHERE book_id=? ORDER BY position", (book_id,)))
        book = db.row(c.execute("SELECT * FROM books WHERE id=?", (book_id,)))
    for ch in chapters:
        if ch["summary"] and changed_chapters is not None and ch["id"] not in changed_chapters:
            continue
        with db.tx() as c:
            sums = [r[0] for r in c.execute(
                "SELECT summary FROM scenes WHERE chapter_id=? ORDER BY position", (ch["id"],))
                if r[0]]
        text = llm.summarize(sums, f"chapter ('{ch['title']}')") if sums else ""
        with db.tx() as c:
            c.execute("UPDATE chapters SET summary=? WHERE id=?", (text, ch["id"]))
            ch["summary"] = text
    book_sum = llm.summarize([f"{ch['title']}: {ch['summary']}" for ch in chapters
                              if ch["summary"]], f"book ('{book['title']}')")
    save_document(book["series_id"], book_id, "book_summary", book_sum)


def thread_questions(c, series_id):
    threads = db.rows(c.execute("SELECT * FROM threads WHERE series_id=? AND status='open'",
                                (series_id,)))
    for t in threads:
        beats = db.rows(c.execute(
            "SELECT tb.*, b.position bp, ch.position cp, s.position sp FROM thread_beats tb "
            "JOIN scenes s ON s.id=tb.scene_id JOIN chapters ch ON ch.id=s.chapter_id "
            "JOIN books b ON b.id=s.book_id WHERE tb.thread_id=? "
            "ORDER BY b.position, ch.position, s.position", (t["id"],)))
        # One passing mention isn't a storyline yet.
        if not beats or (len(beats) < 2 and beats[0]["role"] != "opens"):
            continue
        if any(b["role"] == "resolves" for b in beats):
            c.execute("UPDATE threads SET status='resolved' WHERE id=?", (t["id"],))
            continue
        first, last = beats[0], beats[-1]
        text = (f"“{t['name']}” starts in {where(c, first['scene_id'])} and last comes up "
                f"in {where(c, last['scene_id'])}, but it is never resolved. Is it still coming?")
        _add_question(c, series_id, "thread", text, 2,
                      {"thread_id": t["id"],
                       "choices": ["Still coming (planned for a later book)",
                                   "Resolved already (the Archivist missed it)",
                                   "Dropped (I'll cut or fix it)"]},
                      f"thread:{t['id']}")


# ---------------------------------------------------------------- answers

def answer_preview(qid, action, choice=None, text=None):
    """What will change if the writer answers this way. Nothing is saved."""
    return _answer(qid, action, choice, text, apply=False)


def answer_apply(qid, action, choice=None, text=None):
    return _answer(qid, action, choice, text, apply=True)


def _answer(qid, action, choice, text, apply):
    with db.tx() as c:
        q = db.row(c.execute("SELECT * FROM questions WHERE id=?", (qid,)))
        if not q:
            raise ValueError("No such question")
        p = db.jload(q["payload"], {})
        changes = []
        series_id = q["series_id"]

        if action in ("skip", "intentional"):
            changes.append("Mark as skipped for now" if action == "skip"
                           else "Mark as on purpose. Nothing in the Bible changes.")
            if apply:
                if q["kind"] == "conflict" and action == "intentional":
                    for o in p.get("options", []):
                        for fid in o["fact_ids"]:
                            c.execute("UPDATE facts SET status='found' WHERE id=? AND status='conflict'",
                                      (fid,))
                c.execute("UPDATE questions SET status=?, answered_at=? WHERE id=?",
                          ("skipped" if action == "skip" else "intentional", db.now(), qid))
            return {"changes": changes, "applied": apply}

        answer_text = text.strip() if text else None
        if choice is not None and p.get("choices"):
            answer_text = answer_text or p["choices"][int(choice)]

        if q["kind"] == "conflict":
            ent = db.row(c.execute("SELECT name FROM entities WHERE id=?", (p["entity_id"],)))
            chosen = None
            if choice is not None:
                chosen = p["options"][int(choice)]
            elif answer_text:
                for o in p["options"]:
                    if norm_value(o["value"]) == norm_value(answer_text):
                        chosen = o
            value = chosen["value"] if chosen else answer_text
            changes.append(f"Canon: {ent['name']} · {p['field']} = {value}")
            for o in p["options"]:
                if o is chosen:
                    continue
                for src in o["sources"]:
                    if src.get("scene_id"):
                        changes.append(f"Fix needed in {src['where']}: “{src['quote'] or o['value']}”")
            if apply:
                for o in p["options"]:
                    for fid in o["fact_ids"]:
                        c.execute("UPDATE facts SET status=?, updated=? WHERE id=?",
                                  ("canon" if o is chosen else "retired", db.now(), fid))
                    if o is not chosen:
                        for src in o["sources"]:
                            if src.get("scene_id"):
                                c.execute("INSERT INTO fixes(series_id,scene_id,quote,note,question_id,created)"
                                          " VALUES(?,?,?,?,?,?)",
                                          (series_id, src["scene_id"], src["quote"],
                                           f"Change to match canon: {p['field']} = {value}",
                                           qid, db.now()))
                if chosen:
                    for fid in chosen["fact_ids"]:
                        c.execute("INSERT INTO fact_sources(fact_id,kind,quote,question_id,created)"
                                  " VALUES(?,?,?,?,?)", (fid, "qa", answer_text, qid, db.now()))
                else:
                    r = db.row(c.execute("SELECT book_id FROM facts WHERE id=?",
                                         (p["options"][0]["fact_ids"][0],)))
                    book_id = r["book_id"] if r else None
                    add_fact(c, series_id, book_id, None, p["entity_id"], p["field"], value,
                             answer_text, 1.0, "canon", "qa", qid)

        elif q["kind"] == "same_entity":
            a = db.row(c.execute("SELECT * FROM entities WHERE id=?", (p["a"],)))
            b = db.row(c.execute("SELECT * FROM entities WHERE id=?", (p["b"],)))
            yes = (choice is not None and int(choice) == 0) or (
                answer_text and answer_text.lower().startswith("y"))
            if not (a and b):
                changes.append("One of these entries no longer exists.")
            elif yes:
                changes.append(f"Merge “{b['name']}” into “{a['name']}” "
                               "(facts, appearances and names move over)")
                if apply:
                    merge_entities(c, a["id"], b["id"])
            else:
                changes.append(f"Keep “{a['name']}” and “{b['name']}” separate")

        elif q["kind"] == "thread":
            t = db.row(c.execute("SELECT * FROM threads WHERE id=?", (p["thread_id"],)))
            idx = int(choice) if choice is not None else None
            status = {0: "planned", 1: "resolved", 2: "dropped"}.get(idx, "open")
            changes.append(f"“{t['name']}” is now marked {status}")
            if answer_text and idx is None:
                changes.append(f"Note saved: {answer_text}")
            if apply:
                c.execute("UPDATE threads SET status=?, note=? WHERE id=?",
                          (status, answer_text, t["id"]))

        else:  # gap
            if not answer_text:
                raise ValueError("Type an answer first.")
            eid = p.get("entity_id")
            if not eid:
                eid = resolve_entity(c, series_id, "Author notes", "note")
            ent = db.row(c.execute("SELECT name FROM entities WHERE id=?", (eid,)))
            field = p.get("field") or "note"
            changes.append(f"Canon: {ent['name']} · {field} = {answer_text}")
            if apply:
                add_fact(c, series_id, None, None, eid, field, answer_text,
                         q["text"], 1.0, "canon", "qa", qid)

        if apply:
            c.execute("UPDATE questions SET status='answered', answer=?, answered_at=? WHERE id=?",
                      (answer_text, db.now(), qid))
        return {"changes": changes, "applied": apply}


def merge_entities(c, keep_id, drop_id):
    keep = db.row(c.execute("SELECT * FROM entities WHERE id=?", (keep_id,)))
    drop = db.row(c.execute("SELECT * FROM entities WHERE id=?", (drop_id,)))
    aliases = set(db.jload(keep["aliases"], [])) | set(db.jload(drop["aliases"], [])) | {drop["name"]}
    c.execute("UPDATE entities SET aliases=? WHERE id=?", (json.dumps(sorted(aliases)), keep_id))
    c.execute("UPDATE facts SET entity_id=? WHERE entity_id=?", (keep_id, drop_id))
    c.execute("INSERT OR IGNORE INTO appearances(scene_id,entity_id) "
              "SELECT scene_id, ? FROM appearances WHERE entity_id=?", (keep_id, drop_id))
    c.execute("DELETE FROM appearances WHERE entity_id=?", (drop_id,))
    c.execute("DELETE FROM entities WHERE id=?", (drop_id,))
    for field in {r[0] for r in c.execute("SELECT field FROM facts WHERE entity_id=?", (keep_id,))}:
        if field in STATE_FIELDS:
            continue
        for bid in {r[0] for r in c.execute(
                "SELECT book_id FROM facts WHERE entity_id=? AND field=?", (keep_id, field))}:
            _refresh_conflict_question(c, keep["series_id"], keep_id, field, bid)


def hand_edit(fact_id, value=None, status=None):
    with db.tx() as c:
        f = db.row(c.execute("SELECT * FROM facts WHERE id=?", (fact_id,)))
        if not f:
            raise ValueError("No such fact")
        new_value = value if value is not None else f["value"]
        new_status = status or "canon"
        c.execute("UPDATE facts SET value=?, status=?, updated=? WHERE id=?",
                  (new_value, new_status, db.now(), fact_id))
        c.execute("INSERT INTO fact_sources(fact_id,kind,quote,created) VALUES(?,?,?,?)",
                  (fact_id, "hand", f"{f['value']} → {new_value}", db.now()))
        # List every text source that now disagrees.
        disagree = []
        if new_status == "canon":
            for o in db.rows(c.execute(
                    "SELECT * FROM facts WHERE entity_id=? AND field=? AND id!=? AND status!='retired'",
                    (f["entity_id"], f["field"], fact_id))):
                if norm_value(o["value"]) != norm_value(new_value) and f["field"] not in STATE_FIELDS:
                    if f["field"] in PER_BOOK_FIELDS and o["book_id"] != f["book_id"]:
                        continue
                    disagree += _fact_where(c, o["id"])
        return {"disagree": disagree}


def add_hand_fact(entity_id, field, value, status="canon", book_id=None):
    with db.tx() as c:
        e = db.row(c.execute("SELECT series_id FROM entities WHERE id=?", (entity_id,)))
        fid = add_fact(c, e["series_id"], book_id, None, entity_id, field, value,
                       "Added by hand", 1.0, status, "hand")
        return fid


# ---------------------------------------------------------------- documents

def save_document(series_id, book_id, kind, content):
    with db.tx() as c:
        c.execute("DELETE FROM documents WHERE book_id IS ? AND series_id=? AND kind=?",
                  (book_id, series_id, kind))
        c.execute("INSERT INTO documents(series_id,book_id,kind,content,built_at) VALUES(?,?,?,?,?)",
                  (series_id, book_id, kind, content, db.now()))


def get_saved(series_id, book_id, kind):
    with db.tx() as c:
        r = db.row(c.execute("SELECT content, built_at FROM documents WHERE series_id=? AND "
                             "book_id IS ? AND kind=?", (series_id, book_id, kind)))
    return r


def outline(book_id):
    with db.tx() as c:
        out = []
        for ch in db.rows(c.execute("SELECT * FROM chapters WHERE book_id=? ORDER BY position",
                                    (book_id,))):
            scenes = db.rows(c.execute(
                "SELECT id, position, word_count, summary, pov, location, time_text, "
                "read_fingerprint=fingerprint AS is_read FROM scenes WHERE chapter_id=? ORDER BY position",
                (ch["id"],)))
            out.append({"chapter": ch["position"], "title": ch["title"],
                        "word_count": ch["word_count"], "summary": ch["summary"],
                        "scenes": scenes})
        return out


def characters(series_id):
    with db.tx() as c:
        out = []
        for e in db.rows(c.execute(
                "SELECT * FROM entities WHERE series_id=? AND type='character' ORDER BY name",
                (series_id,))):
            card = entity_card(c, e)
            apps = db.rows(c.execute(
                "SELECT a.scene_id FROM appearances a JOIN scenes s ON s.id=a.scene_id "
                "JOIN chapters ch ON ch.id=s.chapter_id JOIN books b ON b.id=s.book_id "
                "WHERE a.entity_id=? ORDER BY b.position, ch.position, s.position", (e["id"],)))
            card["appearances"] = len(apps)
            card["first"] = where(c, apps[0]["scene_id"]) if apps else ""
            card["last"] = where(c, apps[-1]["scene_id"]) if apps else ""
            out.append(card)
        out.sort(key=lambda x: -x["appearances"])
        return out


def storylines(series_id):
    with db.tx() as c:
        out = []
        for t in db.rows(c.execute("SELECT * FROM threads WHERE series_id=? ORDER BY name",
                                   (series_id,))):
            beats = db.rows(c.execute(
                "SELECT tb.role, tb.note, tb.scene_id, s.summary FROM thread_beats tb "
                "JOIN scenes s ON s.id=tb.scene_id JOIN chapters ch ON ch.id=s.chapter_id "
                "JOIN books b ON b.id=s.book_id WHERE tb.thread_id=? "
                "ORDER BY b.position, ch.position, s.position", (t["id"],)))
            for b in beats:
                b["where"] = where(c, b["scene_id"])
            out.append({"id": t["id"], "name": t["name"], "status": t["status"],
                        "note": t["note"], "beats": beats})
        return out


def subplot_matrix(book_id):
    """Threads down the side, chapters across. Each cell: the roles in that chapter."""
    with db.tx() as c:
        book = db.row(c.execute("SELECT * FROM books WHERE id=?", (book_id,)))
        chapters = db.rows(c.execute("SELECT id, position, title FROM chapters WHERE book_id=? "
                                     "ORDER BY position", (book_id,)))
        threads = db.rows(c.execute("SELECT * FROM threads WHERE series_id=? ORDER BY name",
                                    (book["series_id"],)))
        rows_out = []
        for t in threads:
            cells = {}
            for r in c.execute("SELECT chapter_id, role FROM thread_beats WHERE thread_id=? "
                               "AND book_id=?", (t["id"], book_id)):
                cells.setdefault(r[0], []).append(r[1])
            if not cells:
                continue
            row_cells = [sorted(set(cells.get(ch["id"], []))) for ch in chapters]
            # A run of 6+ empty chapters after the thread started = quiet stretch.
            started, run, longest = False, 0, 0
            for cell in row_cells:
                if cell:
                    started, run = True, 0
                elif started:
                    run += 1
                    longest = max(longest, run)
            rows_out.append({"thread": t["name"], "status": t["status"], "cells": row_cells,
                             "longest_quiet": longest})
        return {"chapters": [{"position": ch["position"], "title": ch["title"]} for ch in chapters],
                "rows": rows_out}


def synopsis(llm, book_id):
    with db.tx() as c:
        book = db.row(c.execute("SELECT * FROM books WHERE id=?", (book_id,)))
        sums = [r[0] for r in c.execute("SELECT summary FROM chapters WHERE book_id=? "
                                        "ORDER BY position", (book_id,)) if r[0]]
    text = llm.synopsis(book["title"], sums)
    save_document(book["series_id"], book_id, "synopsis", text)
    return text


# ---------------------------------------------------------------- bible views

def entity_card(c, e):
    facts = db.rows(c.execute(
        "SELECT * FROM facts WHERE entity_id=? ORDER BY field, "
        "CASE status WHEN 'conflict' THEN 0 WHEN 'canon' THEN 1 WHEN 'planned' THEN 2 "
        "WHEN 'found' THEN 3 ELSE 4 END, id", (e["id"],)))
    for f in facts:
        f["sources"] = _fact_where(c, f["id"])
        f["book"] = None
        f["per_book"] = f["field"] in PER_BOOK_FIELDS
        if f["book_id"]:
            b = db.row(c.execute("SELECT position FROM books WHERE id=?", (f["book_id"],)))
            f["book"] = b["position"] if b else None
    statuses = {f["status"] for f in facts}
    return {"id": e["id"], "type": e["type"], "name": e["name"],
            "aliases": db.jload(e["aliases"], []), "facts": facts,
            "has_conflict": "conflict" in statuses,
            "canon_count": sum(f["status"] == "canon" for f in facts)}


def bible(series_id, typ=None, status=None, q=None):
    with db.tx() as c:
        sql = "SELECT * FROM entities WHERE series_id=?"
        args = [series_id]
        if typ:
            sql += " AND type=?"
            args.append(typ)
        cards = [entity_card(c, e) for e in db.rows(c.execute(sql + " ORDER BY name", args))]
    if status:
        cards = [x for x in cards if any(f["status"] == status for f in x["facts"])]
    if q:
        ql = q.lower()
        cards = [x for x in cards if ql in x["name"].lower()
                 or any(ql in a.lower() for a in x["aliases"])
                 or any(ql in f["value"].lower() or ql in f["field"] for f in x["facts"])]
    cards.sort(key=lambda x: (not x["has_conflict"], x["name"].lower()))
    return cards


def ask(llm, series_id, question):
    words = [w for w in re.findall(r"[A-Za-z][A-Za-z'-]{2,}", question)
             if w.lower() not in {"what", "who", "where", "when", "does", "did", "the", "and",
                                  "know", "about", "how", "why", "with", "from", "have", "has"}]
    notes = []
    with db.tx() as c:
        for e in db.rows(c.execute("SELECT * FROM entities WHERE series_id=?", (series_id,))):
            hay = (e["name"] + " " + e["aliases"]).lower()
            if any(w.lower() in hay for w in words):
                for f in db.rows(c.execute("SELECT * FROM facts WHERE entity_id=? AND "
                                           "status!='retired'", (e["id"],))):
                    src = _fact_where(c, f["id"])
                    notes.append(f"[F{f['id']}] {e['name']} · {f['field']}: {f['value']} "
                                 f"({f['status']}{', ' + src[0]['where'] if src else ''})")
        for w in words[:5]:
            for s in db.rows(c.execute(
                    "SELECT id, summary FROM scenes s WHERE book_id IN (SELECT id FROM books "
                    "WHERE series_id=?) AND summary LIKE ? LIMIT 5", (series_id, f"%{w}%"))):
                notes.append(f"[{where(c, s['id'])}] {s['summary']}")
    notes = list(dict.fromkeys(notes))[:60]
    return {"answer": llm.ask(question, "\n".join(notes)), "notes": notes}


def export_markdown(series_id):
    with db.tx() as c:
        series = db.row(c.execute("SELECT * FROM series WHERE id=?", (series_id,)))
    cards = bible(series_id)
    out = [f"# {series['name']} — Story Bible", ""]
    for typ, label in (("character", "Characters"), ("place", "Places"), ("object", "Objects"),
                       ("rule", "World rules"), ("note", "Author notes")):
        group = [x for x in cards if x["type"] == typ]
        if not group:
            continue
        out += [f"## {label}", ""]
        for x in group:
            out.append(f"### {x['name']}")
            if x["aliases"]:
                out.append(f"*Also called:* {', '.join(x['aliases'])}")
            for f in x["facts"]:
                if f["status"] == "retired":
                    continue
                src = f["sources"][0]["where"] if f["sources"] else ""
                book = f" (as of book {f['book']})" if f["book"] and f["field"] in PER_BOOK_FIELDS else ""
                out.append(f"- **{f['field']}**: {f['value']}{book} — {f['status']}"
                           + (f", {src}" if src else ""))
            out.append("")
    threads = storylines(series_id)
    if threads:
        out += ["## Storylines", ""]
        for t in threads:
            out.append(f"- **{t['name']}** — {t['status']}, {len(t['beats'])} scenes")
    return "\n".join(out)


def export_docx(series_id, path):
    import docx
    d = docx.Document()
    for line in export_markdown(series_id).splitlines():
        if line.startswith("### "):
            d.add_heading(line[4:], level=3)
        elif line.startswith("## "):
            d.add_heading(line[3:], level=2)
        elif line.startswith("# "):
            d.add_heading(line[2:], level=1)
        elif line.startswith("- "):
            d.add_paragraph(line[2:].replace("**", ""), style="List Bullet")
        elif line.strip():
            d.add_paragraph(line.replace("*", ""))
    d.save(path)
    return path
