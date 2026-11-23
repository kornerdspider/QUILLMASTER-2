"""End-to-end test with the demo reader: import two books of a trilogy,
read them in order, and check the Bible, conflicts, questions and answers."""
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.environ["QM2_DATA"] = tempfile.mkdtemp(prefix="qm2test_")

from quillmaster2 import archivist, db, importer, llm  # noqa: E402

SAMPLES = ROOT / "samples"


def setup_module(_):
    db.set_path(Path(os.environ["QM2_DATA"]) / "test.db")
    db.init()


def _series():
    with db.tx() as c:
        return c.execute("INSERT INTO series(name,created) VALUES('Ember Trilogy',0)").lastrowid


def test_split_markdown():
    ch = importer.split(importer.read_blocks(SAMPLES / "Book1_The_Ember_Road.md"))
    assert [c["title"] for c in ch] == ["Chapter 1: The Ferry", "Chapter 2: The Archive",
                                        "Chapter 3: The Climb"]
    assert [len(c["scenes"]) for c in ch] == [2, 2, 1]


def test_split_txt_and_breaks():
    ch = importer.split(importer.read_blocks(SAMPLES / "Book2_The_Ash_Sea.txt"))
    assert [c["title"] for c in ch] == ["Chapter One", "Chapter Two"]
    assert len(ch[0]["scenes"]) == 2


def test_docx_and_odt_roundtrip(tmp_path):
    import docx
    d = docx.Document()
    d.add_heading("Chapter 1", level=1)
    d.add_paragraph("First scene text here.")
    d.add_paragraph("***")
    d.add_paragraph("Second scene text here.")
    p = tmp_path / "b.docx"
    d.save(p)
    ch = importer.split(importer.read_blocks(p))
    assert len(ch) == 1 and len(ch[0]["scenes"]) == 2

    from odf.opendocument import OpenDocumentText
    from odf.text import H, P
    o = OpenDocumentText()
    o.text.addElement(H(outlinelevel=1, text="Chapter 1"))
    o.text.addElement(P(text="Scene one."))
    o.text.addElement(H(outlinelevel=1, text="Chapter 2"))
    o.text.addElement(P(text="Scene two."))
    po = tmp_path / "b.odt"
    o.save(str(po))
    ch = importer.split(importer.read_blocks(po))
    assert [c["title"] for c in ch] == ["Chapter 1", "Chapter 2"]


def test_long_scene_is_cut():
    para = " ".join(["word"] * 400)
    blocks = [("heading", "Chapter 1")] + [("para", para)] * 10
    ch = importer.split(blocks, max_scene_words=1000)
    assert len(ch[0]["scenes"]) == 5


def test_full_series_read_and_answers():
    sid = _series()
    reader = llm.DemoLLM()
    b1 = importer.import_book(sid, "The Ember Road", 1, SAMPLES / "Book1_The_Ember_Road.md")
    b2 = importer.import_book(sid, "The Ash Sea", 2, SAMPLES / "Book2_The_Ash_Sea.txt")
    assert archivist.full_read(reader, b1)
    assert archivist.full_read(reader, b2)

    cards = {c["name"]: c for c in archivist.bible(sid)}
    assert "Mara" in cards and "Tavin" in cards

    with db.tx() as c:
        qs = db.rows(c.execute("SELECT * FROM questions WHERE series_id=? ORDER BY priority",
                               (sid,)))
    kinds = [q["kind"] for q in qs]
    texts = " | ".join(q["text"] for q in qs)
    # Tavin is 19 and 17 inside book 1 -> conflict. 20 in book 2 is fine (per-book field).
    assert "Tavin's age" in texts and "20" not in [o for q in qs if q["kind"] == "conflict"
                                                  for o in db.jload(q["payload"])["choices"]]
    # Mara's eyes: grey in book 1, green in book 2 -> conflict across books.
    assert "Mara's eyes" in texts
    # The locked box is never resolved -> thread question. The silver key is resolved.
    assert "thread" in kinds and "locked box" in texts and "silver key”" not in texts

    age_q = next(q for q in qs if "Tavin's age" in q["text"])
    choices = db.jload(age_q["payload"])["choices"]
    i19 = choices.index("19")
    prev = archivist.answer_preview(age_q["id"], "answer", choice=i19)
    assert any("Canon: Tavin" in x for x in prev["changes"])
    assert any("Fix needed in Book 1 · Ch 3" in x for x in prev["changes"])
    with db.tx() as c:  # preview saved nothing
        assert c.execute("SELECT COUNT(*) FROM facts WHERE status='canon'").fetchone()[0] == 0
    archivist.answer_apply(age_q["id"], "answer", choice=i19)
    card = {c["name"]: c for c in archivist.bible(sid)}["Tavin"]
    ages = {f["value"]: f["status"] for f in card["facts"] if f["field"] == "age"}
    assert ages["19"] == "canon" and ages["17"] == "retired" and ages["20"] == "found"
    with db.tx() as c:
        assert c.execute("SELECT COUNT(*) FROM fixes").fetchone()[0] == 1

    thread_q = next(q for q in qs if q["kind"] == "thread")
    archivist.answer_apply(thread_q["id"], "answer", choice=0)
    assert next(t for t in archivist.storylines(sid)
                if t["name"] == "the locked box")["status"] == "planned"

    # Documents build without the model.
    assert archivist.outline(b1)[0]["scenes"][0]["is_read"] == 1
    m = archivist.subplot_matrix(b1)
    assert any(r["thread"] == "the locked box" for r in m["rows"])
    assert archivist.characters(sid)[0]["appearances"] > 0
    md = archivist.export_markdown(sid)
    assert "Story Bible" in md and "Tavin" in md


def test_incremental_reread(tmp_path):
    sid = _series()
    reader = llm.DemoLLM()
    src = tmp_path / "book.md"
    text = (SAMPLES / "Book1_The_Ember_Road.md").read_text()
    src.write_text(text)
    bid = importer.import_book(sid, "Book", 1, src)
    archivist.full_read(reader, bid)
    assert archivist.scenes_to_read(bid) == []
    # Fix the age in chapter 3 only: one scene changes, one scene is re-read.
    src.write_text(text.replace("Tavin was 17 years old", "Tavin was 19 years old"))
    importer.import_book(sid, "Book", 1, src, book_id=bid)
    assert len(archivist.scenes_to_read(bid)) == 1
    archivist.full_read(reader, bid)
    card = {c["name"]: c for c in archivist.bible(sid)}["Tavin"]
    assert {f["value"] for f in card["facts"] if f["field"] == "age"} == {"19"}
    assert not card["has_conflict"]


def test_hand_edit_lists_disagreements():
    sid = _series()
    bid = importer.import_book(sid, "B", 1, SAMPLES / "Book1_The_Ember_Road.md")
    archivist.full_read(llm.DemoLLM(), bid)
    mara = {c["name"]: c for c in archivist.bible(sid)}["Mara"]
    eyes = next(f for f in mara["facts"] if f["field"] == "eyes")
    res = archivist.hand_edit(eyes["id"], value="blue")
    assert res["disagree"] == []  # only one eyes fact in this series
    mara = {c["name"]: c for c in archivist.bible(sid)}["Mara"]
    assert any(f["value"] == "blue" and f["status"] == "canon" for f in mara["facts"])
