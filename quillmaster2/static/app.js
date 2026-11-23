// Quill Master 2 — screens
const $ = (s, el = document) => el.querySelector(s);
const $$ = (s, el = document) => [...el.querySelectorAll(s)];
const esc = s => String(s ?? "").replace(/[&<>"']/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const state = { series: [], sid: null, book: null, page: "catalog", doc: "outline", type: "", qIndex: 0, questions: [], choice: null };

async function api(path, opts = {}) {
  const o = { ...opts };
  if (o.json !== undefined) { o.body = JSON.stringify(o.json); o.headers = { "Content-Type": "application/json" }; delete o.json; }
  const r = await fetch(path, o);
  const ct = r.headers.get("content-type") || "";
  const data = ct.includes("json") ? await r.json() : await r.text();
  if (!r.ok) throw new Error(data.error || data || r.statusText);
  return data;
}
function toast(msg) { const t = $("#toast"); t.textContent = msg; t.classList.add("on"); clearTimeout(t._h); t._h = setTimeout(() => t.classList.remove("on"), 3200); }
const fmt = n => (n || 0).toLocaleString();
const series = () => state.series.find(s => s.id === state.sid);

// ---------- navigation ----------
$$("nav button[data-page]").forEach(b => b.onclick = () => go(b.dataset.page));
function go(page) {
  state.page = page;
  $$("nav button[data-page]").forEach(b => b.classList.toggle("active", b.dataset.page === page));
  $$(".page").forEach(p => p.classList.toggle("on", p.id === "page-" + page));
  render();
}
// Dark is the default. The moon button switches to the light paper look and back.
$("#nightBtn").onclick = () => { document.body.classList.toggle("night"); try { localStorage.qmTheme = document.body.classList.contains("night") ? "dark" : "light"; } catch (e) {} };
try { if (localStorage.qmTheme === "light") document.body.classList.remove("night"); } catch (e) {}

// ---------- series ----------
async function loadSeries() {
  state.series = await api("/v2/series");
  if (!state.series.length) {
    const s = await api("/v2/series", { method: "POST", json: { name: "My Trilogy" } });
    state.series = await api("/v2/series"); state.sid = s.id;
  }
  if (!state.sid || !series()) state.sid = state.series[0].id;
  $("#seriesSel").innerHTML = state.series.map(s => `<option value="${s.id}" ${s.id === state.sid ? "selected" : ""}>${esc(s.name)}</option>`).join("");
  const s = series();
  const q = s.open_questions; const badge = $("#qBadge");
  badge.hidden = !q; badge.textContent = q > 99 ? "99+" : q;
  if (!state.book || !s.books.find(b => b.id === state.book)) state.book = s.books[0]?.id || null;
}
$("#seriesSel").onchange = e => { state.sid = +e.target.value; state.book = null; refresh(); };
$("#newSeriesBtn").onclick = async () => {
  const name = prompt("Name of the series:", "My Trilogy"); if (!name) return;
  const s = await api("/v2/series", { method: "POST", json: { name } }); state.sid = s.id; state.book = null; refresh();
};

async function refresh() { await loadSeries(); render(); }
function render() {
  ({ catalog: renderCatalog, index: renderIndex, archive: renderArchive, desk: renderDesk, settings: renderSettings })[state.page]?.();
}

// ---------- health and jobs ----------
async function checkHealth() {
  try {
    const h = await api("/v2/health");
    $("#healthDot").className = "dot " + (h.ok ? "ok" : "bad");
    $("#healthMsg").textContent = h.engine === "demo" ? "Demo mode" : (h.ok ? h.model : "Model not ready");
    $("#health").title = h.message;
  } catch (e) { $("#healthDot").className = "dot bad"; $("#healthMsg").textContent = "Server stopped"; }
}
let lastJobStatus = null;
async function pollJob() {
  try {
    const j = await api("/v2/jobs/current");
    const on = j.status === "running";
    $("#job").classList.toggle("on", on);
    if (on) {
      $("#jobMsg").textContent = j.message || "Reading…";
      $("#jobBar").style.width = (j.total ? Math.round(100 * j.progress / j.total) : 2) + "%";
      $("#pauseBtn").onclick = () => api(`/v2/jobs/${j.id}/pause`, { method: "POST" }).then(() => toast("Pausing after this scene…"));
    }
    if (lastJobStatus === "running" && !on) {
      toast(j.status === "error" ? "Read stopped: " + j.message : j.status === "paused" ? "Read paused. Click Read Full Manuscript to resume." : "Full read finished.");
      refresh();
    } else if (on && state.page === "catalog" && Math.random() < 0.35) refresh();
    lastJobStatus = j.status;
  } catch (e) {}
  setTimeout(pollJob, 2000);
}

// ---------- catalog ----------
function renderCatalog() {
  const s = series(); if (!s) return;
  const books = s.books;
  const next = (books.reduce((m, b) => Math.max(m, b.position), 0) || 0) + 1;
  $("#shelf").innerHTML = books.map(b => {
    const pct = b.scenes ? Math.round(100 * b.read / b.scenes) : 0;
    return `<div class="book ${b.id === state.book ? "sel" : ""}" data-id="${b.id}">
      <div class="callno">BOOK ${b.position}</div><h3>${esc(b.title)}</h3>
      <div class="stats">${fmt(b.word_count)} words · ${b.chapters} chapters · ${b.scenes} scenes</div>
      <div class="progress"><i style="width:${pct}%"></i></div>
      <div class="stats">${b.status === "read" && pct === 100 ? "Read" : pct ? pct + "% read" : "Not read yet"}</div>
      <div class="row" style="margin-top:12px">
        <label class="btn small" title="Load a new version of this book. Only changed scenes are re-read.">Re-import<input type="file" hidden accept=".docx,.odt,.md,.txt" data-reimport="${b.id}"></label>
        <button class="btn ghost small danger" data-del="${b.id}">Remove</button>
      </div></div>`;
  }).join("") + `<div class="book add">
      <div class="callno">BOOK ${next}</div><h3>Add a book</h3>
      <label>Title</label><input type="text" id="newTitle" placeholder="Book title">
      <label>Place in the series</label><input type="number" id="newPos" value="${next}" min="1" style="width:80px">
      <label>Manuscript file (.docx, .odt, .md, .txt)</label><input type="file" id="newFile" accept=".docx,.odt,.md,.txt">
      <div class="row" style="margin-top:12px"><button class="btn primary" id="addBookBtn">Add to shelf</button></div></div>`;
  $$(".book[data-id]").forEach(el => el.onclick = e => { if (e.target.closest("button,label,input")) return; state.book = +el.dataset.id; renderCatalog(); });
  $$("[data-del]").forEach(b => b.onclick = async () => { if (!confirm("Remove this book and everything read from it? The file on your computer is not touched.")) return; await api(`/v2/books/${b.dataset.del}`, { method: "DELETE" }); refresh(); });
  $$("[data-reimport]").forEach(inp => inp.onchange = () => upload(inp.files[0], { book_id: inp.dataset.reimport, position: books.find(b => b.id == inp.dataset.reimport).position }));
  $("#addBookBtn").onclick = () => {
    const f = $("#newFile").files[0]; if (!f) return toast("Choose a manuscript file first.");
    upload(f, { title: $("#newTitle").value || f.name.replace(/\.[^.]+$/, ""), position: $("#newPos").value });
  };
  const unread = books.reduce((n, b) => n + (b.scenes - b.read), 0);
  $("#readAllNote").textContent = !books.length ? "Add a book to begin." : unread ? `${fmt(unread)} scenes to read, book by book in series order.` : "Everything is read. Re-import a book after editing it.";
  $("#readAllBtn").disabled = !books.length || !unread;
  $("#readAllBtn").onclick = async () => { try { await api(`/v2/series/${state.sid}/full-read`, { method: "POST" }); toast("Reading started."); lastJobStatus = "running"; } catch (e) { toast(e.message); } };
  renderSpines(); renderFixes();
}
async function upload(file, params) {
  const q = new URLSearchParams({ series_id: state.sid, filename: file.name, ...params });
  toast("Importing " + file.name + "…");
  try { const b = await api("/v2/books?" + q, { method: "POST", body: file }); state.book = b.id; toast(`Added ${b.title}: ${fmt(b.word_count)} words.`); refresh(); }
  catch (e) { toast(e.message); }
}
async function renderSpines() {
  const b = series().books.find(x => x.id === state.book);
  if (!b) { $("#spines").innerHTML = ""; $("#spinesLabel").textContent = "Chapters"; return; }
  $("#spinesLabel").textContent = `Chapters · Book ${b.position}: ${b.title}`;
  const ol = await api(`/v2/books/${b.id}/documents/outline`);
  $("#spines").innerHTML = ol.map(ch => {
    const read = ch.scenes.every(s => s.is_read);
    const h = 70 + Math.min(110, Math.round(ch.word_count / 60));
    return `<div class="spine ${read ? "read" : ""}" style="height:${h}px" title="${esc(ch.title)} · ${fmt(ch.word_count)} words · ${ch.scenes.length} scenes"><span class="t">${esc(ch.title)}</span>${String(ch.chapter).padStart(2, "0")}</div>`;
  }).join("");
}
async function renderFixes() {
  const fx = await api(`/v2/series/${state.sid}/fixes`);
  $("#fixes").innerHTML = fx.length ? fx.map(f => `<div class="item"><span class="where">${esc(f.where)}</span>
     <span style="flex:1">${f.quote ? `<q>${esc(f.quote)}</q> — ` : ""}${esc(f.note)}</span>
     <button class="btn small" data-fix="${f.id}">Fixed</button></div>`).join("") : `<p class="muted small">Nothing to fix right now.</p>`;
  $$("[data-fix]").forEach(b => b.onclick = async () => { await api(`/v2/fixes/${b.dataset.fix}/done`, { method: "POST" }); renderFixes(); });
}

// ---------- index ----------
$$("#indexTabs button").forEach(b => b.onclick = () => { state.doc = b.dataset.doc; $$("#indexTabs button").forEach(x => x.classList.toggle("active", x === b)); renderIndexBody(); });
$("#indexBook").onchange = e => { state.book = +e.target.value; renderIndexBody(); };
function renderIndex() {
  const books = series().books;
  $("#indexBook").innerHTML = books.map(b => `<option value="${b.id}" ${b.id === state.book ? "selected" : ""}>Book ${b.position}: ${esc(b.title)}</option>`).join("");
  renderIndexBody();
}
async function renderIndexBody() {
  const el = $("#indexBody");
  if (!state.book) { el.innerHTML = `<div class="empty-state">Add a book in the Catalog first.</div>`; return; }
  const d = await api(`/v2/books/${state.book}/documents/${state.doc}`);
  if (state.doc === "outline") {
    el.innerHTML = d.map(ch => `<div class="ol-ch"><h3><span class="callno">CH ${String(ch.chapter).padStart(2, "0")}</span>${esc(ch.title)}</h3>
      ${ch.scenes.map(s => `<div class="ol-sc"><div class="meta">Sc ${s.position}<br>${fmt(s.word_count)} w</div>
      <div>${s.summary ? esc(s.summary) : '<span class="muted">Not read yet</span>'}
      <div class="meta">${[s.pov && "POV: " + esc(s.pov), s.location && esc(s.location), s.time_text && esc(s.time_text)].filter(Boolean).join(" · ")}</div></div></div>`).join("")}</div>`).join("");
  } else if (state.doc === "summary") {
    el.innerHTML = `<div class="label">The whole book</div><p class="prose">${esc(d.book) || '<span class="muted">Appears after a full read.</span>'}</p><hr class="brass">` +
      d.chapters.map((c, i) => `<h3>${i + 1}. ${esc(c.title)}</h3><p class="prose">${esc(c.summary) || '<span class="muted">Not read yet.</span>'}</p>`).join("");
  } else if (state.doc === "synopsis") {
    el.innerHTML = `<div class="row" style="margin-bottom:16px"><button class="btn primary" id="synBtn">${d.text ? "Rewrite synopsis" : "Write synopsis"}</button>
      <span class="muted small">1 to 2 pages, ending included, built from the chapter summaries.</span></div><div class="prose" id="synText">${esc(d.text)}</div>`;
    $("#synBtn").onclick = async () => { $("#synBtn").disabled = true; $("#synBtn").textContent = "Writing…"; try { const r = await api(`/v2/books/${state.book}/documents/synopsis`, { method: "POST" }); $("#synText").textContent = r.text; } catch (e) { toast(e.message); } $("#synBtn").disabled = false; $("#synBtn").textContent = "Rewrite synopsis"; };
  } else if (state.doc === "characters") {
    el.innerHTML = d.length ? `<div class="cards">${d.map(c => `<div class="icard" data-ent="${c.id}"><h3>${esc(c.name)}</h3>
      <div class="f"><b>Scenes</b>${c.appearances}</div><div class="f"><b>First</b>${esc(c.first)}</div><div class="f"><b>Last</b>${esc(c.last)}</div>
      ${keyFacts(c, 4)}</div>`).join("")}</div>` : `<div class="empty-state">No characters yet. Run a full read.</div>`;
    $$("[data-ent]", el).forEach(x => x.onclick = () => openEntity(+x.dataset.ent));
  } else if (state.doc === "storylines") {
    el.innerHTML = d.length ? d.map(t => `<div style="margin-bottom:26px"><h3>${esc(t.name)} <span class="status s-${t.status === "open" ? "found" : t.status === "resolved" ? "canon" : t.status === "planned" ? "planned" : "conflict"}">${t.status}</span></h3>
      ${t.beats.map(b => `<div class="beat"><span class="where label" style="color:var(--brass)">${esc(b.where)}</span><span class="label">${b.role}</span><span>${esc(b.summary || b.note)}</span></div>`).join("")}</div>`).join("")
      : `<div class="empty-state">No storylines yet.</div>`;
  } else if (state.doc === "subplot-matrix") {
    if (!d.rows.length) { el.innerHTML = `<div class="empty-state">No storylines in this book yet.</div>`; return; }
    el.innerHTML = `<p class="muted small">Storylines down the side, chapters across. Shaded cells mean the storyline is quiet in that chapter. A long quiet run may be a dropped thread.</p>
      <div class="scroll"><table class="matrix"><tr><th class="t">Storyline</th>${d.chapters.map(c => `<th title="${esc(c.title)}">${c.position}</th>`).join("")}<th>Longest quiet</th></tr>
      ${d.rows.map(r => `<tr><th class="t">${esc(r.thread)}</th>${r.cells.map(c => c.length ? `<td class="${c.includes("resolves") ? "res" : "on"}" title="${c.join(", ")}">${c.includes("resolves") ? "✓" : c.includes("opens") ? "◆" : "•"}</td>` : `<td class="empty"></td>`).join("")}
      <td>${r.longest_quiet >= 6 ? `<span class="status s-conflict">${r.longest_quiet} ch</span>` : r.longest_quiet + " ch"}</td></tr>`).join("")}</table></div>`;
  }
}
function keyFacts(card, n) {
  const seen = new Set(); const out = [];
  for (const f of card.facts) {
    if (f.status === "retired" || seen.has(f.field)) continue;
    seen.add(f.field); out.push(`<div class="f"><b>${esc(f.field)}</b>${esc(f.value)}${f.status === "conflict" ? ' <span class="status s-conflict">conflict</span>' : f.status === "canon" ? ' <span class="status s-canon">canon</span>' : ""}</div>`);
    if (out.length >= n) break;
  }
  return out.join("");
}

// ---------- archive ----------
$$("#typeTabs button").forEach(b => b.onclick = () => { state.type = b.dataset.type; $$("#typeTabs button").forEach(x => x.classList.toggle("active", x === b)); renderArchive(); });
$("#statusSel").onchange = () => renderArchive();
let searchT; $("#search").oninput = () => { clearTimeout(searchT); searchT = setTimeout(renderArchive, 250); };
$("#newEntityBtn").onclick = async () => {
  const name = prompt("Name of the new entry:"); if (!name) return;
  const type = prompt("Type: character, place, object, rule or note", state.type || "character") || "character";
  const r = await api(`/v2/series/${state.sid}/entities`, { method: "POST", json: { name, type } });
  await renderArchive(); openEntity(r.id);
};
async function renderArchive() {
  $("#exportMd").href = `/v2/series/${state.sid}/bible/export?format=md`;
  $("#exportDocx").href = `/v2/series/${state.sid}/bible/export?format=docx`;
  const q = new URLSearchParams({ type: state.type, status: $("#statusSel").value, q: $("#search").value });
  const cards = await api(`/v2/series/${state.sid}/bible?` + q);
  state.cards = cards;
  $("#cards").innerHTML = cards.length ? cards.map(c => `<div class="icard ${c.has_conflict ? "conflict" : ""}" data-ent="${c.id}">
    <span class="type label">${c.type}</span><h3>${esc(c.name)}</h3>${keyFacts(c, 5) || '<div class="f muted">No facts yet</div>'}</div>`).join("")
    : `<div class="empty-state">The Archive is empty. Add a book in the Catalog and click Read Full Manuscript.</div>`;
  $$("#cards [data-ent]").forEach(x => x.onclick = () => openEntity(+x.dataset.ent));
}
async function openEntity(id) {
  let card = (state.cards || []).find(c => c.id === id);
  if (!card) { const all = await api(`/v2/series/${state.sid}/bible`); card = all.find(c => c.id === id); state.cards = all; }
  if (!card) return;
  const books = series().books;
  const d = $("#drawer");
  d.innerHTML = `<div class="row"><span class="label">${card.type}</span><div class="spacer"></div><button class="btn ghost" id="closeDrawer">Close</button></div>
    <h1>${esc(card.name)}</h1>${card.aliases.length ? `<p class="muted small">Also called: ${card.aliases.map(esc).join(", ")}</p>` : ""}
    <table class="facts">${card.facts.map(f => `<tr><td class="field">${esc(f.field)}</td><td>
      <div><strong>${esc(f.value)}</strong> <span class="status s-${f.status}">${f.status}</span>${f.book && f.per_book ? ` <span class="muted small">as of book ${f.book}</span>` : f.book ? ` <span class="muted small">book ${f.book}</span>` : ""}</div>
      ${f.sources.map(s => `<div class="src">${esc(s.where)}${s.quote ? ` · <q>${esc(s.quote)}</q>` : ""}</div>`).join("")}
      <div class="fact-actions">${f.status !== "canon" ? `<button data-canon="${f.id}">Make canon</button>` : ""}<button data-edit="${f.id}" data-val="${esc(f.value)}">Edit</button>${f.status !== "retired" ? `<button data-retire="${f.id}">Retire</button>` : ""}</div>
    </td></tr>`).join("")}</table>
    <hr class="brass"><h3>Add a fact</h3>
    <div class="row"><input type="text" id="nfField" placeholder="field (e.g. eyes)" style="width:150px"><input type="text" id="nfValue" placeholder="value" style="flex:1">
    <select class="field" id="nfStatus"><option value="canon">canon</option><option value="planned">planned (future book)</option></select>
    <select class="field" id="nfBook"><option value="">all books</option>${books.map(b => `<option value="${b.id}">as of book ${b.position}</option>`).join("")}</select>
    <button class="btn primary" id="nfAdd">Add</button></div><div id="disagree"></div>`;
  d.classList.add("on");
  $("#closeDrawer").onclick = () => d.classList.remove("on");
  const after = async r => {
    if (r && r.disagree && r.disagree.length) toast(`${r.disagree.length} place(s) in the text now disagree. They're listed in the Archive card.`);
    await renderArchive(); openEntity(id);
    if (r && r.disagree && r.disagree.length) $("#disagree").innerHTML = `<div class="preview"><strong>These places in the text now disagree:</strong><ul>${r.disagree.map(s => `<li>${esc(s.where)} ${s.quote ? `<q>${esc(s.quote)}</q>` : ""}</li>`).join("")}</ul></div>`;
  };
  $$("[data-canon]", d).forEach(b => b.onclick = async () => after(await api(`/v2/facts/${b.dataset.canon}`, { method: "PATCH", json: { status: "canon" } })));
  $$("[data-retire]", d).forEach(b => b.onclick = async () => after(await api(`/v2/facts/${b.dataset.retire}`, { method: "PATCH", json: { status: "retired" } })));
  $$("[data-edit]", d).forEach(b => b.onclick = async () => { const v = prompt("New value (it becomes canon):", b.dataset.val); if (v === null) return; after(await api(`/v2/facts/${b.dataset.edit}`, { method: "PATCH", json: { value: v, status: "canon" } })); });
  $("#nfAdd").onclick = async () => {
    const field = $("#nfField").value.trim(), value = $("#nfValue").value.trim(); if (!field || !value) return toast("Give a field and a value.");
    await api(`/v2/entities/${id}/facts`, { method: "POST", json: { field, value, status: $("#nfStatus").value, book_id: $("#nfBook").value ? +$("#nfBook").value : null } });
    after();
  };
}

// ---------- reference desk ----------
async function renderDesk() {
  state.questions = await api(`/v2/series/${state.sid}/questions?status=open`);
  if (state.qIndex >= state.questions.length) state.qIndex = 0;
  state.choice = null;
  $("#queue").innerHTML = state.questions.length ? state.questions.map((q, i) => `<div class="qi ${i === state.qIndex ? "cur" : ""}" data-i="${i}">${["", "Conflict", "Check", "Gap"][q.priority] || ""} · ${esc(q.text.slice(0, 90))}${q.text.length > 90 ? "…" : ""}</div>`).join("") : `<p class="muted small">No open questions.</p>`;
  $$("#queue .qi").forEach(x => x.onclick = () => { state.qIndex = +x.dataset.i; renderDesk(); });
  const q = state.questions[state.qIndex];
  if (!q) { $("#qcard").innerHTML = `<div class="empty-state">Nothing waiting at the desk. Questions appear after a full read.</div>`; return; }
  const p = q.payload;
  const passages = q.kind === "conflict" ? p.options.flatMap(o => o.sources.filter(s => s.quote).map(s => `<div class="passage"><span class="w">${esc(s.where)}</span>${esc(s.quote)}</div>`)).join("")
    : p.where ? `<div class="passage"><span class="w">${esc(p.where)}</span></div>` : "";
  $("#qcard").innerHTML = `<div class="qcard"><div class="label">Question ${state.qIndex + 1} of ${state.questions.length} · ${q.kind.replace("_", " ")}</div>
    <div class="qtext">${esc(q.text)}</div>${passages}
    ${p.choices ? `<div class="choices">${p.choices.map((c, i) => `<button data-c="${i}">${esc(c)}</button>`).join("")}</div>` : ""}
    <textarea id="qText" placeholder="${p.choices ? "Or type your own answer" : "Type your answer"}"></textarea>
    <div class="row" style="margin-top:12px"><button class="btn primary" id="qAnswer">Answer</button>
      <button class="btn" id="qSkip">Skip for now</button><button class="btn" id="qIntent">It's on purpose</button></div>
    <div id="qPreview"></div></div>`;
  $$("[data-c]").forEach(b => b.onclick = () => { state.choice = +b.dataset.c; $$("[data-c]").forEach(x => x.classList.toggle("sel", x === b)); });
  const send = async (action) => {
    const payload = { action, choice: state.choice, text: $("#qText").value || null };
    if (action === "answer" && payload.choice === null && !payload.text) return toast("Pick a choice or type an answer.");
    try {
      const prev = await api(`/v2/questions/${q.id}/answer`, { method: "POST", json: payload });
      $("#qPreview").innerHTML = `<div class="preview"><strong>This will change:</strong><ul>${prev.changes.map(c => `<li>${esc(c)}</li>`).join("")}</ul>
        <div class="row"><button class="btn primary" id="qConfirm">Confirm</button><button class="btn ghost" id="qCancel">Cancel</button></div></div>`;
      $("#qCancel").onclick = () => $("#qPreview").innerHTML = "";
      $("#qConfirm").onclick = async () => { await api(`/v2/questions/${q.id}/answer`, { method: "POST", json: { ...payload, confirm: true } }); toast("Saved to the Story Bible."); await loadSeries(); renderDesk(); };
    } catch (e) { toast(e.message); }
  };
  $("#qAnswer").onclick = () => send("answer"); $("#qSkip").onclick = () => send("skip"); $("#qIntent").onclick = () => send("intentional");
}
$("#askBtn").onclick = async () => {
  const question = $("#askBox").value.trim(); if (!question) return;
  $("#askOut").innerHTML = `<div class="answer muted">Looking it up…</div>`;
  try { const r = await api(`/v2/series/${state.sid}/ask`, { method: "POST", json: { question } }); $("#askOut").innerHTML = `<div class="answer">${esc(r.answer)}</div>`; }
  catch (e) { $("#askOut").innerHTML = `<div class="answer">${esc(e.message)}</div>`; }
};
$("#askBox").onkeydown = e => { if (e.key === "Enter") $("#askBtn").click(); };

// ---------- settings ----------
async function renderSettings() {
  const s = await api("/v2/settings");
  $("#setEngine").value = s.engine; $("#setModel").value = s.model; $("#setUrl").value = s.ollama_url;
  $("#setCtx").value = s.num_ctx; $("#setScene").value = s.max_scene_words;
}
$("#saveSettings").onclick = async () => {
  await api("/v2/settings", { method: "PUT", json: { engine: $("#setEngine").value, model: $("#setModel").value.trim(), ollama_url: $("#setUrl").value.trim(), num_ctx: +$("#setCtx").value, max_scene_words: +$("#setScene").value } });
  await checkHealth(); $("#settingsMsg").textContent = $("#healthMsg").textContent + " — " + ($("#health").title || "");
};

// ---------- start ----------
refresh(); checkHealth(); setInterval(checkHealth, 15000); pollJob();
