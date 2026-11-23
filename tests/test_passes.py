"""Passes, Chapter mode edits, export and the Lexicon."""
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.environ.setdefault("QM2_DATA", tempfile.mkdtemp(prefix="qm2test_"))

from quillmaster2 import archivist, chapter, config, db, importer, lexicon, llm, passes  # noqa: E402

POLISH = """# Chapter 1: The Inn

There was a fire in the hearth. Mara saw the innkeeper wipe the bar. "Sit down." she said.
Tavin said softly that the the room smelled of smoke.  He could of left. Suddenly, the door opened.

It was very cold outside -- colder then the night before... The half-elf waited. Another half elf sat by the window… Her heart pounding, Mara crossed the room.

The old man by the fire told the long story of the river crossing again, how the water rose and the boats broke apart and nobody on the far bank could hear the shouting over the storm that night.

***

Mara was at the window after Tavin had gone upstairs. The lamps were lit and the inn was quiet for once.

She walks to the window. The street is empty and the rain is loud and she is tired of waiting for news.

The old man by the fire told the long story of the river crossing again, how the water rose and the boats broke apart and nobody on the far bank could hear them shouting over the storm that night.

The bridge is 300 miles long and was built in 1820.
"""


class FakeAI(llm.DemoLLM):
    """Answers like the real model would, so the AI parts of the passes run."""
    name = "fake"

    def json(self, system, user, schema):
        if "story beat" in system:
            n = user.count("\n")
            return {"scenes": [{"n": i, "beat": "Hook" if i == 1 else "none",
                                "moves_plot": i == 1, "why": "nothing changes"} for i in range(1, n + 1)]}
        if "tension" in system:
            return {"scenes": [{"n": 1, "tension": 2}, {"n": 2, "tension": 2}]}
        if "continuity" in system:
            return {"issues": [{"quote": "Mara saw the innkeeper", "problem": "Mara is blind in canon",
                                "fix": "Mara heard the innkeeper", "fact_id": 1}]}
        if "real world" in system.lower():
            return {"claims": [{"quote": "The bridge is 300 miles long", "claim": "A bridge 300 miles long"}]}
        if "proofread" in system:
            return {"issues": [{"quote": "not in the text at all", "problem": "x"}]}
        return {"issues": []}

    def text(self, system, user):
        return "Shorter scene."

    def embed(self, texts):
        return [[1.0, 0.0] for _ in texts]


def setup_module(_):
    db.set_path(Path(os.environ["QM2_DATA"]) / "passes.db")
    db.init()


def _book(tmp_path, text=POLISH):
    with db.tx() as c:
        sid = c.execute("INSERT INTO series(name) VALUES('S')").lastrowid
    p = tmp_path / "polish.md"
    p.write_text(text)
    bid = importer.import_book(sid, "Polish", 1, p)
    archivist.full_read(llm.DemoLLM(), bid)
    return sid, bid


def kinds(book_id, key):
    return [f["kind"] for f in chapter.findings_list(book_id, key)]


def test_rule_passes(tmp_path):
    cfg = dict(config.DEFAULTS, languagetool_url="")
    _, bid = _book(tmp_path)
    res = passes.run_passes(llm.DemoLLM(), cfg, bid,
                            ["line_edit", "grammar", "punctuation", "repetition", "flow", "factcheck"])
    assert res["filed"]["line_edit"] > 0
    le = kinds(bid, "line_edit")
    for k in ("filter_word", "tag_adverb", "cliche", "suddenly", "very", "weak_opener"):
        assert k in le, k
    gr = [(f["message"], f["suggestion"]) for f in chapter.findings_list(bid, "grammar")]
    assert any("doubled" in m for m, _ in gr)
    assert any(s == "could have" for _, s in gr)
    assert any(s == "colder than" for _, s in gr)
    pu = chapter.findings_list(bid, "punctuation")
    pk = [f["kind"] for f in pu]
    assert "punct_rule" in pk and "ellipsis_style" in pk and "style_sheet" in pk
    assert any(f["suggestion"] and f["suggestion"].startswith(",") for f in pu)  # "Sit down," she said
    assert "repeated_passage" in kinds(bid, "repetition")
    assert "tense_slip" in kinds(bid, "flow")
    assert "claim" in kinds(bid, "factcheck")


def test_model_passes_and_rejections_stick(tmp_path):
    cfg = dict(config.DEFAULTS, languagetool_url="")
    sid, bid = _book(tmp_path)
    mara = next(x for x in archivist.bible(sid) if x["name"] == "Mara")
    archivist.add_hand_fact(mara["id"], "sight", "blind since birth")
    ai = FakeAI()
    passes.run_passes(ai, cfg, bid, ["structure", "continuity", "pacing", "grammar", "factcheck"])
    assert "static_scene" in kinds(bid, "structure") and "missing_beat" in kinds(bid, "structure")
    cont = chapter.findings_list(bid, "continuity")
    assert any(f["suggestion"] == "Mara heard the innkeeper" for f in cont)
    # The model's made-up quote (not in the text) is dropped.
    assert not any(f["quote"] == "not in the text at all" for f in chapter.findings_list(bid, "grammar"))
    with db.tx() as c:
        assert c.execute("SELECT beat FROM scenes WHERE book_id=? ORDER BY id LIMIT 1", (bid,)).fetchone()[0] == "Hook"
    # Reject a finding; re-running doesn't bring it back.
    f = cont[0]
    chapter.reject_finding(f["id"])
    passes.run_passes(ai, cfg, bid, ["continuity"])
    assert not any(x["message"] == f["message"] and x["quote"] == f["quote"]
                   for x in chapter.findings_list(bid, "continuity"))


def test_accept_applies_and_shifts(tmp_path):
    cfg = dict(config.DEFAULTS, languagetool_url="")
    _, bid = _book(tmp_path)
    passes.run_passes(llm.DemoLLM(), cfg, bid, ["grammar", "punctuation"])
    could = next(f for f in chapter.findings_list(bid, "grammar") if f["suggestion"] == "could have")
    with db.tx() as c:
        before = c.execute("SELECT text FROM scenes WHERE id=?", (could["scene_id"],)).fetchone()[0]
    later = [f for f in chapter.findings_list(bid) if f["scene_id"] == could["scene_id"]
             and f["start"] is not None and f["start"] > could["end"]]
    chapter.accept_finding(could["id"])
    with db.tx() as c:
        after = c.execute("SELECT text FROM scenes WHERE id=?", (could["scene_id"],)).fetchone()[0]
    assert "could have left" in after and "could of" not in after
    # Findings after the edit moved with their words.
    for f in later:
        g = next((x for x in chapter.findings_list(bid) if x["id"] == f["id"]), None)
        if g:
            assert after[g["start"]:g["end"]] == before[f["start"]:f["end"]]
    # The edited scene needs re-reading now.
    assert could["scene_id"] in archivist.scenes_to_read(bid)


def test_export_backup_and_italics(tmp_path):
    import docx
    d = docx.Document()
    d.add_heading("Chapter 1", level=1)
    p = d.add_paragraph("She whispered ")
    p.add_run("never").italic = True
    p.add_run(" again.")
    src = tmp_path / "ital.docx"
    d.save(src)
    with db.tx() as c:
        sid = c.execute("INSERT INTO series(name) VALUES('S2')").lastrowid
    bid = importer.import_book(sid, "Ital", 1, src)
    with db.tx() as c:
        text = c.execute("SELECT text FROM scenes WHERE book_id=?", (bid,)).fetchone()[0]
    assert text == "She whispered *never* again."
    out = chapter.export_book(bid, "docx", tmp_path / "out.docx")
    runs = [(r.text, r.italic) for r in docx.Document(out).paragraphs[1].runs]
    assert ("never", True) in runs
    for fmt in ("md", "txt", "odt"):
        assert chapter.export_book(bid, fmt, tmp_path / f"out.{fmt}").stat().st_size > 0
    assert (tmp_path / "out.txt").read_text().count("*") == 0
    path = chapter.backup_book(bid)
    assert path.exists()


def test_workbenches(tmp_path):
    _, bid = _book(tmp_path)
    with db.tx() as c:
        ch = c.execute("SELECT id FROM chapters WHERE book_id=?", (bid,)).fetchone()[0]
    v = chapter.chapter_view(ch)
    assert len(v["scenes"]) == 2
    st = chapter.setting_view(ch)
    assert st[0]["senses"]["smell"] >= 1
    dv = chapter.dialogue_view(ch)
    assert dv["lines"]
    out = chapter.condense(FakeAI(), v["scenes"][0]["id"], "truncate", 20)
    assert out["text"] == "Shorter scene."


def test_lexicon():
    h = lexicon.analyze_poem("An old silent pond\nA frog jumps into the pond\nSplash! Silence again")
    assert h["form"] == "haiku"
    lim = lexicon.analyze_poem("There once was a man from the shore\nWho walked on the sand by the door\n"
                               "He tripped on a stone\nAnd sat all alone\nThen got up and walked to the shore")
    assert lim["form"] == "limerick"
    assert lexicon.lookup("lantern", config.DEFAULTS, llm.DemoLLM())["syllables"] == 2
