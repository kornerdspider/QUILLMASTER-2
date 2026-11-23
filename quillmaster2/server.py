"""Local web server: the /v2 API plus the library-styled screens.

Start it with:  python run.py   (then open http://127.0.0.1:8765)
"""
import threading
import traceback
from pathlib import Path

from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import FileResponse, JSONResponse, PlainTextResponse, Response
from starlette.routing import Mount, Route
from starlette.staticfiles import StaticFiles

from . import __version__, archivist, config, db, importer, llm

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


async def health(_):
    cfg = config.load()
    h = reader().health()
    return ok({"version": __version__, "engine": cfg["engine"], "model": cfg["model"], **h})


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
                "(SELECT COUNT(*) FROM scenes WHERE book_id=b.id AND read_fingerprint=fingerprint) read "
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
        c.execute("DELETE FROM books WHERE id=?", (bid,))
    return ok()


# ---------------------------------------------------------------- full read jobs

def _run_job(job_id, book_ids, stop):
    cfg = config.load()
    model = llm.get(cfg)

    def progress(done, total, msg):
        with db.tx() as c:
            c.execute("UPDATE jobs SET progress=?, total=?, message=? WHERE id=?",
                      (done, total, msg, job_id))
    try:
        h = model.health()
        if not h["ok"]:
            raise llm.LLMError(h["message"])
        for bid in book_ids:
            finished = archivist.full_read(model, bid, progress, stop.is_set)
            if not finished:
                with db.tx() as c:
                    c.execute("UPDATE jobs SET status='paused', finished=? WHERE id=?",
                              (db.now(), job_id))
                return
        with db.tx() as c:
            c.execute("UPDATE jobs SET status='done', message='Done', finished=? WHERE id=?",
                      (db.now(), job_id))
    except Exception as e:  # noqa: BLE001
        traceback.print_exc()
        with db.tx() as c:
            c.execute("UPDATE jobs SET status='error', message=?, finished=? WHERE id=?",
                      (str(e), db.now(), job_id))
            c.execute("UPDATE books SET status='imported' WHERE status='reading'")
    finally:
        with _job_lock:
            _jobs.pop(job_id, None)


def _start(kind, target_id, book_ids):
    with _job_lock:
        if _jobs:
            return None
        with db.tx() as c:
            jid = c.execute("INSERT INTO jobs(kind,target_id,status,started,message) "
                            "VALUES(?,?,'running',?,'Starting')",
                            (kind, target_id, db.now())).lastrowid
        stop = threading.Event()
        _jobs[jid] = stop
    threading.Thread(target=_run_job, args=(jid, book_ids, stop), daemon=True).start()
    return jid


async def full_read_series(request):
    sid = int(request.path_params["id"])
    with db.tx() as c:
        ids = [r[0] for r in c.execute(
            "SELECT id FROM books WHERE series_id=? AND status!='planned' ORDER BY position", (sid,))]
    if not ids:
        return err("Add a book first.")
    jid = _start("series_read", sid, ids)
    return ok({"job_id": jid}, 202) if jid else err("A read is already running.", 409)


async def full_read_book(request):
    bid = int(request.path_params["id"])
    jid = _start("book_read", bid, [bid])
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


routes = [
    Route("/", index),
    Route("/v2/health", health),
    Route("/v2/settings", settings_get, methods=["GET"]),
    Route("/v2/settings", settings_put, methods=["PUT"]),
    Route("/v2/series", series_list, methods=["GET"]),
    Route("/v2/series", series_create, methods=["POST"]),
    Route("/v2/books", book_upload, methods=["POST"]),
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
    Mount("/static", StaticFiles(directory=STATIC), name="static"),
]


def create_app():
    db.init()
    with db.tx() as c:  # a read that was running when the app closed is now paused
        c.execute("UPDATE jobs SET status='paused' WHERE status='running'")
        c.execute("UPDATE books SET status='imported' WHERE status='reading'")
    return Starlette(routes=routes)
