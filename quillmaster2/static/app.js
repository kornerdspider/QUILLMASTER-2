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
  ({ catalog: renderCatalog, index: renderIndex, archive: renderArchive, desk: renderDesk, settings: renderSettings,
     passes: renderPasses, reading: renderReading, lexicon: () => {} })[state.page]?.();
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
      toast(j.status === "error" ? "Stopped: " + j.message : j.status === "paused" ? "Paused. Start it again to pick up where it stopped." : (j.message || "Finished."));
      PASSINFO = null; refresh();
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
      ${b.findings ? `<div class="stats" style="margin-top:4px">${fmt(b.findings)} open findings</div>` : ""}
      <div class="row" style="margin-top:12px">
        <button class="btn small" data-open="${b.id}">Open in Reading Room</button>
        <select class="field small" data-export="${b.id}" style="font-size:13px;padding:6px"><option value="">Download…</option><option value="docx">Word (.docx)</option><option value="odt">LibreOffice (.odt)</option><option value="md">Markdown (.md)</option><option value="txt">Plain text (.txt)</option></select>
      </div>
      <div class="row" style="margin-top:8px">
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
  $$("[data-open]").forEach(b => b.onclick = () => { state.book = +b.dataset.open; state.chapter = null; go("reading"); });
  $$("[data-export]").forEach(sel => sel.onchange = () => { if (sel.value) { location.href = `/v2/books/${sel.dataset.export}/export?format=${sel.value}`; sel.value = ""; } });
  $$("[data-del]").forEach(b => b.onclick = async () => { if (!confirm("Remove this book and everything read from it? The file on your computer is not touched.")) return; await api(`/v2/books/${b.dataset.del}`, { method: "DELETE" }); refresh(); });
  $$("[data-reimport]").forEach(inp => inp.onchange = () => upload(inp.files[0], { book_id: inp.dataset.reimport, position: books.find(b => b.id == inp.dataset.reimport).position }));
  $("#addBookBtn").onclick = () => {
    const f = $("#newFile").files[0]; if (!f) return toast("Choose a manuscript file first.");
    upload(f, { title: $("#newTitle").value || f.name.replace(/\.[^.]+$/, ""), position: $("#newPos").value });
  };
  const unread = books.reduce((n, b) => n + (b.scenes - b.read), 0);
  if (!books.length) {
    $("#shelf").insertAdjacentHTML("afterbegin", `<div class="book add" style="border-style:solid"><div class="callno">NEW HERE?</div><h3>Try the sample trilogy</h3>
      <p class="small">Two short sample books with planted mistakes, so you can see how reading, the Story Bible, the Reference Desk and the passes work.</p>
      <button class="btn primary" id="samplesBtn">Load the samples</button></div>`);
    $("#samplesBtn").onclick = async () => { await api(`/v2/series/${state.sid}/samples`, { method: "POST" }); toast("Sample books added. Click Read Full Manuscript."); refresh(); };
  }
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
  const info = await api("/v2/passes");
  $("#setFramework").innerHTML = info.frameworks.map(f => `<option ${f === s.framework ? "selected" : ""}>${esc(f)}</option>`).join("");
  $("#setEngine").value = s.engine; $("#setModel").value = s.model; $("#setUrl").value = s.ollama_url;
  $("#setCtx").value = s.num_ctx; $("#setScene").value = s.max_scene_words; $("#setLT").value = s.languagetool_url || "";
  $("#setEmbed").value = s.embed_model || ""; $("#setMW").value = s.mw_key || ""; $("#setMWT").value = s.mw_thesaurus_key || "";
  $("#setBackup").value = s.ask_backup ? "1" : "0";
  renderModels(s);
}
async function renderModels(s) {
  const m = await api("/v2/ollama/models");
  const box = $("#modelsBox");
  if (!m.running) {
    box.innerHTML = `<p>Ollama isn't running on this computer. Install it from <a href="https://ollama.com/download" target="_blank">ollama.com/download</a>, open it, then come back here and click <b>Check again</b>.</p><button class="btn" id="mCheck">Check again</button>`;
    $("#mCheck").onclick = () => renderModels(s); return;
  }
  const has = n => m.models.some(x => x === n || x.split(":")[0] === n || x === n + ":latest");
  box.innerHTML = `<p>Installed: ${m.models.length ? m.models.map(esc).join(", ") : "none yet"}.</p>
    ${[["Main model", s.model], ["Embedding model (for spotting repeated scenes)", s.embed_model]].map(([label, n]) =>
      `<div class="row" style="margin:6px 0"><span style="min-width:330px">${label}: <b>${esc(n)}</b></span>${has(n) ? '<span class="chip ok">installed</span>' : `<button class="btn small" data-pull="${esc(n)}">Download it</button>`}</div>`).join("")}
    <p class="muted">Downloads can be several gigabytes. The progress bar shows at the top.</p>`;
  $$("[data-pull]").forEach(b => b.onclick = async () => { try { await api("/v2/ollama/pull", { method: "POST", json: { model: b.dataset.pull } }); lastJobStatus = "running"; toast("Download started."); } catch (e) { toast(e.message); } });
}
$("#quitBtn").onclick = async () => { if (!confirm("Quit Quill Master 2?")) return; await api("/v2/quit", { method: "POST" }).catch(() => {}); document.body.innerHTML = '<p style="padding:40px;font-size:18px">Quill Master 2 has stopped. You can close this tab.</p>'; };
$("#saveSettings").onclick = async () => {
  await api("/v2/settings", { method: "PUT", json: { engine: $("#setEngine").value, model: $("#setModel").value.trim(), ollama_url: $("#setUrl").value.trim(), num_ctx: +$("#setCtx").value, max_scene_words: +$("#setScene").value,
    framework: $("#setFramework").value, languagetool_url: $("#setLT").value.trim(), embed_model: $("#setEmbed").value.trim(), mw_key: $("#setMW").value.trim(), mw_thesaurus_key: $("#setMWT").value.trim(), ask_backup: $("#setBackup").value === "1" } });
  await checkHealth(); $("#settingsMsg").textContent = "Saved. " + ($("#health").title || ""); renderSettings();
};


// ---------- shared: agents, modal, backup ----------
const AGENT_CLASS = { "Story Editor": "ag-story", "Archivist": "ag-arch", "Character Editor": "ag-char", "Concordance": "ag-conc", "Copy Editor": "ag-copy", "Reference Librarian": "ag-ref" };
let PASSINFO = null;
async function passInfo() { if (!PASSINFO) PASSINFO = await api("/v2/passes"); return PASSINFO; }
const passName = k => (PASSINFO?.passes.find(p => p.key === k) || { name: k }).name;
function modal(html) { $("#modal").innerHTML = html; $("#modalBg").classList.add("on"); }
function closeModal() { $("#modalBg").classList.remove("on"); }
$("#modalBg").onclick = e => { if (e.target.id === "modalBg") closeModal(); };
function backedUp(bookId) { try { return sessionStorage["qmBackup" + bookId] === "1"; } catch (e) { return false; } }
function markBackedUp(bookId) { try { sessionStorage["qmBackup" + bookId] = "1"; } catch (e) {} }
async function ensureBackup(bookId) {
  const cfg = await api("/v2/settings");
  if (!cfg.ask_backup || backedUp(bookId)) return true;
  return new Promise(resolve => {
    modal(`<h2>Make a backup first?</h2><p>You're about to change the manuscript. A backup saves a copy of the book as it is right now, so you can always go back.</p>
      <div class="row" style="margin-top:18px"><button class="btn primary" id="bkYes">Make a backup and continue</button><button class="btn" id="bkNo">Continue without</button><button class="btn ghost" id="bkCancel">Cancel</button></div>`);
    $("#bkYes").onclick = async () => {
      const r = await api(`/v2/books/${bookId}/backup`, { method: "POST" });
      markBackedUp(bookId); closeModal();
      toast(`Backup saved: ${r.filename}`);
      modal(`<h2>Backup saved</h2><p><b>${esc(r.filename)}</b> is in this folder on your computer:</p><p><code>${esc(r.folder)}</code></p><div class="row"><a class="btn" href="${r.url}">Download a copy too</a><button class="btn primary" id="bkOk">Continue</button></div>`);
      $("#bkOk").onclick = () => { closeModal(); resolve(true); };
    };
    $("#bkNo").onclick = () => { markBackedUp(bookId); closeModal(); resolve(true); };
    $("#bkCancel").onclick = () => { closeModal(); resolve(false); };
  });
}
async function startJob(promise, msg) { try { await promise; lastJobStatus = "running"; toast(msg); } catch (e) { toast(e.message); } }

// ---------- passes ----------
state.passSel = new Set(["line_edit", "grammar", "punctuation"]);
async function renderPasses() {
  const info = await passInfo(), s = series(), cfg = await api("/v2/settings");
  if (!s.books.length) { $("#passList").innerHTML = `<div class="empty-state">Add a book in the Catalog first.</div>`; return; }
  $("#passBook").innerHTML = s.books.map(b => `<option value="${b.id}" ${b.id === state.book ? "selected" : ""}>Book ${b.position}: ${esc(b.title)}</option>`).join("");
  $("#passBook").onchange = e => { state.book = +e.target.value; renderPasses(); };
  $("#passFramework").innerHTML = info.frameworks.map(f => `<option ${f === cfg.framework ? "selected" : ""}>${esc(f)}</option>`).join("");
  $("#passFramework").onchange = e => api("/v2/settings", { method: "PUT", json: { framework: e.target.value } }).then(() => toast("Framework saved."));
  const ol = await api(`/v2/books/${state.book}/documents/outline`);
  $("#passChapters").innerHTML = ol.map(c => `<option value="${c.id}">${c.chapter}. ${esc(c.title)}</option>`).join("");
  $("#passScope").onchange = () => { const pick = $("#passScope").value === "pick"; $("#passChapters").style.display = pick ? "" : "none"; $("#passChapters").size = pick ? Math.min(8, ol.length) : 1; };
  state._outline = ol;
  $("#presets").innerHTML = Object.entries(info.presets).map(([k, p]) => `<button class="btn" data-preset="${k}" title="${esc(p.for)}">${esc(p.name)} <span class="muted small">· ${esc(p.for)}</span></button>`).join("");
  $$("[data-preset]").forEach(b => b.onclick = () => { state.passSel = new Set(info.presets[b.dataset.preset].passes); drawPassList(info); });
  drawPassList(info);
  $("#runPasses").onclick = async () => {
    const chapters = await chosenChapterIds();
    await startJob(api(`/v2/books/${state.book}/passes`, { method: "POST", json: { passes: [...state.passSel], chapters } }), "Passes started.");
  };
  $("#passNote").textContent = cfg.engine === "demo" ? "Demo mode: passes that need the AI are partly skipped." : "";
  renderPassResults(info);
}
async function chosenChapterIds() {
  if ($("#passScope").value !== "pick") return null;
  const ids = [...$("#passChapters").selectedOptions].map(o => +o.value);
  return ids.length ? ids : null;
}
function drawPassList(info) {
  $("#passList").innerHTML = info.passes.map((p, i) => `<label><input type="checkbox" data-pass="${p.key}" ${state.passSel.has(p.key) ? "checked" : ""}>
    <span class="n">${i + 1}</span><span><b>${esc(p.name)}</b> <span class="muted small">· ${esc(p.agent)} · ${p.scope === "book" ? "whole book" : p.scope === "chapter" ? "by chapter" : "book or chapter"}</span><br><span class="small">${esc(p.checks)}</span></span></label>`).join("");
  $$("[data-pass]").forEach(c => c.onchange = () => { c.checked ? state.passSel.add(c.dataset.pass) : state.passSel.delete(c.dataset.pass); });
}
async function renderPassResults(info) {
  const sum = await api(`/v2/books/${state.book}/findings/summary`);
  $("#passSummary").innerHTML = `<tr><th>Pass</th><th>Open</th><th>Accepted</th><th>Rejected</th><th>Last run</th><th>Notes</th></tr>` +
    info.passes.map(p => { const c = sum.counts[p.key] || {}, r = sum.runs[p.key];
      return `<tr><td>${esc(p.name)}</td><td>${c.open || 0}</td><td>${c.accepted || 0}</td><td>${c.rejected || 0}</td><td class="muted">${r ? new Date(r.finished * 1000).toLocaleString() : "never"}</td><td class="muted small">${esc(r?.note || "")}</td></tr>`; }).join("");
  $("#passFilter").innerHTML = `<option value="">All passes</option>` + info.passes.map(p => `<option value="${p.key}">${esc(p.name)}</option>`).join("");
  $("#passFilter").value = state.passFilter || "";
  $("#passFilter").onchange = e => { state.passFilter = e.target.value; renderFindList(); };
  renderFindList();
}
async function renderFindList() {
  const list = await api(`/v2/books/${state.book}/findings?status=open${state.passFilter ? "&pass=" + state.passFilter : ""}`);
  $("#findCount").textContent = `${list.length}${list.length >= 500 ? "+" : ""} open`;
  $("#findList").innerHTML = list.length ? list.slice(0, 300).map(f => `<div class="item list" style="border:0"><div class="item">
    <span class="sevdot ${f.severity}"></span><span class="where">${f.cp ? `Ch ${f.cp}${f.sp ? " · Sc " + f.sp : ""}` : "Whole book"}</span>
    <span style="flex:1"><span class="label">${esc(passName(f.pass))}</span> ${esc(f.message)}${f.quote ? `<br><q class="small muted">${esc(f.quote.slice(0, 140))}</q>` : ""}</span>
    ${f.chapter_id ? `<button class="btn small" data-goto="${f.chapter_id}" data-fid="${f.id}">Open</button>` : ""}</div></div>`).join("")
    : `<p class="muted">No open findings. Run some passes above.</p>`;
  $$("[data-goto]").forEach(b => b.onclick = () => { state.chapter = +b.dataset.goto; state.focusFinding = +b.dataset.fid; state.wb = "line"; go("reading"); });
}

// ---------- reading room ----------
state.wb = "line";
$$("#rrTabs button").forEach(b => b.onclick = () => { state.wb = b.dataset.wb; $$("#rrTabs button").forEach(x => x.classList.toggle("active", x === b)); renderReading(); });
$("#rrFocus").onclick = () => document.body.classList.toggle("focus");
document.addEventListener("keydown", e => { if (e.key === "Escape") { document.body.classList.remove("focus"); closeModal(); $("#drawer").classList.remove("on"); } });
$("#rrFilter").onchange = e => { state.rrFilter = e.target.value; drawFindings(); };
async function renderReading() {
  await passInfo();
  const s = series();
  if (!s.books.length) { $("#rrMain").innerHTML = `<div class="empty-state">Add a book in the Catalog first.</div>`; return; }
  if (!state.book) state.book = s.books[0].id;
  $("#rrBook").innerHTML = s.books.map(b => `<option value="${b.id}" ${b.id === state.book ? "selected" : ""}>Book ${b.position}: ${esc(b.title)}</option>`).join("");
  $("#rrBook").onchange = e => { state.book = +e.target.value; state.chapter = null; renderReading(); };
  if (!state.chapter) {
    const ol = await api(`/v2/books/${state.book}/documents/outline`);
    if (!ol.length) return;
    state.chapter = ol[0].id;
  }
  const v = await api(`/v2/chapters/${state.chapter}`);
  if (v.book.id !== state.book) { state.book = v.book.id; $("#rrBook").value = v.book.id; }
  state.view = v;
  $("#rrChapter").innerHTML = v.chapters.map(c => `<option value="${c.id}" ${c.id === v.chapter.id ? "selected" : ""}>${c.position}. ${esc(c.title)}</option>`).join("");
  $("#rrChapter").onchange = e => { state.chapter = +e.target.value; renderReading(); };
  const idx = v.chapters.findIndex(c => c.id === v.chapter.id);
  $("#rrPrev").disabled = idx <= 0; $("#rrNext").disabled = idx >= v.chapters.length - 1;
  $("#rrPrev").onclick = () => { state.chapter = v.chapters[idx - 1].id; renderReading(); };
  $("#rrNext").onclick = () => { state.chapter = v.chapters[idx + 1].id; renderReading(); };
  $("#rrPolish").onclick = () => startJob(api(`/v2/books/${state.book}/passes`, { method: "POST", json: { preset: "polish", chapters: [v.chapter.id] } }), "Polishing this chapter…");
  $("#rrCards").innerHTML = v.cards.length ? v.cards.map(c => `<div class="mini-card" data-ent="${c.id}"><b>${esc(c.name)}</b>${keyFacts(c, 3).replace(/class="f"/g, 'class="f"')}</div>`).join("") : `<p class="muted small">Run a full read to see who and what appears here.</p>`;
  $$("#rrCards [data-ent]").forEach(x => x.onclick = () => openEntity(+x.dataset.ent));
  const passes = [...new Set(v.findings.map(f => f.pass))];
  $("#rrFilter").innerHTML = `<option value="">All passes (${v.findings.length})</option>` + passes.map(p => `<option value="${p}" ${p === state.rrFilter ? "selected" : ""}>${esc(passName(p))} (${v.findings.filter(f => f.pass === p).length})</option>`).join("");
  if (state.wb === "line") drawText(); else if (state.wb === "beats") drawBeats(); else if (state.wb === "dialogue") drawDialogue(); else drawSetting();
  drawFindings();
}
function visibleFindings() { return state.view.findings.filter(f => !state.rrFilter || f.pass === state.rrFilter); }
function markup(text, fs) {
  const spans = fs.filter(f => f.start !== null && f.end > f.start).sort((a, b) => a.start - b.start || b.end - a.end);
  let out = "", pos = 0;
  const plain = t => esc(t).replace(/\*/g, '<span class="mk">*</span>');
  for (const f of spans) {
    if (f.start < pos) continue;
    out += plain(text.slice(pos, f.start));
    out += `<mark data-f="${f.id}" class="${AGENT_CLASS[f.agent] || ""} sev-${f.severity}" title="${esc(passName(f.pass))}: ${esc(f.message)}">${plain(text.slice(f.start, f.end))}</mark>`;
    pos = f.end;
  }
  return out + plain(text.slice(pos));
}
function drawText() {
  const v = state.view, fs = visibleFindings();
  $("#rrMain").innerHTML = v.scenes.map(sc => `<div class="scene" data-scene="${sc.id}">
    <div class="scene-head"><span class="callno">SC ${sc.position}</span><span>${fmt(sc.word_count)} words</span>${sc.beat ? `<span class="chip ok">${esc(sc.beat)}</span>` : ""}${sc.is_read ? "" : '<span class="chip no">changed: re-read in the Catalog</span>'}
      <span class="spacer"></span><button data-edit-scene="${sc.id}">Edit text</button><button data-condense="${sc.id}">Condense</button></div>
    <div class="stext" data-text="${sc.id}">${markup(sc.text, fs.filter(f => f.scene_id === sc.id))}</div></div>`).join("");
  $$("#rrMain mark").forEach(m => m.onclick = () => focusFinding(+m.dataset.f));
  $$("[data-edit-scene]").forEach(b => b.onclick = () => editScene(+b.dataset.editScene));
  $$("[data-condense]").forEach(b => b.onclick = () => condenseScene(+b.dataset.condense));
  if (state.focusFinding) { const id = state.focusFinding; state.focusFinding = null; setTimeout(() => focusFinding(id), 50); }
}
function drawFindings() {
  const v = state.view; if (!v) return;
  const fs = visibleFindings();
  const card = f => `<div class="fcard ${AGENT_CLASS[f.agent] || ""}" data-fc="${f.id}">
    <div class="who"><span class="sevdot ${f.severity}"></span>${esc(f.agent || "")} · ${esc(passName(f.pass))}</div>
    <div class="msg">${esc(f.message)}</div>${f.quote ? `<q>${esc(f.quote.slice(0, 160))}</q>` : ""}
    ${f.suggestion !== null && f.suggestion !== undefined ? `<input type="text" value="${esc(f.suggestion)}" data-sugg="${f.id}" placeholder="(remove these words)" title="Edit the replacement before accepting">` : ""}
    <div class="acts" style="margin-top:6px">${f.suggestion !== null && f.suggestion !== undefined && f.start !== null
      ? `<button class="btn primary" data-acc="${f.id}">Accept</button>` : `<button class="btn" data-done="${f.id}">Done</button>`}
      <button class="btn ghost" data-rej="${f.id}">Reject</button></div></div>`;
  const book = v.book_findings.filter(f => !state.rrFilter || f.pass === state.rrFilter);
  $("#rrFindings").innerHTML = (book.length ? `<div class="label" style="margin:4px 0 8px">Whole book</div>${book.map(card).join("")}<div class="label" style="margin:14px 0 8px">This chapter</div>` : "") +
    (fs.length ? fs.map(card).join("") : `<p class="muted small">No open findings here. Click <b>Polish this chapter</b>, or run passes from the Passes page.</p>`);
  $$("[data-fc]").forEach(c => c.onclick = e => { if (!e.target.closest("button,input")) focusFinding(+c.dataset.fc, true); });
  $$("[data-acc]").forEach(b => b.onclick = async () => {
    const f = v.findings.find(x => x.id === +b.dataset.acc);
    if (!(await ensureBackup(v.book.id))) return;
    const inp = $(`[data-sugg="${f.id}"]`);
    try { await api(`/v2/findings/${f.id}/accept`, { method: "POST", json: { replacement: inp ? inp.value : f.suggestion } }); toast("Changed."); renderReading(); } catch (e) { toast(e.message); renderReading(); }
  });
  $$("[data-done]").forEach(b => b.onclick = async () => { await api(`/v2/findings/${b.dataset.done}/accept`, { method: "POST", json: {} }); renderReading(); });
  $$("[data-rej]").forEach(b => b.onclick = async () => { await api(`/v2/findings/${b.dataset.rej}/reject`, { method: "POST" }); renderReading(); });
}
function focusFinding(id, scroll) {
  $$("#rrMain mark.on, .fcard.on").forEach(x => x.classList.remove("on"));
  const m = $(`#rrMain mark[data-f="${id}"]`), c = $(`[data-fc="${id}"]`);
  if (m) { m.classList.add("on"); if (scroll !== false) m.scrollIntoView({ block: "center", behavior: "smooth" }); }
  if (c) { c.classList.add("on"); c.scrollIntoView({ block: "nearest", behavior: "smooth" }); }
}
async function editScene(id) {
  const sc = state.view.scenes.find(x => x.id === id);
  modal(`<h2>Edit scene ${sc.position}</h2><p class="muted small">Italics are written as *word*. Saving marks this scene for re-reading and clears its open findings.</p>
    <textarea id="sceneText">${esc(sc.text)}</textarea><div class="row" style="margin-top:12px"><button class="btn primary" id="sceneSave">Save</button><button class="btn ghost" id="sceneCancel">Cancel</button></div>`);
  $("#sceneCancel").onclick = closeModal;
  $("#sceneSave").onclick = async () => {
    const text = $("#sceneText").value; closeModal();
    if (!(await ensureBackup(state.view.book.id))) return;
    await api(`/v2/scenes/${id}`, { method: "PUT", json: { text } }); toast("Scene saved."); renderReading();
  };
}
async function condenseScene(id) {
  const sc = state.view.scenes.find(x => x.id === id);
  modal(`<h2>Condense scene ${sc.position}</h2><p class="muted small">The Abridger cuts padding and repetition and keeps every plot point${sc.beat ? ` and the “${esc(sc.beat)}” beat` : ""}.</p>
    <div class="row"><label>Cut about <input type="number" id="cdPct" value="15" min="5" max="70" style="width:70px">%</label>
    <select class="field" id="cdMode"><option value="truncate">Shorten</option><option value="summary">Summarize</option></select><button class="btn primary" id="cdGo">Show me</button></div>
    <div id="cdOut" style="margin-top:14px"></div>`);
  $("#cdGo").onclick = async () => {
    $("#cdOut").innerHTML = `<p class="muted">Working… (this can take a minute)</p>`;
    try {
      const r = await api(`/v2/scenes/${id}/condense`, { method: "POST", json: { mode: $("#cdMode").value, target_pct: +$("#cdPct").value } });
      $("#cdOut").innerHTML = `<p class="small muted">${fmt(r.before_words)} words → ${fmt(r.after_words)} words</p><textarea id="cdText">${esc(r.text)}</textarea>
        <div class="row" style="margin-top:10px"><button class="btn primary" id="cdApply">Replace the scene with this</button><button class="btn ghost" id="cdClose">Close</button></div>`;
      $("#cdClose").onclick = closeModal;
      $("#cdApply").onclick = async () => { const text = $("#cdText").value; closeModal(); if (!(await ensureBackup(state.view.book.id))) return; await api(`/v2/scenes/${id}`, { method: "PUT", json: { text } }); toast("Scene replaced."); renderReading(); };
    } catch (e) { $("#cdOut").innerHTML = `<p>${esc(e.message)}</p>`; }
  };
}
function drawBeats() {
  const v = state.view;
  const structure = v.findings.filter(f => f.pass === "structure" || f.pass === "pacing");
  $("#rrMain").innerHTML = `<p class="muted small">Story beats come from the Structure pass. Goal, conflict and outcome come from <b>Read scene beats</b>.</p>
    <div class="strip">${v.scenes.map(s => `<div class="cell ${s.beat ? "beat" : ""}">Sc ${s.position}<br>${esc(s.beat || "—")}${s.tension ? `<br>tension ${"●".repeat(s.tension)}` : ""}</div>`).join("")}</div>
    <div class="row" style="margin-bottom:14px"><button class="btn primary" id="beatsGo">Read scene beats (AI)</button><button class="btn" id="structGo">Run Structure pass</button></div>
    <table class="tbl"><tr><th>Scene</th><th>Goal</th><th>Conflict</th><th>Outcome</th><th>Turns?</th></tr>
    ${v.scenes.map(s => `<tr><td>Sc ${s.position}</td><td>${esc(s.goal || "")}</td><td>${esc(s.conflict || "")}</td><td>${esc(s.outcome || "")}</td><td>${s.moves_plot === null ? "" : s.moves_plot ? '<span class="chip ok">yes</span>' : '<span class="chip no">no</span>'}</td></tr>`).join("")}</table>
    ${structure.length ? `<h3 style="margin-top:22px">Beat and pacing notes</h3>${structure.map(f => `<p>• ${esc(f.message)}</p>`).join("")}` : ""}`;
  $("#beatsGo").onclick = () => startJob(api(`/v2/chapters/${v.chapter.id}/beats`, { method: "POST" }), "Reading scene beats…");
  $("#structGo").onclick = () => startJob(api(`/v2/books/${state.book}/passes`, { method: "POST", json: { passes: ["structure", "pacing"] } }), "Structure pass started (whole book).");
}
async function drawDialogue() {
  const v = state.view, d = await api(`/v2/chapters/${v.chapter.id}/dialogue`);
  const st = x => x ? `${x.avg_len.toFixed(1)} w/sentence · ${(x.contractions * 100).toFixed(0)}% contractions` : "—";
  $("#rrMain").innerHTML = `<div class="row" style="margin-bottom:14px"><button class="btn primary" id="voiceGo">Check voices in this chapter</button><span class="muted small">${d.unattributed} lines couldn't be matched to a speaker.</span></div>
    <table class="tbl"><tr><th>Speaker</th><th>Lines</th><th>Words</th><th>In this chapter</th><th>Across the book</th></tr>
    ${d.speakers.map(s => `<tr><td><b>${esc(s.name)}</b></td><td>${s.lines}</td><td>${fmt(s.words)}</td><td>${st(s.here)}</td><td class="muted">${st(s.book)}</td></tr>`).join("") || `<tr><td colspan="5" class="muted">No attributed dialogue yet.</td></tr>`}</table>
    ${d.flagged.length ? `<h3 style="margin-top:22px">Lines that may not sound like the character</h3>${d.flagged.map(f => `<p>• <q>${esc(f.quote)}</q> — ${esc(f.message)}</p>`).join("")}` : ""}
    <h3 style="margin-top:22px">All dialogue</h3>${d.lines.map(l => `<div class="beat" style="grid-template-columns:110px 1fr"><span class="label">${esc(l.speaker || "?")}</span><span>${esc(l.text)}</span></div>`).join("")}`;
  $("#voiceGo").onclick = () => startJob(api(`/v2/books/${state.book}/passes`, { method: "POST", json: { passes: ["characterization"], chapters: [v.chapter.id] } }), "Checking voices…");
}
async function drawSetting() {
  const v = state.view, rows = await api(`/v2/chapters/${v.chapter.id}/setting`);
  const max = Math.max(1, ...rows.flatMap(r => Object.values(r.senses)));
  $("#rrMain").innerHTML = rows.map(r => `<div class="scene"><div class="scene-head"><span class="callno">SC ${r.position}</span><span>${fmt(r.words)} words</span>
      <span class="chip ${r.grounded_place ? "ok" : "no"}">${r.grounded_place ? "place set" : "no place"}</span><span class="chip ${r.grounded_time ? "ok" : "no"}">${r.grounded_time ? "time set" : "no time"}</span>
      ${r.location ? `<span>${esc(r.location)}</span>` : ""}</div>
    <div class="row" style="align-items:flex-end;gap:28px"><div><div class="sense">${Object.values(r.senses).map(n => `<i style="height:${Math.max(2, 44 * n / max)}px"></i>`).join("")}</div>
      <div class="sense-l">${Object.keys(r.senses).map(k => `<span title="${k}">${k.slice(0, 2)}</span>`).join("")}</div></div>
      <div style="flex:1">${r.notes.length ? r.notes.map(n => `<p class="small">• ${esc(n)}</p>`).join("") : '<p class="small muted">Well grounded.</p>'}</div></div></div>`).join("");
}
// select text in the Reading Room -> look up or replace
document.addEventListener("mouseup", () => {
  const bar = $("#selbar"); const sel = window.getSelection();
  if (state.page !== "reading" || state.wb !== "line" || !sel.rangeCount || sel.isCollapsed) { bar.style.display = "none"; return; }
  const r = sel.getRangeAt(0); const box = r.startContainer.parentElement?.closest("[data-text]");
  if (!box || box !== r.endContainer.parentElement?.closest("[data-text]")) { bar.style.display = "none"; return; }
  const pre = document.createRange(); pre.setStart(box, 0); pre.setEnd(r.startContainer, r.startOffset);
  const start = pre.toString().length, end = start + r.toString().length;
  state.selection = { scene: +box.dataset.text, start, end, text: r.toString() };
  const rect = r.getBoundingClientRect();
  bar.style.display = "flex"; bar.style.left = rect.left + "px"; bar.style.top = (rect.top - 44) + "px";
});
$("#selLook").onclick = () => { const w = state.selection.text.trim(); $("#selbar").style.display = "none"; go("lexicon"); $("#lexWord").value = w; lookUp(w, true); };
$("#selReplace").onclick = () => {
  const sel = state.selection; $("#selbar").style.display = "none";
  modal(`<h2>Replace</h2><p><q>${esc(sel.text)}</q></p><input type="text" id="repText" value="${esc(sel.text)}" style="width:100%"><div class="row" style="margin-top:12px"><button class="btn primary" id="repGo">Replace</button><button class="btn ghost" id="repCancel">Cancel</button></div>`);
  $("#repCancel").onclick = closeModal;
  $("#repGo").onclick = async () => { const text = $("#repText").value; closeModal(); if (!(await ensureBackup(state.view.book.id))) return;
    try { await api(`/v2/scenes/${sel.scene}/replace`, { method: "POST", json: { start: sel.start, end: sel.end, text } }); toast("Replaced."); renderReading(); } catch (e) { toast(e.message); } };
};

// ---------- lexicon ----------
async function lookUp(word, fromReading) {
  if (!word) return;
  $("#lexOut").innerHTML = `<p class="muted">Looking up…</p>`;
  try {
    const r = await api("/v2/lexicon/lookup", { method: "POST", json: { word } });
    const canApply = fromReading && state.selection && state.view;
    $("#lexOut").innerHTML = `<h2>${esc(r.word)} <span class="muted small">${r.syllables} syllable${r.syllables === 1 ? "" : "s"}</span></h2>
      ${r.definitions.length ? `<ol>${r.definitions.map(d => `<li>${esc(d)}</li>`).join("")}</ol>` : '<p class="muted">No definitions found.</p>'}
      ${r.synonyms.length ? `<div class="label" style="margin:12px 0 6px">Synonyms${canApply ? " · click one to put it in your text" : ""}</div><div class="row">${r.synonyms.map(x => `<button class="btn small" data-syn="${esc(x)}">${esc(x)}</button>`).join("")}</div>` : ""}
      <p class="muted small" style="margin-top:10px">Source: ${esc(r.source)}${r.error ? " · " + esc(r.error) : ""}</p>`;
    $$("[data-syn]").forEach(b => b.onclick = async () => {
      if (!canApply) return;
      if (!(await ensureBackup(state.view.book.id))) return;
      const sel = state.selection;
      try { await api(`/v2/scenes/${sel.scene}/replace`, { method: "POST", json: { start: sel.start, end: sel.end, text: b.dataset.syn } }); toast(`Replaced with “${b.dataset.syn}”.`); state.selection = null; go("reading"); } catch (e) { toast(e.message); }
    });
  } catch (e) { $("#lexOut").innerHTML = `<p>${esc(e.message)}</p>`; }
}
$("#lexGo").onclick = () => lookUp($("#lexWord").value.trim(), false);
$("#lexWord").onkeydown = e => { if (e.key === "Enter") $("#lexGo").click(); };
$("#poemGo").onclick = async () => {
  try {
    const r = await api("/v2/lexicon/poem", { method: "POST", json: { text: $("#poemText").value, form: $("#poemForm").value } });
    $("#poemOut").innerHTML = `<h3>${esc(r.form)} <span class="muted small">rhyme scheme ${esc(r.scheme)}</span></h3>
      ${r.checks.map(c => `<p>• ${esc(c)}</p>`).join("")}
      <div style="margin-top:12px">${r.lines.map((l, i) => `<div class="poem-line"><span class="muted">${i + 1}</span><span>${esc(l.text)}</span><span class="muted small">${l.syllables} syl</span><span class="label">${l.rhyme}</span></div>`).join("")}</div>
      <p class="muted small">${esc(r.note)}</p>`;
  } catch (e) { toast(e.message); }
};

// ---------- start ----------
refresh(); checkHealth(); setInterval(checkHealth, 15000); pollJob();
