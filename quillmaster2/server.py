"""Local web server: the /v2 API plus the library-styled screens.

Start it with:  python run.py   (then open http://127.0.0.1:8765)
"""
import os
import sys
import threading
import traceback
from pathlib import Path

from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import FileResponse, JSONResponse, PlainTextResponse, Response
from starlette.routing import Mount, Route
from starlette.staticfiles import StaticFiles

from . import __version__, archivist, chapter, config, db, importer, lexicon, llm, ollama_setup, passes

STATIC = Path(__file__).parent / "static"
_jobs = {}            # job id -> threading.Event used to pause
_job_lock = threading.Lock()


def ok(data=None, status=200):
    return JSONResponse(data if data is not None else {"ok": True}, status_code=status)


def err(message, status=400):
    return JSONResponse({"error": message}, status_code=status)


def reader():
    return llm.get(config.load())


async def body(request):
    try:
        return await request.json()
    except Exception:  # noqa: BLE001
        return {}


# ---------------------------------------------------------------- basics

async def index(_):
    return FileResponse(STATIC / "index.html")


async def quit_app(_):
    """Stop Quill Master (used when it runs in a browser tab with no window to close)."""
    threading.Timer(0.5, lambda: os._exit(0)).start()
    return ok({"bye": True})


async def health(_):
    cfg = config.load()
    h = reader().health()
    return ok({"version": __version__, "engine": cfg["engine"], "model": cfg["model"],
               "packaged": bool(getattr(sys, "frozen", False)), **h})


async def settings_get(_):
    return ok(config.load())


async def settings_put(request):
    data = await body(request)
    cfg = config.load()
    cfg.update(data)
    return ok(config.save(cfg))


# ---------------------------------------------------------------- series and books

async def series_list(_):
    with db.tx() as c:
        out = db.rows(c.execute("SELECT * FROM series ORDER BY id"))
        for s in out:
            s["books"] = db.rows(c.execute(
                "SELECT b.*, (SELECT COUNT(*) FROM chapters WHERE book_id=b.id) chapters, "
                "(SELECT COUNT(*) FROM scenes WHERE book_id=b.id) scenes, "
                "(SELECT COUNT(*) FROM scenes WHERE book_id=b.id AND read_fingerprint=fingerprint) read, "
                "(SELECT COUNT(*) FROM findings WHERE book_id=b.id AND status='open') findings "
                "FROM books b WHERE series_id=? ORDER BY position", (s["id"],)))
            s["open_questions"] = c.execute(
                "SELECT COUNT(*) FROM questions WHERE series_id=? AND status='open'",
                (s["id"],)).fetchone()[0]
            s["conflicts"] = c.execute(
                "SELECT COUNT(*) FROM facts f JOIN entities e ON e.id=f.entity_id "
                "WHERE e.series_id=? AND f.status='conflict'", (s["id"],)).fetchone()[0]
    return ok(out)


async def series_create(request):
    data = await body(request)
    name = (data.get("name") or "").strip() or "My Series"
    with db.tx() as c:
        sid = c.execute("INSERT INTO series(name,created) VALUES(?,?)", (name, db.now())).lastrowid
    return ok({"id": sid, "name": name}, 201)


async def book_upload(request: Request):
    """POST raw file bytes. Query: series_id, title, position, filename, book_id (to re-import)."""
    qp = request.query_params
    filename = Path(qp.get("filename", "")).name
    if Path(filename).suffix.lower() not in (".docx", ".odt", ".md", ".markdown", ".txt"):
        return err("Use a .docx, .odt, .md or .txt file.")
    data = await request.body()
    if not data:
        return err("The file was empty.")
    dest = config.data_dir() / "uploads" / f"{int(db.now())}_{filename}"
    dest.write_bytes(data)
    try:
        book_id = importer.import_book(
            int(qp["series_id"]), qp.get("title") or Path(filename).stem,
            int(qp.get("position", 1)), dest, config.load()["max_scene_words"],
            book_id=int(qp["book_id"]) if qp.get("book_id") else None, display_name=filename)
    except (ValueError, KeyError) as e:
        return err(str(e))
    with db.tx() as c:
        book = db.row(c.execute("SELECT * FROM books WHERE id=?", (book_id,)))
    return ok(book, 201)


def _samples_dir():
    base = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent.parent))
    return base / "samples"


async def load_samples(request):
    """Add the two sample books (with planted mistakes) to a series, to try things out."""
    sid = int(request.path_params["id"])
    folder = _samples_dir()
    files = [("The Ember Road", 1, "Book1_The_Ember_Road.md"), ("The Ash Sea", 2, "Book2_The_Ash_Sea.txt")]
    added = []
    for title, pos, name in files:
        if (folder / name).exists():
            added.append(importer.import_book(sid, title, pos, folder / name,
                                              config.load()["max_scene_words"], display_name=name))
    return ok({"added": added}) if added else err("Sample books weren't found.", 404)


async def book_patch(request):
    bid = int(request.path_params["id"])
    data = await body(request)
    with db.tx() as c:
        for k in ("title", "position"):
            if k in data:
                c.execute(f"UPDATE books SET {k}=? WHERE id=?", (data[k], bid))
    return ok()


async def book_delete(request):
    bid = int(request.path_params["id"])
    with db.tx() as c:
        for (sid,) in c.execute("SELECT id FROM scenes WHERE book_id=?", (bid,)).fetchall():
            importer.forget_scene(c, sid)
        c.execute("DELETE FROM scenes WHERE book_id=?", (bid,))
        c.execute("DELETE FROM chapters WHERE book_id=?", (bid,))
        c.execute("DELETE FROM documents WHERE book_id=?", (bid,))
        c.execute("DELETE FROM findings WHERE book_id=?", (bid,))
        c.execute("DELETE FROM pass_runs WHERE book_id=?", (bid,))
        c.execute("DELETE FROM books WHERE id=?", (bid,))
    return ok()


# ---------------------------------------------------------------- full read jobs

def _run_job(job_id, work, stop):
    """Run one background job. work(progress, stop) returns a message or raises."""
    def progress(done, total, msg):
        with db.tx() as c:
            c.execute("UPDATE jobs SET progress=?, total=?, message=? WHERE id=?",
                      (done, total, msg, job_id))
    try:
        result = work(progress, stop)
        status = "paused" if result == "paused" else "done"
        with db.tx() as c:
            c.execute("UPDATE jobs SET status=?, message=?, finished=? WHERE id=?",
                      (status, "Paused" if status == "paused" else (result or "Done"), db.now(), job_id))
    except Exception as e:  # noqa: BLE001
        traceback.print_exc()
        with db.tx() as c:
            c.execute("UPDATE jobs SET status='error', message=?, finished=? WHERE id=?",
                      (str(e)[:500], db.now(), job_id))
            c.execute("UPDATE books SET status='imported' WHERE status='reading'")
    finally:
        with _job_lock:
            _jobs.pop(job_id, None)


def _start(kind, target_id, work):
    with _job_lock:
        if _jobs:
            return None
        with db.tx() as c:
            jid = c.execute("INSERT INTO jobs(kind,target_id,status,started,message) "
                            "VALUES(?,?,'running',?,'Starting')",
                            (kind, target_id, db.now())).lastrowid
        stop = threading.Event()
        _jobs[jid] = stop
    threading.Thread(target=_run_job, args=(jid, work, stop), daemon=True).start()
    return jid


def _read_work(book_ids):
    def work(progress, stop):
        model = reader()
        h = model.health()
        if not h["ok"]:
            raise llm.LLMError(h["message"])
        for bid in book_ids:
            if not archivist.full_read(model, bid, progress, stop.is_set):
                return "paused"
        return "Full read finished"
    return work


async def full_read_series(request):
    sid = int(request.path_params["id"])
    with db.tx() as c:
        ids = [r[0] for r in c.execute(
            "SELECT id FROM books WHERE series_id=? AND status!='planned' ORDER BY position", (sid,))]
    if not ids:
        return err("Add a book first.")
    jid = _start("series_read", sid, _read_work(ids))
    return ok({"job_id": jid}, 202) if jid else err("A read is already running.", 409)


async def full_read_book(request):
    bid = int(request.path_params["id"])
    jid = _start("book_read", bid, _read_work([bid]))
    return ok({"job_id": jid}, 202) if jid else err("A read is already running.", 409)


async def job_get(request):
    with db.tx() as c:
        j = db.row(c.execute("SELECT * FROM jobs WHERE id=?", (int(request.path_params["id"]),)))
    return ok(j) if j else err("No such job", 404)


async def job_current(_):
    with db.tx() as c:
        j = db.row(c.execute("SELECT * FROM jobs ORDER BY id DESC LIMIT 1"))
    return ok(j or {})


async def job_pause(request):
    jid = int(request.path_params["id"])
    with _job_lock:
        ev = _jobs.get(jid)
    if ev:
        ev.set()
    return ok()


# ---------------------------------------------------------------- bible

async def bible_get(request):
    qp = request.query_params
    return ok(archivist.bible(int(request.path_params["id"]), qp.get("type") or None,
                              qp.get("status") or None, qp.get("q") or None))


async def fact_patch(request):
    data = await body(request)
    try:
        return ok(archivist.hand_edit(int(request.path_params["id"]),
                                      data.get("value"), data.get("status")))
    except ValueError as e:
        return err(str(e), 404)


async def entity_add_fact(request):
    data = await body(request)
    if not data.get("field") or not data.get("value"):
        return err("Give a field and a value.")
    fid = archivist.add_hand_fact(int(request.path_params["id"]), data["field"], data["value"],
                                  data.get("status", "canon"), data.get("book_id"))
    return ok({"id": fid}, 201)


async def entity_create(request):
    data = await body(request)
    sid = int(request.path_params["id"])
    if not data.get("name"):
        return err("Give a name.")
    with db.tx() as c:
        eid = c.execute("INSERT INTO entities(series_id,type,name,aliases,created) VALUES(?,?,?,?,?)",
                        (sid, data.get("type", "character"), data["name"], "[]",
                         db.now())).lastrowid
    return ok({"id": eid}, 201)


async def bible_export(request):
    sid = int(request.path_params["id"])
    fmt = request.query_params.get("format", "md")
    if fmt == "docx":
        path = config.data_dir() / f"story_bible_{sid}.docx"
        archivist.export_docx(sid, path)
        return FileResponse(path, filename="Story Bible.docx")
    return PlainTextResponse(archivist.export_markdown(sid), headers={
        "Content-Disposition": 'attachment; filename="Story Bible.md"'})


# ---------------------------------------------------------------- reference desk

async def questions_get(request):
    sid = int(request.path_params["id"])
    status = request.query_params.get("status", "open")
    with db.tx() as c:
        qs = db.rows(c.execute("SELECT * FROM questions WHERE series_id=? AND status=? "
                               "ORDER BY priority, id", (sid, status)))
    for q in qs:
        q["payload"] = db.jload(q["payload"], {})
    return ok(qs)


async def question_answer(request):
    data = await body(request)
    fn = archivist.answer_apply if data.get("confirm") else archivist.answer_preview
    try:
        return ok(fn(int(request.path_params["id"]), data.get("action", "answer"),
                     data.get("choice"), data.get("text")))
    except (ValueError, IndexError) as e:
        return err(str(e))


async def ask(request):
    data = await body(request)
    if not (data.get("question") or "").strip():
        return err("Type a question.")
    try:
        return ok(archivist.ask(reader(), int(request.path_params["id"]), data["question"]))
    except llm.LLMError as e:
        return err(str(e), 503)


async def fixes_get(request):
    sid = int(request.path_params["id"])
    with db.tx() as c:
        fx = db.rows(c.execute("SELECT * FROM fixes WHERE series_id=? AND status='open' "
                               "ORDER BY id", (sid,)))
        for f in fx:
            f["where"] = archivist.where(c, f["scene_id"])
    return ok(fx)


async def fix_done(request):
    with db.tx() as c:
        c.execute("UPDATE fixes SET status='done' WHERE id=?", (int(request.path_params["id"]),))
    return ok()


# ---------------------------------------------------------------- the index (documents)

async def document_get(request):
    bid = int(request.path_params["id"])
    kind = request.path_params["kind"]
    with db.tx() as c:
        book = db.row(c.execute("SELECT * FROM books WHERE id=?", (bid,)))
    if not book:
        return err("No such book", 404)
    sid = book["series_id"]
    if kind == "outline":
        return ok(archivist.outline(bid))
    if kind == "summary":
        saved = archivist.get_saved(sid, bid, "book_summary")
        return ok({"book": saved["content"] if saved else "",
                   "chapters": [{"title": c["title"], "summary": c["summary"]}
                                for c in archivist.outline(bid)]})
    if kind == "characters":
        return ok(archivist.characters(sid))
    if kind == "storylines":
        return ok(archivist.storylines(sid))
    if kind == "subplot-matrix":
        return ok(archivist.subplot_matrix(bid))
    if kind == "synopsis":
        saved = archivist.get_saved(sid, bid, "synopsis")
        return ok({"text": saved["content"] if saved else "",
                   "built_at": saved["built_at"] if saved else None})
    return err("Unknown document", 404)


async def synopsis_build(request):
    try:
        return ok({"text": archivist.synopsis(reader(), int(request.path_params["id"]))})
    except llm.LLMError as e:
        return err(str(e), 503)


# ---------------------------------------------------------------- passes

async def passes_info(_):
    return ok({"passes": passes.PASSES, "presets": passes.PRESETS,
               "frameworks": list(passes.FRAMEWORKS)})


async def passes_run(request):
    bid = int(request.path_params["id"])
    data = await body(request)
    keys = data.get("passes") or []
    if data.get("preset") in passes.PRESETS:
        keys = passes.PRESETS[data["preset"]]["passes"]
    keys = [k for k in keys if k in passes.BY_KEY]
    if not keys:
        return err("Pick at least one pass.")
    chapters = data.get("chapters") or None

    def work(progress, stop):
        model = reader()
        if getattr(model, "name", "") != "demo":
            h = model.health()
            if not h["ok"]:
                raise llm.LLMError(h["message"])
        res = passes.run_passes(model, config.load(), bid, keys, chapters, progress, stop.is_set)
        total = sum(res["filed"].values())
        msg = f"Passes finished: {total} findings"
        if res["notes"]:
            msg += ". " + " ".join(dict.fromkeys(res["notes"]))[:300]
        return msg
    jid = _start("passes", bid, work)
    return ok({"job_id": jid}, 202) if jid else err("Something is already running.", 409)


async def findings_get(request):
    qp = request.query_params
    return ok(chapter.findings_list(int(request.path_params["id"]), qp.get("pass") or None,
                                    qp.get("status", "open"),
                                    int(qp["chapter"]) if qp.get("chapter") else None))


async def findings_summary(request):
    return ok(chapter.findings_summary(int(request.path_params["id"])))


async def finding_accept(request):
    data = await body(request)
    try:
        return ok(chapter.accept_finding(int(request.path_params["id"]), data.get("replacement")))
    except ValueError as e:
        return err(str(e), 409)


async def finding_reject(request):
    chapter.reject_finding(int(request.path_params["id"]))
    return ok()


# ---------------------------------------------------------------- chapter mode

async def chapter_get(request):
    v = chapter.chapter_view(int(request.path_params["id"]))
    return ok(v) if v else err("No such chapter", 404)


async def chapter_dialogue(request):
    return ok(chapter.dialogue_view(int(request.path_params["id"])))


async def chapter_setting(request):
    return ok(chapter.setting_view(int(request.path_params["id"])))


async def chapter_beats(request):
    cid = int(request.path_params["id"])

    def work(progress, stop):
        progress(0, 1, "Reading scene beats")
        chapter.analyze_beats(reader(), cid)
        return "Scene beats ready"
    jid = _start("beats", cid, work)
    return ok({"job_id": jid}, 202) if jid else err("Something is already running.", 409)


async def scene_put(request):
    data = await body(request)
    if "text" not in data:
        return err("Send the new text.")
    chapter.edit_scene(int(request.path_params["id"]), data["text"])
    return ok()


async def scene_replace(request):
    data = await body(request)
    try:
        text = chapter.replace_span(int(request.path_params["id"]), int(data["start"]),
                                    int(data["end"]), data.get("text", ""))
        return ok({"text": text})
    except (ValueError, KeyError) as e:
        return err(str(e), 409)


async def scene_condense(request):
    data = await body(request)
    try:
        return ok(chapter.condense(reader(), int(request.path_params["id"]),
                                   data.get("mode", "truncate"), int(data.get("target_pct", 15))))
    except llm.LLMError as e:
        return err(str(e), 503)


async def book_backup(request):
    path = chapter.backup_book(int(request.path_params["id"]))
    return ok({"filename": path.name, "folder": str(path.parent),
               "url": f"/v2/backups/{path.name}"})


async def backup_file(request):
    name = Path(request.path_params["name"]).name
    path = config.data_dir() / "backups" / name
    if not path.exists():
        return err("No such backup", 404)
    return FileResponse(path, filename=name)


async def book_export(request):
    bid = int(request.path_params["id"])
    fmt = request.query_params.get("format", "docx")
    if fmt not in ("docx", "odt", "md", "txt"):
        return err("Format must be docx, odt, md or txt")
    with db.tx() as c:
        b = db.row(c.execute("SELECT title FROM books WHERE id=?", (bid,)))
    if not b:
        return err("No such book", 404)
    path = config.data_dir() / f"export_{bid}.{fmt}"
    chapter.export_book(bid, fmt, path)
    return FileResponse(path, filename=f"{b['title']}.{fmt}")


# ---------------------------------------------------------------- lexicon and models

async def lexicon_lookup(request):
    data = await body(request)
    try:
        return ok(lexicon.lookup(data.get("word", ""), config.load(), reader()))
    except ValueError as e:
        return err(str(e))


async def lexicon_poem(request):
    data = await body(request)
    if not (data.get("text") or "").strip():
        return err("Paste a poem first.")
    return ok(lexicon.analyze_poem(data["text"], data.get("form") or None))


async def ollama_models(_):
    m = llm.OllamaLLM(config.load())
    names = m.installed()
    return ok({"running": names is not None, "models": names or []})


async def ollama_pull(request):
    data = await body(request)
    model = (data.get("model") or "").strip()
    if not model:
        return err("Type a model name.")
    cfg = config.load()

    def work(progress, stop):
        import json as _json
        import requests as _rq
        r = _rq.post(f"{cfg['ollama_url'].rstrip('/')}/api/pull", json={"model": model, "stream": True},
                     stream=True, timeout=None)
        if r.status_code != 200:
            raise llm.LLMError(f"Ollama couldn't download {model}: {r.text[:200]}")
        for line in r.iter_lines():
            if stop.is_set():
                return "paused"
            if not line:
                continue
            d = _json.loads(line)
            if d.get("error"):
                raise llm.LLMError(d["error"])
            total, done = d.get("total") or 0, d.get("completed") or 0
            progress(int(done / 1e6), int(total / 1e6) or 1,
                     f"Downloading {model}: {d.get('status', '')}"
                     + (f" {done / total:.0%}" if total else ""))
        return f"{model} is installed"
    try:
        jid = _start("pull", 0, work)
    except Exception as e:  # noqa: BLE001
        return err(str(e))
    return ok({"job_id": jid}, 202) if jid else err("Something is already running.", 409)



# ---------------------------------------------------------------- AI setup

async def ai_status(_):
    return ok(ollama_setup.status())


async def ai_install(_):
    def work(progress, stop):
        return ollama_setup.install(progress, stop.is_set)
    jid = _start("install_ollama", 0, work)
    return ok({"job_id": jid}, 202) if jid else err("Something is already running.", 409)


async def ai_start(_):
    import asyncio
    try:
        msg = await asyncio.get_running_loop().run_in_executor(None, ollama_setup.start)
        return ok({"message": msg})
    except RuntimeError as e:
        return err(str(e), 503)


async def ai_use_model(request):
    data = await body(request)
    cfg = config.load()
    cfg["model"] = (data.get("model") or ollama_setup.RECOMMENDED_MODEL).strip()
    cfg["engine"] = "ollama"
    config.save(cfg)
    return ok(ollama_setup.status())


async def ai_test(_):
    import asyncio
    cfg = config.load()
    try:
        res = await asyncio.get_running_loop().run_in_executor(
            None, ollama_setup.check_model, llm.OllamaLLM(cfg))
        return ok({"model": cfg["model"], **res})
    except llm.LLMError as e:
        return err(str(e), 503)


routes = [
    Route("/", index),
    Route("/v2/health", health),
    Route("/v2/quit", quit_app, methods=["POST"]),
    Route("/v2/settings", settings_get, methods=["GET"]),
    Route("/v2/settings", settings_put, methods=["PUT"]),
    Route("/v2/series", series_list, methods=["GET"]),
    Route("/v2/series", series_create, methods=["POST"]),
    Route("/v2/books", book_upload, methods=["POST"]),
    Route("/v2/series/{id:int}/samples", load_samples, methods=["POST"]),
    Route("/v2/books/{id:int}", book_patch, methods=["PATCH"]),
    Route("/v2/books/{id:int}", book_delete, methods=["DELETE"]),
    Route("/v2/series/{id:int}/full-read", full_read_series, methods=["POST"]),
    Route("/v2/books/{id:int}/full-read", full_read_book, methods=["POST"]),
    Route("/v2/jobs/current", job_current),
    Route("/v2/jobs/{id:int}", job_get),
    Route("/v2/jobs/{id:int}/pause", job_pause, methods=["POST"]),
    Route("/v2/series/{id:int}/bible", bible_get),
    Route("/v2/series/{id:int}/bible/export", bible_export),
    Route("/v2/series/{id:int}/entities", entity_create, methods=["POST"]),
    Route("/v2/entities/{id:int}/facts", entity_add_fact, methods=["POST"]),
    Route("/v2/facts/{id:int}", fact_patch, methods=["PATCH"]),
    Route("/v2/series/{id:int}/questions", questions_get),
    Route("/v2/questions/{id:int}/answer", question_answer, methods=["POST"]),
    Route("/v2/series/{id:int}/ask", ask, methods=["POST"]),
    Route("/v2/series/{id:int}/fixes", fixes_get),
    Route("/v2/fixes/{id:int}/done", fix_done, methods=["POST"]),
    Route("/v2/books/{id:int}/documents/{kind}", document_get),
    Route("/v2/books/{id:int}/documents/synopsis", synopsis_build, methods=["POST"]),
    Route("/v2/passes", passes_info),
    Route("/v2/books/{id:int}/passes", passes_run, methods=["POST"]),
    Route("/v2/books/{id:int}/findings", findings_get),
    Route("/v2/books/{id:int}/findings/summary", findings_summary),
    Route("/v2/findings/{id:int}/accept", finding_accept, methods=["POST"]),
    Route("/v2/findings/{id:int}/reject", finding_reject, methods=["POST"]),
    Route("/v2/chapters/{id:int}", chapter_get),
    Route("/v2/chapters/{id:int}/dialogue", chapter_dialogue),
    Route("/v2/chapters/{id:int}/setting", chapter_setting),
    Route("/v2/chapters/{id:int}/beats", chapter_beats, methods=["POST"]),
    Route("/v2/scenes/{id:int}", scene_put, methods=["PUT"]),
    Route("/v2/scenes/{id:int}/replace", scene_replace, methods=["POST"]),
    Route("/v2/scenes/{id:int}/condense", scene_condense, methods=["POST"]),
    Route("/v2/books/{id:int}/backup", book_backup, methods=["POST"]),
    Route("/v2/backups/{name}", backup_file),
    Route("/v2/books/{id:int}/export", book_export),
    Route("/v2/lexicon/lookup", lexicon_lookup, methods=["POST"]),
    Route("/v2/lexicon/poem", lexicon_poem, methods=["POST"]),
    Route("/v2/ollama/models", ollama_models),
    Route("/v2/ai/status", ai_status),
    Route("/v2/ai/install-ollama", ai_install, methods=["POST"]),
    Route("/v2/ai/start-ollama", ai_start, methods=["POST"]),
    Route("/v2/ai/use-model", ai_use_model, methods=["POST"]),
    Route("/v2/ai/test", ai_test, methods=["POST"]),
    Route("/v2/ollama/pull", ollama_pull, methods=["POST"]),
    Mount("/static", StaticFiles(directory=STATIC), name="static"),
]


def create_app():
    db.init()
    with db.tx() as c:  # a read that was running when the app closed is now paused
        c.execute("UPDATE jobs SET status='paused' WHERE status='running'")
        c.execute("UPDATE books SET status='imported' WHERE status='reading'")
    threading.Thread(target=ollama_setup.autostart, daemon=True).start()
    return Starlette(routes=routes)
