"""Reads a manuscript and splits it into chapters and scenes.

Formats: .docx, .odt, .md, .txt
Chapters: Word/LibreOffice heading styles, Markdown '#' headings, or lines
like "Chapter 7", "CHAPTER SEVEN", "Prologue", "Epilogue", "Part Two".
Scene breaks: a line that is only ***, * * *, #, ~~~, --- or similar.
"""
import hashlib
import re
from pathlib import Path

from . import db

CHAPTER_RE = re.compile(
    r"^\s*(chapter|ch\.|prologue|epilogue|interlude|part|book)\b[\s\w:.\-—–']{0,60}$",
    re.IGNORECASE,
)
SCENE_BREAK_RE = re.compile(r"^\s*([*#~\-_=•·]\s*){1,7}$|^\s*§\s*$")


def _word_count(text):
    return len(re.findall(r"\b[\w'’-]+\b", text))


def fingerprint(text):
    norm = re.sub(r"\s+", " ", text).strip().lower()
    return hashlib.sha256(norm.encode("utf-8")).hexdigest()[:24]


# ---------- readers: each returns a list of (kind, text) with kind in
# {"heading", "para", "break"} ----------

def _read_docx(path):
    import docx  # python-docx
    out = []
    for p in docx.Document(path).paragraphs:
        text = p.text.strip()
        style = (p.style.name or "").lower() if p.style is not None else ""
        if not text:
            continue
        if style.startswith("heading") or style == "title":
            out.append(("heading", text))
        else:
            out.append(("para", text))
    return out


def _read_odt(path):
    from odf import teletype
    from odf.opendocument import load
    from odf.namespaces import TEXTNS
    doc = load(str(path))
    H_Q, P_Q = (TEXTNS, "h"), (TEXTNS, "p")
    out = []
    body = doc.text  # walk in document order

    def walk(node):
        for child in node.childNodes:
            q = getattr(child, "qname", None)
            if q == H_Q:
                t = teletype.extractText(child).strip()
                if t:
                    out.append(("heading", t))
            elif q == P_Q:
                t = teletype.extractText(child).strip()
                if t:
                    out.append(("para", t))
            elif getattr(child, "childNodes", None):
                walk(child)
    walk(body)
    return out


def _read_plain(text, markdown=False):
    out = []
    para = []

    def flush():
        if para:
            out.append(("para", " ".join(para).strip()))
            para.clear()

    for raw in text.splitlines():
        line = raw.rstrip()
        stripped = line.strip()
        if not stripped:
            flush()
            continue
        if markdown and re.match(r"^#{1,3}\s+\S", stripped):
            flush()
            out.append(("heading", stripped.lstrip("#").strip()))
            continue
        if SCENE_BREAK_RE.match(stripped):
            flush()
            out.append(("break", ""))
            continue
        para.append(stripped)
    flush()
    return out


def read_blocks(path):
    path = Path(path)
    ext = path.suffix.lower()
    if ext == ".docx":
        blocks = _read_docx(path)
    elif ext == ".odt":
        blocks = _read_odt(path)
    elif ext in (".md", ".markdown"):
        blocks = _read_plain(path.read_text(encoding="utf-8", errors="replace"), markdown=True)
    elif ext == ".txt":
        blocks = _read_plain(path.read_text(encoding="utf-8", errors="replace"))
    else:
        raise ValueError(f"Unsupported file type: {ext}. Use .docx, .odt, .md or .txt")
    # Promote chapter-like paragraphs to headings, and lone break symbols to breaks.
    fixed = []
    for kind, text in blocks:
        if kind == "para" and SCENE_BREAK_RE.match(text):
            fixed.append(("break", ""))
        elif kind == "para" and len(text) < 70 and CHAPTER_RE.match(text):
            fixed.append(("heading", text))
        else:
            fixed.append((kind, text))
    return fixed


def split(blocks, max_scene_words=2500):
    """Turn blocks into [{title, scenes:[text, ...]}]."""
    chapters = []
    current = None
    scene = []

    def end_scene():
        if current is not None and scene:
            current["scenes"].append("\n\n".join(scene))
        scene.clear()

    for kind, text in blocks:
        if kind == "heading":
            end_scene()
            current = {"title": text, "scenes": []}
            chapters.append(current)
        elif kind == "break":
            end_scene()
        else:
            if current is None:
                current = {"title": "Opening", "scenes": []}
                chapters.append(current)
            scene.append(text)
    end_scene()
    chapters = [c for c in chapters if c["scenes"]]

    # Cut very long scenes into parts at paragraph boundaries.
    for c in chapters:
        parts = []
        for s in c["scenes"]:
            if _word_count(s) <= max_scene_words:
                parts.append(s)
                continue
            buf, n = [], 0
            for para in s.split("\n\n"):
                w = _word_count(para)
                if buf and n + w > max_scene_words:
                    parts.append("\n\n".join(buf))
                    buf, n = [], 0
                buf.append(para)
                n += w
            if buf:
                parts.append("\n\n".join(buf))
        c["scenes"] = parts
    return chapters


def import_book(series_id, title, position, path, max_scene_words=2500, book_id=None,
                display_name=None):
    """Import a file. If book_id is given, re-import over that book and keep
    the reading work for every scene whose text did not change."""
    chapters = split(read_blocks(path), max_scene_words)
    if not chapters:
        raise ValueError("No text found in that file.")
    total_words = sum(_word_count(s) for c in chapters for s in c["scenes"])

    with db.tx() as c:
        if book_id is None:
            cur = c.execute(
                "INSERT INTO books(series_id,title,position,filename,word_count,status,created)"
                " VALUES(?,?,?,?,?,'imported',?)",
                (series_id, title, position, display_name or Path(path).name, total_words, db.now()))
            book_id = cur.lastrowid
            old = {}
        else:
            c.execute("UPDATE books SET filename=?, word_count=? WHERE id=?",
                      (display_name or Path(path).name, total_words, book_id))
            old = {}
            for r in c.execute("SELECT id, fingerprint FROM scenes WHERE book_id=?", (book_id,)):
                old.setdefault(r["fingerprint"], []).append(r["id"])

        kept = set()
        new_chapter_ids = []
        for ci, ch in enumerate(chapters, 1):
            words = sum(_word_count(s) for s in ch["scenes"])
            cid = c.execute(
                "INSERT INTO chapters(book_id,position,title,word_count) VALUES(?,?,?,?)",
                (book_id, ci, ch["title"], words)).lastrowid
            new_chapter_ids.append(cid)
            for si, text in enumerate(ch["scenes"], 1):
                fp = fingerprint(text)
                reuse = old.get(fp)
                if reuse:
                    sid = reuse.pop(0)
                    kept.add(sid)
                    c.execute("UPDATE scenes SET chapter_id=?, position=? WHERE id=?",
                              (cid, si, sid))
                else:
                    c.execute(
                        "INSERT INTO scenes(book_id,chapter_id,position,text,word_count,fingerprint)"
                        " VALUES(?,?,?,?,?,?)",
                        (book_id, cid, si, text, _word_count(text), fp))

        # Drop scenes that no longer exist, and the reading work that came from them.
        for ids in old.values():
            for sid in ids:
                if sid not in kept:
                    forget_scene(c, sid)
                    c.execute("DELETE FROM scenes WHERE id=?", (sid,))
        placeholders = ",".join("?" * len(new_chapter_ids))
        c.execute(f"DELETE FROM chapters WHERE book_id=? AND id NOT IN ({placeholders})",
                  (book_id, *new_chapter_ids))
    return book_id


def forget_scene(c, scene_id):
    """Remove everything the Archivist learned from one scene's text.
    Canon facts and facts with other sources stay."""
    fact_ids = [r[0] for r in c.execute(
        "SELECT fact_id FROM fact_sources WHERE scene_id=? AND kind='text'", (scene_id,))]
    c.execute("DELETE FROM fact_sources WHERE scene_id=? AND kind='text'", (scene_id,))
    for fid in set(fact_ids):
        left = c.execute("SELECT COUNT(*) FROM fact_sources WHERE fact_id=?", (fid,)).fetchone()[0]
        status = c.execute("SELECT status FROM facts WHERE id=?", (fid,)).fetchone()
        if left == 0 and status and status[0] in ("found", "conflict"):
            c.execute("DELETE FROM facts WHERE id=?", (fid,))
    c.execute("DELETE FROM events WHERE scene_id=?", (scene_id,))
    c.execute("DELETE FROM thread_beats WHERE scene_id=?", (scene_id,))
    c.execute("DELETE FROM appearances WHERE scene_id=?", (scene_id,))
    c.execute("UPDATE scenes SET read_fingerprint=NULL, summary=NULL WHERE id=?", (scene_id,))
