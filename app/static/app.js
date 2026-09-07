const $ = s => document.querySelector(s);
const el = (t, c) => { const e = document.createElement(t); if (c) e.className = c; return e; };
const esc = s => (s || "").replace(/[&<>"]/g,
  c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
/* 요소가 없어도 스크립트 전체가 죽지 않도록 감싼다 */
const on = (sel, ev, fn) => {
  const n = document.querySelector(sel);
  if (n) n.addEventListener(ev, fn);
  else console.warn("바인딩 대상 없음:", sel);
};
const fmtTok = n =>
  n >= 1e6 ? (n / 1e6).toFixed(1) + "M" :
  n >= 1e3 ? Math.round(n / 1e3) + "K" : String(n);
/* 텍스트 노드든 요소든 가장 가까운 조상을 찾는다 */
/* 뜨는 창은 한 번에 하나만. 겹쳐 뜨면 서로 가려 읽을 수 없다. */
const MODALS = ["#modal", "#setupModal", "#helpModal", "#usageModal"];
function openModal(id) {
  MODALS.forEach(m => {
    const n = document.querySelector(m);
    if (n) n.classList.toggle("hidden", m !== id);
  });
}
function closeModals() { MODALS.forEach(m => {
  const n = document.querySelector(m); if (n) n.classList.add("hidden"); }); }

const closestOf = (node, selector) => {
  const e = node && (node.nodeType === 1 ? node : node.parentElement);
  return e ? e.closest(selector) : null;
};
const md = t => esc(t).replace(/\*\*(.+?)\*\*/g, "<b>$1</b>").replace(/^- /gm, "· ");

let cur = null;             // 현재 문서
let allParas = [];          // 현재 문서의 전체 문단
let mode = "page";
let curPara = null;
let curPage = null;        // 지금 보고 있는 쪽 (스캔본 질문에 함께 보낸다)
let ctxTerm = "";
let history = [];
let busy = false;
/* 지면 ↔ 번역 상호 표시. 끄면 순수하게 읽기만 한다. */
let linkOn = localStorage.getItem("linkOn") !== "0";
let dragging = false;      // 끄는 동안에는 호버 표시를 하지 않는다
let pageZoom = +(localStorage.getItem("pageZoom") || 1);

function toast(msg, ms = 2600) {
  const t = $("#toast");
  t.textContent = msg;
  t.classList.remove("hidden");
  clearTimeout(t._t);
  t._t = setTimeout(() => t.classList.add("hidden"), ms);
}

/* ---------------- 문서 목록 ---------------- */
let FOLDERS = [], DOCS = [], openFolders = new Set(
  JSON.parse(localStorage.getItem("openFolders") || "[]"));

/* 되돌릴 수 없는 일을 하기 전에 묻는다. 무엇이 사라지는지 먼저 보여준다.
   브라우저 기본 confirm 은 앱 안에서 모양이 겉돌고 막힐 때가 있어 직접 만든다. */
function askConfirm({ title, body = [], ok = "삭제", danger = true }) {
  return new Promise(resolve => {
    const back = el("div", "confirm-back");
    const box = el("div", "confirm-box");
    box.innerHTML = `<h3>${esc(title)}</h3>` +
      (body.length ? `<ul>${body.map(t => `<li>${esc(t)}</li>`).join("")}</ul>` : "");
    const row = el("div", "confirm-row");
    const no = el("button", "btn-plain"); no.textContent = "취소";
    const yes = el("button", danger ? "btn-danger" : "btn-plain");
    yes.textContent = ok;
    row.append(no, yes); box.appendChild(row); back.appendChild(box);
    document.body.appendChild(back);
    const done = v => { back.remove(); document.removeEventListener("keydown", key); resolve(v); };
    const key = ev => { if (ev.key === "Escape") done(false); };
    document.addEventListener("keydown", key);
    no.onclick = () => done(false);
    yes.onclick = () => done(true);
    back.onclick = ev => { if (ev.target === back) done(false); };
    yes.focus();
  });
}

/* 문서에 할 수 있는 일. 되돌릴 수 없는 것들이라 한자리에 모아 두고
   각각 무엇이 사라지는지 먼저 보여준다. */
function docMenu(d, anchor) {
  document.querySelectorAll(".fieldmenu").forEach(n => n.remove());
  const m = el("div", "fieldmenu");
  const items = [
    { label: "번역 초기화", hint: "이 문서의 번역만 지웁니다. 다시 열면 새로 번역합니다.",
      go: () => resetTranslations(d) },
    { label: "문서 삭제", hint: "목록에서 지웁니다. 메모도 함께 사라집니다.",
      go: () => deleteDoc(d) },
  ];
  for (const it of items) {
    const b = el("button", "fm-item");
    b.innerHTML = `<b>${esc(it.label)}</b><i>${esc(it.hint)}</i>`;
    b.onclick = ev => { ev.stopPropagation(); m.remove(); it.go(); };
    m.appendChild(b);
  }
  document.body.appendChild(m);
  const r = anchor.getBoundingClientRect();
  m.style.left = Math.min(r.left - 150, innerWidth - m.offsetWidth - 10) + "px";
  m.style.top = Math.min(r.bottom + 4, innerHeight - m.offsetHeight - 10) + "px";
  setTimeout(() => document.addEventListener("click",
    function off() { m.remove(); document.removeEventListener("click", off); },
    { once: true }), 0);
}

async function resetTranslations(d) {
  let info = null;
  try { info = await (await fetch(`/api/docs/${d.id}/translations`)).json(); }
  catch (e) { /* 못 물어봐도 진행은 할 수 있게 둔다 */ }

  const body = [];
  if (info) {
    if (!info.mine && !info.shared) {
      toast("지울 번역이 없습니다");
      return;
    }
    body.push(`번역해 둔 ${info.mine}문단이 지워집니다`);
    if (info.shared)
      body.push(`${info.shared}문단은 다른 교재도 쓰고 있어 그대로 둡니다`);
  }
  body.push("저장된 해설도 함께 지워집니다.");
  body.push("문서와 메모는 그대로 남습니다. 다시 열면 새로 번역합니다.");

  if (!await askConfirm({ title: `'${d.title}' 의 번역을 초기화할까요?`, body,
                          ok: "초기화" })) return;
  const r = await fetch(`/api/docs/${d.id}/translations`, { method: "DELETE" });
  if (!r.ok) { toast("초기화하지 못했습니다"); return; }
  const j = await r.json();
  await loadDocs();
  if (cur && cur.id === d.id) await openDoc(d.id);
  toast(`번역 ${j.removed}문단을 지웠습니다`);
}

async function deleteDoc(d) {
  let info = null;
  try { info = await (await fetch(`/api/docs/${d.id}/impact`)).json(); }
  catch (e) { /* 못 물어봐도 삭제 자체는 진행할 수 있게 둔다 */ }

  const body = [];
  if (info) {
    body.push(`${info.paras}개 문단과 지면 캐시가 없어집니다`);
    if (info.trans) body.push(`번역해 둔 ${info.trans}문단이 함께 지워집니다 (다시 넣으면 새로 번역해야 합니다)`);
    if (info.notes) body.push(`직접 쓰신 메모 ${info.notes}개가 함께 지워집니다`);
    if (info.explains) body.push(`저장된 해설 ${info.explains}개가 없어집니다`);
  }
  body.push("되돌릴 수 없습니다.");

  if (!await askConfirm({ title: `'${d.title}' 을(를) 목록에서 지울까요?`, body }))
    return;
  const r = await fetch(`/api/docs/${d.id}`, { method: "DELETE" });
  if (!r.ok) { toast("지우지 못했습니다"); return; }
  if (cur && cur.id === d.id) {
    // 보고 있던 문서를 지웠으면 첫 화면으로 되돌린다
    cur = null;
    $("#paras").innerHTML = "";
    $("#more").innerHTML = "";
    $("#welcome").style.display = "";
    closeAllCards();
    clearMarks();
  }
  await loadDocs();
  toast(`'${d.title}' 을(를) 지웠습니다`);
}

let FIELDS = [];
async function loadFields() {
  try { FIELDS = await (await fetch("/api/fields")).json(); }
  catch (e) { FIELDS = []; }
}

async function loadDocs() {
  if (!FIELDS.length) await loadFields();     // 분야 이름표가 있어야 그린다
  [DOCS, FOLDERS] = await Promise.all([
    (await fetch("/api/docs")).json(),
    (await fetch("/api/folders")).json(),
  ]);
  drawSidebar();
  // 교재가 하나도 없으면 무엇을 해야 하는지 알려준다
  const w = document.getElementById("welcomeTitle");
  if (w) w.textContent = DOCS.length
    ? "왼쪽에서 교재를 고르세요"
    : "‘＋ 문서 추가’로 PDF를 넣어 시작하세요";
}

/* 분야 — 용어집과 말투를 고르는 값. 문서를 추가할 때 자동으로 정해지고,
   틀리면 여기서 바꾼다. 번역은 (원문, 분야) 로 묶여 있어 바꾸면 그 분야로
   받은 번역이 보인다. 예전 것도 지워지지 않으니 되돌리면 돌아온다. */
function fieldLabel(id) {
  const f = FIELDS.find(x => x.id === id);
  return f ? f.label : "일반 학술";
}

function pickField(d, anchor) {
  document.querySelectorAll(".fieldmenu").forEach(n => n.remove());
  const m = el("div", "fieldmenu");
  FIELDS.forEach(f => {
    const b = el("button", "fm-item" + (f.id === d.field ? " on" : ""));
    b.innerHTML = `<b>${esc(f.label)}</b><i>${esc(f.hint)}</i>`;
    b.onclick = async ev => {
      ev.stopPropagation();
      m.remove();
      const r = await (await fetch(`/api/docs/${d.id}/field`, {
        method: "PATCH", headers: { "content-type": "application/json" },
        body: JSON.stringify({ field: f.id })
      })).json();
      toast(`분야를 '${f.label}'로 바꿨습니다` +
            (r.ready ? ` (번역 ${r.ready}문단 있음)` : " (이 분야 번역은 새로 받습니다)"));
      await loadDocs();
      if (cur && cur.id === d.id) await openDoc(d.id);
    };
    m.appendChild(b);
  });
  document.body.appendChild(m);
  const r = anchor.getBoundingClientRect();
  m.style.left = Math.min(r.left, innerWidth - m.offsetWidth - 10) + "px";
  m.style.top = Math.min(r.bottom + 4, innerHeight - m.offsetHeight - 10) + "px";
  setTimeout(() => document.addEventListener("click",
    function off() { m.remove(); document.removeEventListener("click", off); },
    { once: true }), 0);
}

function docNode(d) {
  const n = el("div", "doc");
  n.dataset.id = d.id;
  n.draggable = true;
  const pct = d.total ? Math.round(d.done / d.total * 100) : 0;
  const tag = d.scanned && !d.total ? "스캔본" : `번역 ${pct}%`;
  n.innerHTML = `<div class="t">${esc(d.title)}</div>
    <div class="a">${esc(d.author || "")}${d.author ? " · " : ""}${d.pages}쪽 · ${tag}</div>
    <div class="bar"><i style="width:${pct}%"></i></div>
    <button class="fieldchip" title="번역 분야 — 눌러서 바꿉니다">${esc(fieldLabel(d.field))}</button>
    <button class="docmore" title="이 문서에 할 일">⋯</button>`;
  n.querySelector(".fieldchip").onclick = ev => {
    ev.stopPropagation(); pickField(d, ev.currentTarget);
  };
  n.querySelector(".docmore").onclick = ev => {
    ev.stopPropagation(); docMenu(d, ev.currentTarget);
  };
  n.onclick = () => openDoc(d.id);
  n.ondragstart = ev => {
    ev.dataTransfer.setData("text/doc-id", String(d.id));
    ev.dataTransfer.effectAllowed = "move";
    n.classList.add("dragging");
  };
  n.ondragend = () => n.classList.remove("dragging");
  if (cur && cur.id === d.id) n.classList.add("on");
  return n;
}

function folderNode(f, docs) {
  const wrap = el("div", "folder" + (f.id === null ? " uncat" : ""));
  const key = String(f.id);
  const open = f.id === null || openFolders.has(key);

  const head = el("div", "folder-h");
  // 삼각형에 tw 를 쓰면 안 된다. tw 는 지면 글자 층(투명·절대위치) 클래스라
  // 여기에 걸리면 표시가 사라지고 줄이 무너진다.
  head.innerHTML = `<span class="tri">${open ? "▾" : "▸"}</span>
    <span class="cap">${esc(f.name)}</span>
    <span class="cnt">${docs.length}</span>`;
  if (f.id !== null) {
    const x = el("button", "x"); x.textContent = "✕"; x.title = "폴더 삭제 (문서는 남습니다)";
    x.onclick = async ev => {
      ev.stopPropagation();
      await fetch(`/api/folders/${f.id}`, { method: "DELETE" });
      loadDocs();
    };
    head.appendChild(x);
    head.ondblclick = async ev => {
      ev.stopPropagation();
      const name = prompt("폴더 이름", f.name);
      if (name == null) return;
      await fetch(`/api/folders/${f.id}`, {
        method: "PATCH", headers: { "content-type": "application/json" },
        body: JSON.stringify({ name })
      });
      loadDocs();
    };
  }

  const body = el("div", "folder-body" + (open ? "" : " closed"));
  docs.forEach(d => body.appendChild(docNode(d)));

  head.onclick = () => {
    if (f.id === null) return;
    openFolders.has(key) ? openFolders.delete(key) : openFolders.add(key);
    localStorage.setItem("openFolders", JSON.stringify([...openFolders]));
    drawSidebar();
  };

  // 문서를 끌어다 놓으면 이 폴더로 옮긴다
  wrap.ondragover = ev => {
    if (!ev.dataTransfer.types.includes("text/doc-id")) return;
    ev.preventDefault(); wrap.classList.add("drop");
  };
  wrap.ondragleave = () => wrap.classList.remove("drop");
  wrap.ondrop = async ev => {
    ev.preventDefault(); ev.stopPropagation();
    wrap.classList.remove("drop");
    const id = ev.dataTransfer.getData("text/doc-id");
    if (!id) return;
    await fetch(`/api/docs/${id}/folder`, {
      method: "POST", headers: { "content-type": "application/json" },
      body: JSON.stringify({ folder_id: f.id })
    });
    if (f.id !== null) { openFolders.add(key); localStorage.setItem("openFolders", JSON.stringify([...openFolders])); }
    loadDocs();
  };

  wrap.append(head, body);
  return wrap;
}

function drawSidebar() {
  const box = $("#doclist");
  box.innerHTML = "";
  for (const f of FOLDERS) {
    box.appendChild(folderNode(f, DOCS.filter(d => d.folder_id === f.id)));
  }
  const loose = DOCS.filter(d => !d.folder_id);
  if (loose.length || !FOLDERS.length) {
    box.appendChild(folderNode({ id: null, name: "분류 안 함" }, loose));
  }
}

$("#newFolderBtn").onclick = async () => {
  const name = prompt("폴더 이름", "새 폴더");
  if (name == null) return;
  await fetch("/api/folders", {
    method: "POST", headers: { "content-type": "application/json" },
    body: JSON.stringify({ name })
  });
  loadDocs();
};

let lastEngineLabel = "Claude";   // 미연결 안내에서 쓴다
async function refreshStatus() {
  const dot = $("#aiDot"), lab = $("#aiLab");
  try {
    const st = await (await fetch("/api/status")).json();
    const ok = st.ai !== "none";
    dot.className = "dot " + (ok ? "ok" : "bad");
    const who = st.engine_label || "Claude";
    lastEngineLabel = who;
    dot.title = ok ? `${who} 연결됨 (${st.ai})`
                   : `${who} 미연결 — 눌러서 연결하세요`;
    // 연결이 안 됐을 때는 눈에 띄는 버튼을 내놓는다.
    // 점만으로는 다시 연결할 방법을 알 수가 없다.
    const cb = document.getElementById("connectBtn");
    if (cb) cb.classList.toggle("hidden", ok);
    const pct = st.total ? Math.round(st.translated / st.total * 100) : 0;
    if (!ok) {
      lab.textContent = `${who} 미연결 — 연결하기`;
      lab.title = "눌러서 연결 설정 열기";
      showReconnect();
      return;
    }
    hideReconnect();
    // 남은 한도는 알 수 없으니 '얼마나 빨리 쓰고 있는지'를 보여준다
    const h = st.hour || {}, s5 = st.session5h || {};
    lab.textContent = `번역 ${pct}%`;
    // GPT 로 쓰는 동안에는 사용량을 알 수 없다. 예전 Claude 수치를 그대로
    // 띄우면 지금 그만큼 쓰고 있는 것처럼 보인다.
    if (st.usage_known !== false && s5.calls)
      lab.textContent += `  ·  5시간 ${fmtTok(s5.tokens)} (${fmtTok(h.tokens)}/h)`;
    lab.title = "눌러서 자세히 보기";
    $("#modelTag").textContent = (st.model_now || st.model_chat || "")
      .replace("claude-", "").replace(/-\d{8}$/, "");
  } catch (e) {
    dot.className = "dot bad"; lab.textContent = "-";
  }
}

/* ---------------- 문서 열기 ---------------- */
async function openDoc(id, jumpOrd, term, where) {
  closeAllCards();
  const r = await (await fetch(`/api/doc/${id}?start=0&limit=20000`)).json();
  cur = r.doc;
  allParas = r.paras;
  pendingEls.clear();
  document.querySelectorAll(".doc").forEach(n => n.classList.toggle("on", +n.dataset.id === id));
  $("#docTitle").textContent = cur.title;
  $("#docMeta").textContent = [cur.author, `${cur.pages}쪽`, cur.filename]
    .filter(Boolean).join("  ·  ");
  $("#welcome").style.display = "none";
  $("#more").innerHTML = "";
  curPara = null; curPage = null;
  draw();
  $("#reader").scrollTop = 0;
  if (term) {
    // 검색 결과에서 들어온 경우 — 그 단어를 찾아 표시한다.
    $("#find").value = term;
    setTimeout(() => {
      runFind(term);
      const k = hits.findIndex(h => h.ord === jumpOrd);
      if (k >= 0) gotoHit(k);
    }, 250);
  } else {
    $("#find").value = ""; clearFind();
    if (jumpOrd != null) jumpTo(jumpOrd);
  }
  // 기다리지 않으면 뒤늦게 도착해 방금 칠한 표시를 지운다
  await loadNotes();

  // 스캔본은 읽는 것과 상관없이 뒤에서 글자를 읽어둔다
  if (cur.scanned && !allParas.length) startOcr(id);
}

async function startOcr(id) {
  try {
    const j = await (await fetch(`/api/ocr/${id}`, { method: "POST" })).json();
    if (j.state !== "running") return;
    for (;;) {
      await new Promise(r => setTimeout(r, 1500));
      const s = await (await fetch(`/api/job/${j.job}`)).json();
      const banner = document.getElementById("ocrBanner");
      if (banner && s.state === "running") {
        banner.innerHTML = "이 문서는 <b>스캔본</b>입니다. 원본 지면을 그대로 보여주고 있고, " +
          "번역·질문에 쓸 글자를 뒤에서 읽는 중입니다 — " + esc(s.msg);
      }
      if (s.state === "done") {
        toast("글자 인식이 끝났습니다. 이제 번역을 쓸 수 있습니다.", 4000);
        const r = await (await fetch(`/api/doc/${id}?start=0&limit=20000`)).json();
        if (cur && cur.id === id) { allParas = r.paras; draw(); }
        loadDocs();
        return;
      }
      if (s.state === "error") {
        if (banner) banner.textContent = "글자 인식에 실패했습니다: " + s.msg;
        return;
      }
    }
  } catch (e) { /* 원본을 읽는 데는 지장이 없다 */ }
}

/* 화면에 들어온 지면을 '지금 보고 있는 쪽'으로 삼는다.
   스캔본에 질문할 때 이 쪽 그림을 함께 보내기 위해 필요하다. */
const pageSeen = new IntersectionObserver(entries => {
  for (const e of entries) {
    if (e.isIntersecting) curPage = +e.target.dataset.page;
  }
}, { root: document.getElementById("reader"), threshold: 0.25 });

/* 특정 문단으로 화면을 옮긴다 */
function jumpTo(ord) {
  setTimeout(() => {
    const t = document.querySelector(`.row[data-ord="${ord}"]`);
    if (t) { t.scrollIntoView({ block: "center" }); t.classList.add("sel"); }
  }, 150);
}

/* ---------------- 그리기 ---------------- */
function draw() {
  const box = $("#paras");
  box.innerHTML = "";
  box.classList.toggle("pagemode", mode === "page");
  if (!cur) return;
  if (mode === "page") drawPages(box);
  else drawText(box);
  const zb = document.getElementById("pagezoom");
  if (zb) zb.classList.toggle("hidden", mode !== "page" || !cur);
  setTimeout(drawNotePins, 60);
}

/* 문단 하나 (지면/본문 공용) */
function makeRow(p, only) {
  // only 가 주어지면 그 문장들만 그린다 (쪽을 걸친 문단을 나눠 놓을 때)
  const row = el("div", "row " + p.kind);
  row.dataset.ord = p.ord;
  const en = el("div", "en");
  en.textContent = only && p.pairs && p.pairs.length
    ? only.map(i => p.pairs[i][0]).join(" ")
    : p.en;
  const ko = el("div", "ko");
  if (p.ko && p.pairs && p.pairs.length) {
    // 문장마다 span 을 두어 지면의 어느 줄인지 되짚을 수 있게 한다
    (only || p.pairs.map((_, i) => i)).forEach(i => {
      const sp = el("span", "sent");
      sp.dataset.si = i; sp.dataset.ord = p.ord;
      sp.textContent = p.pairs[i][1] + " ";
      ko.appendChild(sp);
    });
  } else if (p.ko) ko.textContent = p.ko;
  else {
    ko.className = "ko pending";
    ko.textContent = "…";
    ko.dataset.pending = "1"; ko.dataset.ord = p.ord;
    transObserver.observe(ko);
  }
  // 문장 단위로 나누지 못한 경우에만 어디까지 이어지는지 적어준다
  const pgs = only ? [] : [...new Set((p.boxes || []).map(b => b[0]))]
    .sort((a, b) => a - b);
  if (pgs.length > 1) {
    const tag = el("div", "spanpg");
    tag.textContent = `원문 p.${pgs[0]}–${pgs[pgs.length - 1]}에 걸침`;
    row.append(en, ko, tag);
  } else row.append(en, ko);
  // 문단 클릭 선택은 두지 않는다. 글자를 끌어 고르는 데 방해가 되고,
  // 지금 읽는 문단은 화면 위치로 알 수 있다.
  rowSeen.observe(row);
  return row;
}

/* 문장이 어느 쪽에 놓였는지 가른다.

   번역이 문장 단위로 짝지어져 있으므로, 쪽을 걸친 문단도 문장을 자르지
   않고 쪽별로 나눠 놓을 수 있다. 한 문장이 쪽 경계에 걸치면 줄이 더 많은
   쪽에 통째로 둔다. */
function sentencePages(p) {
  const lines = p.boxes || [];
  if (!lines.length || !p.pairs || !p.pairs.length) return null;
  const total = normText(p.en).length || 1;
  const out = new Map();
  for (let i = 0; i < p.pairs.length; i++) {
    const [a, b] = sentRange(p, i);
    let i0 = Math.floor(a / total * lines.length);
    let i1 = Math.ceil(b / total * lines.length);
    i0 = Math.max(0, Math.min(i0, lines.length - 1));
    i1 = Math.max(i0 + 1, Math.min(i1, lines.length));
    const tally = {};
    for (let k = i0; k < i1; k++) {
      const pg = lines[k][0];
      tally[pg] = (tally[pg] || 0) + 1;
    }
    const pg = +Object.keys(tally).sort((x, y) => tally[y] - tally[x])[0];
    if (!out.has(pg)) out.set(pg, []);
    out.get(pg).push(i);
  }
  return out;
}

/* 지면 보기 — 원본 PDF 한 장 + 그 쪽의 원문·번역 */
function drawPages(box) {
  if (cur.scanned) {
    const b = el("div", "ocr-banner");
    b.id = "ocrBanner";
    b.innerHTML = allParas.length
      ? "이 문서는 <b>스캔본</b>입니다. 아래 글자는 기계가 읽어낸 것이라 오탈자가 있을 수 있습니다. 정확한 내용은 위 지면을 보세요."
      : "이 문서는 <b>스캔본</b>입니다. 원본 지면을 그대로 보여주고 있고, 번역·질문에 쓸 글자를 뒤에서 읽는 중입니다.";
    box.appendChild(b);
  }

  const byPage = new Map();
  const put = (pg, p, only) => {
    if (!byPage.has(pg)) byPage.set(pg, []);
    byPage.get(pg).push([p, only]);
  };
  for (const p of allParas) {
    const pgs = [...new Set((p.boxes || []).map(b => b[0]))];
    if (pgs.length > 1) {
      const split = sentencePages(p);
      if (split && split.size > 1) {
        // 문장 단위로 갈라 각 쪽 아래에 놓는다
        [...split.keys()].sort((a, b) => a - b)
          .forEach(pg => put(pg, p, split.get(pg)));
        continue;
      }
    }
    put(p.page, p, null);
  }

  for (let i = 1; i <= cur.pages; i++) {
    const wrap = el("div", "sheet-wrap");

    const img = el("div", "pdfpage");
    img.dataset.doc = cur.id; img.dataset.page = i;
    img.style.minHeight = "340px";
    img.textContent = `${i}쪽 불러오는 중…`;
    pageObserver.observe(img);
    pageSeen.observe(img);

    const num = el("div", "pdfnum");
    num.textContent = `p. ${i}`;

    const ko = el("div", "sheet-ko");
    const rows = byPage.get(i) || [];
    if (!rows.length) {
      const e = el("div", "sheet-empty");
      e.textContent = cur.scanned
        ? "이 쪽은 아직 글자를 읽지 못했습니다."
        : "이 쪽에는 본문 글자가 없습니다 (도판·여백).";
      ko.appendChild(e);
    } else rows.forEach(([p, only]) => ko.appendChild(makeRow(p, only)));

    wrap.append(img, num, ko);
    box.appendChild(wrap);
  }
}

/* 본문 보기 — 글자만 이어서 */
function drawText(box) {
  let last = -1;
  for (const p of allParas) {
    if (p.page !== last) {
      const m = el("div", "pagemark");
      m.textContent = `p. ${p.page}`;
      box.appendChild(m);
      last = p.page;
    }
    box.appendChild(makeRow(p));
  }
  if (!allParas.length) {
    const e = el("div", "sheet-empty");
    e.textContent = "읽어낸 글자가 없습니다. 지면 보기로 원본을 보세요.";
    box.appendChild(e);
  }
}

/* 지면 이미지는 가까워질 때만 내려받는다 */
/* 화면에 들어온 지면을 '지금 보고 있는 쪽'으로 삼는다.
   스캔본에 질문할 때 이 쪽 그림을 함께 보내기 위해 필요하다. */
const pageObserver = new IntersectionObserver(entries => {
  for (const e of entries) {
    if (!e.isIntersecting) continue;
    const box = e.target;
    pageObserver.unobserve(box);
    const img = new Image();
    img.onload = () => {
      box.textContent = ""; box.style.minHeight = ""; box.appendChild(img);
      addTextLayer(box, img);
    };
    img.onerror = () => { box.textContent = "이 쪽을 불러오지 못했습니다"; };
    // 주소에 문서 표를 붙인다. 안 붙이면 지웠다 새로 넣은 문서가 같은 번호를
    // 받았을 때 브라우저가 옛 문서의 지면을 캐시에서 그대로 내놓는다.
    img.src = `/api/page/${box.dataset.doc}/${box.dataset.page}?w=1400`
            + (cur && cur.ver ? `&v=${cur.ver}` : "");
  }
}, { root: document.getElementById("reader"), rootMargin: "1500px 0px" });

/* ---------------- 한국어 ↔ 지면 짝짓기 ---------------- */
/* 지면 표시는 두 채널로 나뉜다.
     hover — 커서를 올린 문장 (옅게)
     sel   — 드래그로 고른 문장 (진하게)
   채널은 서로를 지우지 않는다. 고른 문장에는 hover 를 그리지 않으므로
   두 표시가 겹쳐 진해지는 일이 없다. */
let selSet = null;                     // {ord, sis:Set} — 지금 고른 문장들

function clearChannel(ch) {
  document.querySelectorAll(".hl-" + ch).forEach(n => n.remove());
}
function clearMarks() {
  clearChannel("hover"); clearChannel("sel"); selSet = null;
  document.querySelectorAll(".sent.on").forEach(n => n.classList.remove("on"));
}

/* 문단 안에서 i번째 문장이 차지하는 글자 구간을 찾는다 */
function sentRange(p, si) {
  let at = 0;
  for (let k = 0; k < si; k++) at += normText(p.pairs[k][0]).length + 1;
  return [at, at + normText(p.pairs[si][0]).length];
}

/* 글자 구간 -> 지면의 줄 상자들. 줄마다 글자 수가 비슷하다는 가정을 쓴다 */
function boxesFor(p, siList) {
  const lines = p.boxes || [];
  if (!lines.length) return [];
  if (!p.pairs || !p.pairs.length || !siList.length) return lines;
  const total = p.en.length || 1;
  const picked = new Set();
  for (const si of siList) {
    const [a, b] = sentRange(p, si);
    let i0 = Math.floor(a / total * lines.length);
    let i1 = Math.ceil(b / total * lines.length);
    i0 = Math.max(0, Math.min(i0, lines.length - 1));
    i1 = Math.max(i0 + 1, Math.min(i1, lines.length));
    for (let i = i0; i < i1; i++) picked.add(i);
  }
  return [...picked].sort((x, y) => x - y).map(i => lines[i]);
}

function inView(node) {
  if (!node) return false;
  const r = node.getBoundingClientRect();
  const v = document.getElementById("reader").getBoundingClientRect();
  return r.bottom > v.top + 8 && r.top < v.bottom - 8;
}
function revealIfNeeded(node) {
  if (node && !inView(node)) node.scrollIntoView({ block: "center", behavior: "smooth" });
}

function markBoxes(boxes, ch) {
  const byPage = new Map();
  for (const b of boxes) {
    if (!byPage.has(b[0])) byPage.set(b[0], []);
    byPage.get(b[0]).push(b);
  }
  let first = null;
  for (const [pg, list] of byPage) {
    const holder = document.querySelector(`.pdfpage[data-page="${pg}"]`);
    const img = holder && holder.querySelector("img");
    if (!img) continue;
    const W = img.clientWidth, H = img.clientHeight;
    for (const [, x0, y0, x1, y1] of list) {
      const d = el("div", "hl hl-" + ch);
      d.style.left = (x0 * W) + "px";
      d.style.top = (y0 * H) + "px";
      d.style.width = Math.max(2, (x1 - x0) * W) + "px";
      d.style.height = Math.max(2, (y1 - y0) * H) + "px";
      holder.appendChild(d);
      if (!first) first = d;
    }
  }
  return first;
}

/* 한 채널을 비우고 다시 그린다 */
function paintPage(p, siList, ch) {
  clearChannel(ch);
  if (mode !== "page") return null;
  return markBoxes(boxesFor(p, siList), ch);
}
// 옛 이름 — 남은 호출처가 있어도 깨지지 않게
function markOnPage(p, siList, strong) { return paintPage(p, siList, strong ? "sel" : "hover"); }

/* ---- 지면에서 끌면 그 대목의 번역이 켜진다 (한국어→지면의 반대) ---- */

/* 줄 번호 -> 그 줄에 걸치는 문장 번호들 (boxesFor 의 역방향) */
function sentsForLines(p, lineIdxs) {
  const lines = p.boxes || [];
  if (!lines.length || !p.pairs || !p.pairs.length) return [];
  const total = p.en.length || 1;
  const sis = new Set();
  for (const li of lineIdxs) {
    const a = li / lines.length * total;
    const b = (li + 1) / lines.length * total;
    for (let si = 0; si < p.pairs.length; si++) {
      const [sa, sb] = sentRange(p, si);
      if (sb > a && sa < b) sis.add(si);
    }
  }
  return [...sis].sort((x, y) => x - y);
}

/* 지면 위 사각형과 겹치는 줄들을 문단별로 모은다 */
function linesInRect(page, r) {
  const byPara = new Map();
  for (const p of allParas) {
    (p.boxes || []).forEach((b, i) => {
      if (b[0] !== page) return;
      const [, x0, y0, x1, y1] = b;
      if (x1 < r.x0 || x0 > r.x1 || y1 < r.y0 || y0 > r.y1) return;
      if (!byPara.has(p.ord)) byPara.set(p.ord, { p, idx: [] });
      byPara.get(p.ord).idx.push(i);
    });
  }
  return byPara;
}

function showKoFor(page, rect) {
  const found = linesInRect(page, rect);
  if (!found.size) return false;
  clearMarks();

  let firstSpan = null;
  for (const { p, idx } of found.values()) {
    const sis = sentsForLines(p, idx);
    // 지면 쪽에도 같은 대목을 칠해 어디를 잡았는지 보이게 한다
    markBoxes(idx.map(i => p.boxes[i]), true);
    const list = sis.length ? sis : [];
    for (const si of list) {
      const sp = document.querySelector(
        `.sent[data-ord="${p.ord}"][data-si="${si}"]`);
      if (sp) { sp.classList.add("on"); if (!firstSpan) firstSpan = sp; }
    }
    // 번역 문단 자체도 표시해 어디를 잡았는지 분명히 보이게 한다
    const row = document.querySelector(`.row[data-ord="${p.ord}"]`);
    if (row) row.classList.add("sel");
    if (!list.length && row && !firstSpan) firstSpan = row;
  }
  if (firstSpan) firstSpan.scrollIntoView({ block: "center", behavior: "smooth" });
  return true;
}

/* 지면 위 글자 층 — 이미지 위에 투명한 글자를 좌표대로 얹어
   진짜 PDF 뷰어처럼 글자를 끌어 선택할 수 있게 한다. */
async function addTextLayer(holder, img) {
  if (!cur || holder.dataset.tl) return;
  holder.dataset.tl = "1";
  let items;
  try {
    const r = await fetch(`/api/textlayer/${cur.id}/${holder.dataset.page}`
                          + (cur.ver ? `?v=${cur.ver}` : ""));
    items = (await r.json()).items || [];
  } catch (e) { return; }
  if (!items.length) return;

  const layer = el("div", "tlayer");
  const W = img.clientWidth, H = img.clientHeight;
  for (const it of items) {
    const sp = el("span", "tw");
    sp.textContent = it.text + " ";
    if (it.ord != null) sp.dataset.ord = it.ord;
    const w = (it.x1 - it.x0) * W, h = (it.y1 - it.y0) * H;
    sp.style.left = (it.x0 * W) + "px";
    sp.style.top = (it.y0 * H) + "px";
    sp.style.fontSize = Math.max(h * 0.92, 4) + "px";
    sp.style.width = w + "px";
    layer.appendChild(sp);
  }
  holder.appendChild(layer);
}

/* 지면에서 글자를 끌면 그 대목의 번역만 켠다.

   지면 쪽은 브라우저 기본 선택 표시가 이미 보여주므로 덧칠하지 않는다.
   번역은 문장 단위로 짝지어 두었으므로 고른 부분이 걸친 문장만 켠다. */
function normText(t) {
  // 줄 끝에서 잘린 하이픈을 되붙여야 원문과 대조된다 (impli- cation)
  return (t || "").replace(/([A-Za-z])-\s+([a-z])/g, "$1$2")
                  .replace(/\s+/g, " ").trim();
}

function koFromPageSelection(sel) {
  if (!linkOn) return false;
  const txt = sel.toString().trim();
  if (!txt) return false;
  // 선택이 지면 글자 층에서 시작했는지 본다
  if (!closestOf(sel.anchorNode, ".tlayer")
      && !closestOf(sel.focusNode, ".tlayer")) return false;

  const range = sel.getRangeAt(0);
  const ords = new Set();
  document.querySelectorAll(".tlayer .tw[data-ord]").forEach(sp => {
    if (range.intersectsNode(sp)) ords.add(+sp.dataset.ord);
  });
  if (!ords.size) return false;

  clearMarks();
  const norm = normText(txt);
  let first = null, hit = false;

  for (const ord of [...ords].sort((a, b) => a - b)) {
    const p = allParas.find(x => x.ord === ord);
    if (!p || !p.pairs || !p.pairs.length) continue;

    // 고른 글자가 원문의 어디인지 찾는다. 못 찾으면 아무것도 켜지 않는다.
    const hay = normText(p.en);
    let at = hay.indexOf(norm);
    if (at < 0 && norm.length > 24) at = hay.indexOf(norm.slice(0, 24));
    if (at < 0) continue;
    const from = at, to = at + norm.length;

    for (let i = 0; i < p.pairs.length; i++) {
      const [a2, b2] = sentRange(p, i);
      if (b2 <= from || a2 >= to) continue;      // 걸치지 않는 문장은 건너뛴다
      const sp = document.querySelector(
        `.sent[data-ord="${ord}"][data-si="${i}"]`);
      if (sp) { sp.classList.add("on"); if (!first) first = sp; hit = true; }
    }
  }
  revealIfNeeded(first);
  return hit;
}

/* 한국어를 드래그하면 그 문장들이 지면의 어디인지 표시한다 (sel 채널) */
function echoOriginal(sel) {
  if (!linkOn) return false;
  const koBox = closestOf(sel.anchorNode, ".ko");
  if (!koBox) return false;
  const range = sel.getRangeAt(0);
  const hit = [...koBox.querySelectorAll(".sent")].filter(sp => range.intersectsNode(sp));
  if (!hit.length) return false;
  const ord = +hit[0].dataset.ord;
  const p = allParas.find(x => x.ord === ord);
  if (!p || !p.pairs || !p.pairs.length) return false;
  const sis = hit.map(sp => +sp.dataset.si);
  selSet = { ord, sis: new Set(sis) };
  clearChannel("hover");                 // 고른 자리에 옅은 표시가 남지 않게
  revealIfNeeded(paintPage(p, sis, "sel"));
  return true;
}

/* 커서를 올린 문장은 hover 채널로만 그린다. 고른 문장이면 그리지 않는다. */
document.addEventListener("mouseover", e => {
  if (!linkOn || dragging) return;
  const sp = e.target.closest && e.target.closest(".sent");
  if (!sp) return;
  const ord = +sp.dataset.ord, si = +sp.dataset.si;
  if (selSet && selSet.ord === ord && selSet.sis.has(si)) { clearChannel("hover"); return; }
  const p = allParas.find(x => x.ord === ord);
  if (p) paintPage(p, [si], "hover");
});
document.addEventListener("mouseout", e => {
  if (!linkOn || dragging) return;
  if (e.target.closest && e.target.closest(".sent")) clearChannel("hover");
});

/* 화면 가운데 있는 문단을 '지금 읽는 문단'으로 삼는다.
   질문할 때의 맥락이자 진도 기록에 쓰인다. */
let progressTimer = null;
const rowSeen = new IntersectionObserver(entries => {
  let best = null;
  for (const e of entries) {
    if (!e.isIntersecting) continue;
    if (!best || e.boundingClientRect.top < best.boundingClientRect.top) best = e;
  }
  if (!best) return;
  const ord = +best.target.dataset.ord;
  if (Number.isNaN(ord)) return;
  curPara = ord;
  const p = allParas.find(x => x.ord === ord);
  if (p) curPage = p.page;

  // 스크롤할 때마다 저장하면 요란하다. 잠잠해지면 한 번만 남긴다.
  clearTimeout(progressTimer);
  progressTimer = setTimeout(() => {
    if (!cur) return;
    fetch("/api/progress", {
      method: "POST", headers: { "content-type": "application/json" },
      body: JSON.stringify({ doc_id: cur.id, ord: curPara })
    }).catch(() => {});
  }, 1500);
}, { root: document.getElementById("reader"), rootMargin: "-35% 0px -55% 0px" });

function select(row) {
  document.querySelectorAll(".row.sel").forEach(n => n.classList.remove("sel"));
  row.classList.add("sel");
  curPara = +row.dataset.ord;
  const pp = allParas.find(x => x.ord === curPara);
  if (pp) curPage = pp.page;
}

/* ---------------- 읽는 대로 번역 ----------------
   문단마다 따로 부르면 호출 고정비(약 4.3만 토큰)가 문단 수만큼 든다.
   화면에 다가온 문단을 잠깐 모았다가 한 번에 보내면 문단당 2.6천으로 준다. */
const pendingEls = new Map();      // ord -> 번역이 들어갈 자리
let flushTimer = null;
let inFlight = 0;
const MAX_INFLIGHT = 2;
const BATCH = 30;   // 서버가 글자 수로 다시 끊는다

const transObserver = new IntersectionObserver(entries => {
  for (const e of entries) {
    if (!e.isIntersecting) continue;
    const koEl = e.target;
    transObserver.unobserve(koEl);
    if (!koEl.dataset.pending || koEl.dataset.queued) continue;
    koEl.dataset.queued = "1";
    koEl.textContent = "번역 대기 중…";
    pendingEls.set(+koEl.dataset.ord, koEl);
  }
  scheduleFlush();
}, { root: document.getElementById("reader"), rootMargin: "500px 0px 900px 0px" });

function scheduleFlush() {
  if (flushTimer) clearTimeout(flushTimer);
  // 스크롤이 잠깐 멎으면 그때까지 모인 것을 한 묶음으로 보낸다
  flushTimer = setTimeout(flush, 350);
}

async function flush() {
  if (!cur || inFlight >= MAX_INFLIGHT || !pendingEls.size) return;
  const ords = [...pendingEls.keys()].slice(0, BATCH);
  const els = new Map();
  for (const o of ords) { els.set(o, pendingEls.get(o)); pendingEls.delete(o); }

  const at = cur.id;
  inFlight++;
  els.forEach(e => { e.textContent = "번역 중…"; });
  try {
    const r = await fetch("/api/translate-batch", {
      method: "POST", headers: { "content-type": "application/json" },
      body: JSON.stringify({ doc_id: at, ords })
    });
    const j = await r.json();
    if (!cur || cur.id !== at) return;
    if (j.error) { els.forEach((e, o) => markFailed(o, e)); return; }
    els.forEach((e, o) => {
      const got = j.items && j.items[String(o)];
      if (got) applyTranslation(o, e, got);
      else markFailed(o, e);
    });
  } catch (err) {
    els.forEach((e, o) => markFailed(o, e));
  } finally {
    inFlight--;
    if (pendingEls.size) scheduleFlush();
  }
}

function applyTranslation(ord, koEl, got) {
  const p = allParas.find(x => x.ord === ord);
  if (p) { p.ko = got.ko; p.pairs = got.pairs || []; }
  if (!document.body.contains(koEl)) return;
  koEl.className = "ko";
  delete koEl.dataset.pending; delete koEl.dataset.queued;
  koEl.onclick = null;
  koEl.textContent = "";
  if (p && p.pairs.length) {
    p.pairs.forEach(([, koS], i) => {
      const sp = el("span", "sent");
      sp.dataset.si = i; sp.dataset.ord = ord;
      sp.textContent = koS + " ";
      koEl.appendChild(sp);
    });
  } else koEl.textContent = got.ko;
}

function markFailed(ord, koEl) {
  if (!document.body.contains(koEl)) return;
  koEl.textContent = "번역 실패 — 클릭하면 다시 시도";
  delete koEl.dataset.queued;
  koEl.onclick = ev => {
    ev.stopPropagation();
    koEl.dataset.queued = "1";
    pendingEls.set(ord, koEl);
    scheduleFlush();
  };
}

/* ---------------- 묻기(채팅) ---------------- */
function openPanel(ord, term) {
  if (ord != null) curPara = ord;
  $("#panel").classList.remove("closed");
  setCtx(term);
  drawChat();
  $("#chatInput").focus();
}

function setCtx(term) {
  ctxTerm = term || "";
  const box = $("#pCtx");
  if (ctxTerm) { $("#pCtxText").textContent = ctxTerm; box.classList.remove("hidden"); }
  else box.classList.add("hidden");
}

function drawChat() {
  const c = $("#chat");
  c.innerHTML = "";
  if (!history.length) {
    c.innerHTML = curPara == null
      ? `<div class="chat-empty">문단에 매이지 않은 일반 질문입니다.
수업 내용이든 개념이든 편하게 물어보세요.</div>`
      : `<div class="chat-empty">읽고 있는 문단을 맥락으로 삼아 답합니다.
아래에 질문을 쓰거나, 본문에서 문장을 드래그해 지목한 뒤 물어보세요.</div>`;
    return;
  }
  for (const m of history) {
    const d = el("div", "msg " + (m.role === "user" ? "u" : "a"));
    d.innerHTML = m.role === "user" ? esc(m.content) : md(m.content);
    if (m.role === "assistant") d.appendChild(answerActions(m.content));
    c.appendChild(d);
  }
  c.scrollTop = c.scrollHeight;
}

/* 답변을 그대로 두면 사라진다. 옮겨 담을 길을 준다. */
function answerActions(text) {
  const bar = el("div", "msg-act");
  const copy = el("button"); copy.textContent = "복사";
  copy.onclick = () => {
    navigator.clipboard.writeText(text);
    copy.textContent = "복사됨"; setTimeout(() => copy.textContent = "복사", 1200);
  };
  bar.append(copy);
  return bar;
}

async function send(text) {
  text = (text || "").trim();
  if (!text || busy) return;
  busy = true; $("#sendBtn").disabled = true;
  history.push({ role: "user", content: text });
  drawChat();

  const c = $("#chat");
  const wait = el("div", "msg a");
  wait.innerHTML = `<div class="loading">
      <span class="dots"><i></i><i></i><i></i></span>
      <span id="loadMsg">Claude가 읽고 있습니다…</span>
    </div>
    <div class="skel"><span></span><span></span><span></span></div>`;
  c.appendChild(wait); c.scrollTop = c.scrollHeight;

  // 오래 걸릴 때 멈춘 것처럼 보이지 않게 문구를 바꾼다
  const phases = ["문단 맥락을 살피는 중…", "답을 정리하는 중…", "거의 다 됐습니다…"];
  let pi = 0;
  const tick = setInterval(() => {
    const m = document.getElementById("loadMsg");
    if (m) m.textContent = phases[pi++ % phases.length];
  }, 4500);

  try {
    const r = await fetch("/api/chat", {
      method: "POST", headers: { "content-type": "application/json" },
      body: JSON.stringify({
        doc_id: cur ? cur.id : null,
        ord: curPara,                 // null 이면 일반 질문
        page: curPage,                // 스캔본이면 이 쪽 그림을 함께 본다
        term: ctxTerm, messages: history
      })
    });
    const j = await r.json();
    clearInterval(tick); wait.remove();
    if (j.error) {
      const e = el("div", "msg err"); e.textContent = j.error;
      c.appendChild(e); c.scrollTop = c.scrollHeight;
    } else {
      history.push({ role: "assistant", content: j.answer });
      drawChat();
    }
  } catch (err) {
    clearInterval(tick); wait.remove();
    const e = el("div", "msg err"); e.textContent = "요청에 실패했습니다: " + err;
    c.appendChild(e);
  } finally {
    busy = false; $("#sendBtn").disabled = false;
  }
}

const input = $("#chatInput");
input.addEventListener("input", () => {
  input.style.height = "auto";
  input.style.height = Math.min(input.scrollHeight, 130) + "px";
});
input.addEventListener("keydown", e => {
  if (e.key === "Enter" && !e.shiftKey) {
    e.preventDefault();
    const v = input.value; input.value = ""; input.style.height = "auto";
    send(v);
  }
});
$("#sendBtn").onclick = () => {
  const v = input.value; input.value = ""; input.style.height = "auto"; send(v);
};
$("#quick").onclick = e => { if (e.target.dataset.q) send(e.target.dataset.q); };
$("#pClose").onclick = () => $("#panel").classList.add("closed");
$("#pClear").onclick = () => { history = []; drawChat(); };
$("#ctxClear").onclick = () => setCtx("");
$("#saveVocab").onclick = async () => {
  const last = [...history].reverse().find(m => m.role === "assistant");
  const term = ctxTerm || (history.find(m => m.role === "user") || {}).content;
  if (!term) { toast("저장할 내용이 없습니다"); return; }
  await fetch("/api/vocab", {
    method: "POST", headers: { "content-type": "application/json" },
    body: JSON.stringify({
      term, meaning: (last ? last.content : "").slice(0, 600),
      doc_id: cur && cur.id, ord: curPara
    })
  });
  toast("단어장에 저장했습니다");
};

document.addEventListener("mousedown", () => { dragging = true; });
document.addEventListener("mouseup", () => {
  setTimeout(() => { dragging = false; }, 30);
});

/* ---------------- 오른쪽 클릭 메뉴 ----------------
   드래그할 때마다 말풍선이 튀어나오면 읽는 데 방해된다.
   고른 뒤 오른쪽 클릭했을 때만 내놓는다. */
document.addEventListener("mouseup", e => {
  // 메모 카드·핀·메뉴·패널 위에서 뗀 것은 본문 선택이 아니다
  if (e.target.closest("#ctxmenu") || e.target.closest("#panel")
      || e.target.closest(".memo-card") || e.target.closest(".note-pin")) return;
  const sel = window.getSelection();
  const txt = sel.toString().trim();
  // 새로 끌었으면 이전 표시부터 무조건 걷는다.
  // koFromPageSelection·echoOriginal 은 짝을 못 찾으면 칠하기 전에 빠져나가는데,
  // 그 자리에서 걷지 않으면 앞서 켠 문장이 그대로 남아 끌 때마다 쌓인다.
  clearMarks();
  if (!txt) return;
  if (koFromPageSelection(sel)) return;
  echoOriginal(sel);
});

document.addEventListener("contextmenu", e => {
  const sel = window.getSelection();
  const txt = sel.toString().trim();
  const inReader = e.target.closest("#reader");
  if (!txt || !inReader) { hideCtxMenu(); return; }
  e.preventDefault();

  const row = closestOf(sel.anchorNode, ".row")
    || closestOf(sel.focusNode, ".row");
  const rect = sel.getRangeAt(0).getBoundingClientRect();
  const m = $("#ctxmenu");
  m.innerHTML = "";
  const item = (label, fn) => {
    const btn = el("button");
    btn.textContent = label;
    btn.onclick = () => { hideCtxMenu(); fn(); };
    m.appendChild(btn);
  };
  item("묻기", () => openPanel(row ? +row.dataset.ord : null, txt));
  item("메모", () => openDraftCard(txt, row, sel, rect));
  item("복사", () => navigator.clipboard.writeText(txt));

  m.classList.remove("hidden");
  const w = m.offsetWidth || 120, h = m.offsetHeight || 110;
  m.style.left = Math.min(e.clientX, window.innerWidth - w - 8) + "px";
  m.style.top = Math.min(e.clientY + window.scrollY,
                         window.innerHeight - h - 8) + "px";
});

function hideCtxMenu() { $("#ctxmenu").classList.add("hidden"); }
document.addEventListener("mousedown", e => {
  if (!e.target.closest("#ctxmenu")) hideCtxMenu();
});
document.addEventListener("keydown", e => {
  if (e.key === "Escape") { hideCtxMenu(); closeModals(); }
});

/* ---------------- 포스트잇 메모 ---------------- */

/* 번역문 안에서 드래그한 구간의 글자 위치를 잰다 */
function koOffsets(koEl, range) {
  const w = document.createTreeWalker(koEl, NodeFilter.SHOW_TEXT);
  let at = 0, from = null, to = null, n;
  while ((n = w.nextNode())) {
    if (n === range.startContainer) from = at + range.startOffset;
    if (n === range.endContainer) to = at + range.endOffset;
    at += n.length;
  }
  if (from == null || to == null || to <= from) return null;
  return [from, to];
}

/* 저장해 둔 좌표 위에 상자를 덧그린다.
   글자 사이에 태그를 끼워 넣지 않는다 — 그러면 드래그·호버 로직이
   같은 DOM 을 건드릴 때마다 표시가 깨진다. */
function rangeFromOffsets(koEl, from, to) {
  const w = document.createTreeWalker(koEl, NodeFilter.SHOW_TEXT);
  const r = document.createRange();
  let at = 0, started = false, n;
  while ((n = w.nextNode())) {
    const end = at + n.length;
    if (!started && from <= end) { r.setStart(n, Math.max(0, from - at)); started = true; }
    if (started && to <= end) { r.setEnd(n, Math.max(0, to - at)); return r; }
    at = end;
  }
  return null;
}

function paintRange(koEl, from, to, cls, key) {
  const r = rangeFromOffsets(koEl, from, to);
  if (!r) return null;
  const base = koEl.getBoundingClientRect();
  let last = null;
  for (const rc of r.getClientRects()) {
    if (rc.width < 1) continue;
    const d = el("div", cls);
    if (key) d.dataset.key = key;
    d.style.left = (rc.left - base.left) + "px";
    d.style.top = (rc.top - base.top) + "px";
    d.style.width = rc.width + "px";
    d.style.height = rc.height + "px";
    koEl.appendChild(d);
    last = d;
  }
  return last;
}

/* 지면 글자 층의 조각들 위에 상자를 그린다 */
function paintSpans(holder, spans, cls) {
  const base = holder.getBoundingClientRect();
  let last = null;
  for (const sp of spans) {
    const rc = sp.getBoundingClientRect();
    if (rc.width < 1) continue;
    const d = el("div", cls);
    d.style.position = "absolute";
    d.style.left = (rc.left - base.left) + "px";
    d.style.top = (rc.top - base.top) + "px";
    d.style.width = rc.width + "px";
    d.style.height = rc.height + "px";
    holder.appendChild(d);
    last = d;
  }
  return last;
}

function clearPaint(cls) {
  document.querySelectorAll("." + cls).forEach(n => n.remove());
}

let NOTES = [];

/* ======================= 메모 =======================
   메모는 카드 여러 장이다. 카드마다 자기 표시(memo-on[data-key])와
   연결선(#linkLayer [data-key])을 소유하고, 닫힐 때 자기 것만 걷는다.
   저장된 메모의 표시(memo-saved)와 핀은 카드와 무관하게 늘 그려 둔다. */
const cards = new Map();       // key -> {el, ctx}
let draftSeq = 0;
const SVG_NS = "http://www.w3.org/2000/svg";

async function loadNotes() {
  if (!cur) { NOTES = []; drawNotePins(); return; }
  try { NOTES = await (await fetch(`/api/notes?doc_id=${cur.id}`)).json(); }
  catch (e) { NOTES = []; }
  drawNotePins();
}

/* 저장된 메모의 표시와 핀을 그린다. 열린 카드의 표시는 건드리지 않는다. */
function drawNotePins() {
  document.querySelectorAll(".note-pin").forEach(n => n.remove());
  clearPaint("memo-saved");
  for (const n of NOTES) {
    if (n.ord == null) continue;
    const row = document.querySelector(`.row[data-ord="${n.ord}"]`);
    if (!row) continue;
    const koEl = row.querySelector(".ko");
    let anchor = null;
    if (koEl && n.kfrom != null && n.kto != null && n.kto > n.kfrom) {
      anchor = paintRange(koEl, n.kfrom, n.kto, "memo-box memo-saved");
    }
    anchor = anchor || row;
    const holder = row.closest(".sheet-ko") || row.closest("#paras");
    if (!holder) continue;
    if (getComputedStyle(holder).position === "static") holder.style.position = "relative";
    const hb = holder.getBoundingClientRect();
    const ab = anchor.getBoundingClientRect();
    const pin = el("button", "note-pin");
    pin.textContent = "✎";
    pin.title = (n.memo || n.text || "").slice(0, 60);
    // 이 상자는 자체 스크롤이 있다. 스크롤 양을 더하지 않으면 핀이 밀린다.
    pin.style.top = (ab.bottom - hb.top + holder.scrollTop - 20) + "px";
    pin.classList.toggle("open", cards.has(String(n.id)));
    pin.onclick = ev => { ev.stopPropagation(); toggleNoteCard(n); };
    holder.appendChild(pin);
  }
  drawLinks();
}

function toggleNoteCard(n) {
  const key = String(n.id);
  if (cards.has(key)) { focusCard(key); return; }
  openNoteCard(n);
}

/* 카드가 가리키는 구간에 표시를 그리고, 선이 붙을 자리를 돌려준다 */
function paintOpenMark(key, ctx) {
  const row = document.querySelector(`.row[data-ord="${ctx.ord}"]`);
  const koEl = row && row.querySelector(".ko");
  if (!koEl || ctx.kfrom == null || ctx.kto == null) return row;
  return paintRange(koEl, ctx.kfrom, ctx.kto, "memo-box memo-on", key) || row;
}

/* 저장된 메모를 카드로 연다 */
function openNoteCard(n) {
  const key = String(n.id);
  if (cards.has(key)) { focusCard(key); return; }
  const ctx = { id: n.id, ord: n.ord, kfrom: n.kfrom, kto: n.kto,
                doc_id: n.doc_id, page: n.page, text: n.text, en: n.en, ko: n.ko };
  const anchor = paintOpenMark(key, ctx);
  revealIfNeeded(anchor);
  makeCard(key, ctx, n.memo || "", anchor, null);
  drawNotePins();                          // 핀에 '열림' 표시
}

/* 드래그한 구간에 새 메모 카드를 연다 */
function openDraftCard(txt, row, sel, rect) {
  const p = row ? allParas.find(x => x.ord === +row.dataset.ord) : null;
  const inKo = closestOf(sel.anchorNode, ".ko");
  const off = inKo ? koOffsets(inKo, sel.getRangeAt(0)) : null;
  const key = "draft:" + (++draftSeq);
  const ctx = { ord: p ? p.ord : null, kfrom: off ? off[0] : null, kto: off ? off[1] : null,
                doc_id: cur ? cur.id : null, page: p ? p.page : curPage,
                text: txt, en: inKo ? "" : txt, ko: inKo ? txt : "" };
  const anchor = (inKo && off)
    ? paintRange(inKo, off[0], off[1], "memo-box memo-on", key) : null;
  makeCard(key, ctx, "", anchor, rect);
}

function makeCard(key, ctx, memo, anchor, rect) {
  const card = el("div", "memo-card");
  card.dataset.key = key;
  card.innerHTML = `<textarea placeholder="메모를 적으세요"></textarea>
    <div class="mc-foot">
      <button class="mc-del"${ctx.id ? "" : " hidden"}>삭제</button>
      <button class="mc-cancel">취소</button>
      <button class="mc-save">저장</button>
    </div>`;
  const ta = card.querySelector("textarea");
  ta.value = memo;
  document.getElementById("memoLayer").appendChild(card);
  placeCard(card, anchor, rect);
  cards.set(key, { el: card, ctx });

  card.querySelector(".mc-cancel").onclick = () => closeCard(key);
  card.querySelector(".mc-save").onclick = () => saveCard(key);
  card.querySelector(".mc-del").onclick = () => deleteCard(key);
  ta.addEventListener("keydown", e => {
    if (e.key === "Escape") closeCard(key);
    if (e.key === "Enter" && (e.metaKey || e.ctrlKey)) saveCard(key);
  });
  card.addEventListener("mousedown", e => {
    if (e.target.closest("textarea") || e.target.closest("button")) return;
    cardDrag = { key, x: e.clientX, y: e.clientY,
                 l: parseFloat(card.style.left) || 0, t: parseFloat(card.style.top) || 0 };
    card.classList.add("dragging");
    e.preventDefault();
  });
  ta.focus();
  drawLinks();
  return card;
}

/* 기본 자리: 문장 오른쪽 옆. 자리가 없으면 아래. 이미 열린 카드만큼 비껴 둔다. */
function placeCard(card, anchor, rect) {
  const w = 270, pad = 12;
  const a = rect || (anchor && anchor.getBoundingClientRect());
  let left, top;
  if (a) {
    const col = anchor && (anchor.closest(".sheet-ko") || anchor.closest("#paras"));
    const cb = col ? col.getBoundingClientRect() : null;
    if (cb && cb.right + w + pad < window.innerWidth) { left = cb.right + 14; top = a.top - 6; }
    else { left = a.left + a.width / 2 - w / 2; top = a.bottom + 10; }
  } else { left = window.innerWidth / 2 - w / 2; top = window.innerHeight / 2 - 110; }
  const n = cards.size;
  left += n * 18; top += n * 22;
  left = Math.max(pad, Math.min(left, window.innerWidth - w - pad));
  top = Math.max(pad, Math.min(top, window.innerHeight - 200));
  card.style.left = left + "px";
  card.style.top = top + "px";
}

function focusCard(key) {
  const c = cards.get(key); if (!c) return;
  c.el.querySelector("textarea").focus();
  c.el.classList.add("bump");
  setTimeout(() => c.el.classList.remove("bump"), 320);
}

function closeCard(key) {
  const c = cards.get(key); if (!c) return;
  c.el.remove(); cards.delete(key);
  const q = `[data-key="${CSS.escape(key)}"]`;
  document.querySelectorAll(".memo-on" + q).forEach(n => n.remove());
  document.querySelectorAll("#linkLayer " + q).forEach(n => n.remove());
  document.querySelectorAll(".note-pin.open").forEach(pn => {
    // 닫힌 카드의 핀만 '열림'을 끈다
    if (!cards.size) pn.classList.remove("open");
  });
  if (cards.size) drawNotePins();
}
function closeAllCards() { [...cards.keys()].forEach(closeCard); drawNotePins(); }

async function saveCard(key) {
  const c = cards.get(key); if (!c) return;
  const memo = c.el.querySelector("textarea").value.trim();
  const h = { "content-type": "application/json" };
  if (c.ctx.id) {
    await fetch(`/api/notes/${c.ctx.id}`, { method: "PATCH", headers: h, body: JSON.stringify({ memo }) });
  } else {
    const r = await fetch("/api/notes", { method: "POST", headers: h, body: JSON.stringify({ ...c.ctx, memo }) });
    const j = await r.json().catch(() => ({}));
    if (j.id) c.ctx.id = j.id;
  }
  closeCard(key);
  toast("메모를 저장했습니다");
  await loadNotes();
}
async function deleteCard(key) {
  const c = cards.get(key); if (!c || !c.ctx.id) return;
  await fetch(`/api/notes/${c.ctx.id}`, { method: "DELETE" });
  closeCard(key);
  toast("메모를 지웠습니다");
  await loadNotes();
}

/* 열린 카드마다 자기 표시의 끝에서 카드로 선을 잇는다 */
function drawLinks() {
  const svg = document.getElementById("linkLayer");
  if (!svg) return;
  svg.innerHTML = "";
  for (const [key, c] of cards) {
    const marks = document.querySelectorAll(`.memo-on[data-key="${CSS.escape(key)}"]`);
    const target = marks[marks.length - 1];
    if (!target) continue;
    const t = target.getBoundingClientRect(), b = c.el.getBoundingClientRect();
    const x1 = t.right, y1 = t.top + t.height / 2;
    const x2 = b.left < x1 ? b.right : b.left, y2 = b.top + 22;
    const mid = (x1 + x2) / 2;
    const path = document.createElementNS(SVG_NS, "path");
    path.setAttribute("d", `M ${x1} ${y1} C ${mid} ${y1}, ${mid} ${y2}, ${x2} ${y2}`);
    path.setAttribute("fill", "none"); path.setAttribute("stroke", "var(--memo-line)");
    path.setAttribute("stroke-width", "1.6"); path.setAttribute("stroke-dasharray", "4 3");
    path.dataset.key = key;
    const dot = document.createElementNS(SVG_NS, "circle");
    dot.setAttribute("cx", x1); dot.setAttribute("cy", y1); dot.setAttribute("r", "3");
    dot.setAttribute("fill", "var(--memo-line)"); dot.dataset.key = key;
    svg.append(path, dot);
  }
}

/* 카드 끌기는 문서 전체에서 한 벌로 처리한다 */
let cardDrag = null;
document.addEventListener("mousemove", e => {
  if (!cardDrag) return;
  const c = cards.get(cardDrag.key); if (!c) { cardDrag = null; return; }
  c.el.style.left = (cardDrag.l + e.clientX - cardDrag.x) + "px";
  c.el.style.top = (cardDrag.t + e.clientY - cardDrag.y) + "px";
  drawLinks();
});
document.addEventListener("mouseup", () => {
  if (!cardDrag) return;
  const c = cards.get(cardDrag.key);
  if (c) c.el.classList.remove("dragging");
  cardDrag = null;
  drawLinks();
});

/* 스크롤·창 크기 변화에 핀과 선을 따라 붙인다 */
document.addEventListener("scroll", e => {
  const t = e.target;
  if (t && t.classList && t.classList.contains("sheet-ko")) {
    clearTimeout(window._pinT);
    window._pinT = setTimeout(drawNotePins, 80);
  }
  if (cards.size) requestAnimationFrame(drawLinks);
}, true);
window.addEventListener("resize", () => { if (cards.size) drawLinks(); });

/* ---------------- 지면 확대 ---------------- */
function applyPageZoom() {
  document.documentElement.style.setProperty("--pz", pageZoom);
  const lab = document.getElementById("pzLabel");
  if (lab) lab.textContent = Math.round(pageZoom * 100) + "%";
  const box = document.getElementById("pagezoom");
  if (box) box.classList.toggle("hidden", mode !== "page" || !cur);
  localStorage.setItem("pageZoom", pageZoom);

  // 글자 층은 그림 크기에 맞춰 놓아둔 것이라 다시 깔아야 한다
  clearMarks();
  document.querySelectorAll(".pdfpage").forEach(holder => {
    const img = holder.querySelector("img");
    const layer = holder.querySelector(".tlayer");
    if (!img || !layer) return;
    layer.remove();
    delete holder.dataset.tl;
    addTextLayer(holder, img);
  });
}
function setPageZoom(v) {
  pageZoom = Math.max(0.5, Math.min(3, Math.round(v * 20) / 20));
  applyPageZoom();
}
on("#pzIn", "click", () => setPageZoom(pageZoom + 0.1));
on("#pzOut", "click", () => setPageZoom(pageZoom - 0.1));
on("#pzReset", "click", () => setPageZoom(1));

/* ---------------- 보기 설정 ---------------- */
function applyBodyClass() {
  // 지면 보기에서는 원문이 지면에 그대로 있으므로 옆 단에는 번역만 둔다
  const c = [];
  if (mode === "en" || mode === "ko") c.push("mode-" + mode);
  if (mode === "page") c.push("page-en-off");
  document.body.className = c.join(" ");
}

function setMode(m) {
  closeAllCards();
  mode = m;
  document.querySelectorAll("#viewmode button")
    .forEach(b => b.classList.toggle("on", b.dataset.mode === m));
  applyBodyClass();
  localStorage.setItem("mode", m);
  draw();
  applyPageZoom();
}
$("#viewmode").onclick = e => { if (e.target.dataset.mode) setMode(e.target.dataset.mode); };

function applyLinkToggle() {
  const b = document.getElementById("linkToggle");
  if (b) {
    b.classList.toggle("on", linkOn);
    b.setAttribute("aria-checked", linkOn ? "true" : "false");
    b.title = linkOn
      ? "대치 켜짐 — 한쪽을 짚으면 반대쪽이 표시됩니다"
      : "대치 꺼짐 — 읽기만 합니다";
  }
  if (!linkOn) clearMarks();
}
on("#preBtn", "click", async () => {
  if (!cur) { toast("교재를 먼저 고르세요"); return; }
  const btn = $("#preBtn");
  if (btn.classList.contains("running")) { toast("이미 번역 중입니다"); return; }
  const j = await (await fetch(`/api/pretranslate/${cur.id}`,
                               { method: "POST" })).json();
  if (j.state === "done") { toast(j.msg || "이미 다 번역되어 있습니다", 4000); return; }
  btn.classList.add("running");
  const at = cur.id;
  toast(`${j.total}문단 번역을 시작합니다. 창을 닫지 마세요.`, 6000);
  for (;;) {
    await new Promise(r => setTimeout(r, 2000));
    let st;
    try { st = await (await fetch(`/api/job/${j.job}`)).json(); }
    catch (e) { break; }
    btn.textContent = st.progress
      ? `번역 ${Math.round(st.progress * 100)}%` : "미리 번역";
    if (st.state === "done") {
      btn.classList.remove("running"); btn.textContent = "미리 번역";
      toast("번역이 끝났습니다. 이제 읽는 동안 호출이 없습니다.", 5000);
      if (cur && cur.id === at) await openDoc(at);
      loadDocs();
      return;
    }
    if (st.state === "error") {
      btn.classList.remove("running"); btn.textContent = "미리 번역";
      toast("번역 실패: " + st.msg, 6000);
      return;
    }
  }
});
on("#helpBtn", "click", () => openModal("#helpModal"));
on("#helpClose", "click", () => $("#helpModal").classList.add("hidden"));
on("#helpModal", "click", e => {
  if (e.target.id === "helpModal") $("#helpModal").classList.add("hidden");
});
on("#linkToggle", "click", () => {
  linkOn = !linkOn;
  localStorage.setItem("linkOn", linkOn ? "1" : "0");
  applyLinkToggle();
  toast(linkOn ? "대치 켜짐" : "대치 꺼짐");
});

let fs = +(localStorage.getItem("fs") || 16.5);
const applyFs = () => {
  document.documentElement.style.setProperty("--fs", fs + "px");
  localStorage.setItem("fs", fs);
};
$("#fontPlus").onclick = () => { fs = Math.min(24, fs + 1); applyFs(); };
$("#fontMinus").onclick = () => { fs = Math.max(12, fs - 1); applyFs(); };

/* ---------------- 설정(모델) ---------------- */
async function saveSetting(body, msg) {
  await fetch("/api/settings", {
    method: "POST", headers: { "content-type": "application/json" },
    body: JSON.stringify(body)
  });
  if (msg) toast(msg);
  refreshStatus();
}

$("#setBtn").onclick = async () => {
  const s = await (await fetch("/api/settings")).json();

  // 쓸 AI 고르기. 깔려 있지 않은 쪽은 고를 수는 있게 두되 왜 안 되는지 알린다.
  const eSel = $("#engineSel");
  eSel.innerHTML = "";
  for (const e of s.engines || []) {
    const o = el("option");
    o.value = e.id;
    o.textContent = e.label + (e.ready ? "" : " (설치 안 됨)");
    o.selected = e.id === s.engine;
    eSel.appendChild(o);
  }
  const paintEngine = () => {
    const e = (s.engines || []).find(x => x.id === eSel.value);
    $("#claudeOpts").classList.toggle("hidden", eSel.value !== "claude");
    $("#gptOpts").classList.toggle("hidden", eSel.value !== "gpt");
    $("#engineHint").innerHTML = !e ? ""
      : e.ready
        ? esc(e.hint)
        : esc(e.hint) + "<br><b>아직 설치되지 않았습니다.</b> 터미널에서 "
          + (e.id === "gpt"
             ? "<code>npm install -g @openai/codex</code> 를 실행한 뒤 "
               + "<code>codex login</code> 으로 ChatGPT 계정 로그인이 필요합니다. "
               + "(무료 계정도 됩니다)"
             : "<code>npm install -g @anthropic-ai/claude-code</code> 를 실행한 뒤 "
               + "<code>claude</code> 로 로그인하세요.");
  };
  paintEngine();
  eSel.onchange = async () => {
    paintEngine();
    await saveSetting({ engine: eSel.value }, "쓸 AI를 바꿨습니다");
  };

  const g = $("#gptModel");
  g.innerHTML = "";
  for (const m of s.gpt_models || []) {
    const o = el("option");
    o.value = m.id; o.textContent = m.label; o.selected = m.id === (s.gpt_model || "");
    g.appendChild(o);
  }
  g.onchange = () => saveSetting({ gpt_model: g.value }, "GPT 모델을 바꿨습니다");

  for (const [id, key] of [["#modelChat", "model_chat"], ["#modelTrans", "model_trans"]]) {
    const sel = $(id);
    sel.innerHTML = "";
    for (const m of s.models) {
      const o = el("option");
      o.value = m.id; o.textContent = m.label; o.selected = m.id === s[key];
      sel.appendChild(o);
    }
    sel.onchange = () => {
      const body = {}; body[key] = sel.value;
      saveSetting(body, "모델을 바꿨습니다");
    };
  }
  refreshLibrary();
  openModal("#modal");
};

/* PDF 는 앱에 담지 않는다. 어디에 있는지만 기억한다. */
async function refreshLibrary() {
  const box = $("#libState");
  if (!box) return;
  try {
    const l = await (await fetch("/api/library")).json();
    const miss = (l.missing || []).length;
    box.textContent = !l.total
      ? "아직 교재가 없습니다. ‘문서 추가’로 PDF를 넣으세요."
      : miss
        ? `원본을 못 찾는 문서 ${miss}개 — 폴더를 지정하면 다시 연결됩니다.`
        : `문서 ${l.total}개 모두 원본과 연결됨`;
    if (l.dir) box.textContent += `\n${l.dir}`;
  } catch (e) { box.textContent = "-"; }
}
on("#libPick", "click", async () => {
  const j = await (await fetch("/api/library/pick", { method: "POST" })).json();
  toast(j.error ? j.error
    : `${j.total}개 중 ${j.found}개 연결`
      + (j.copied ? ` · ${j.copied}개를 앱 폴더로 복사(권한 창 방지)` : ""), 6000);
  refreshLibrary(); loadDocs();
});
$("#modalClose").onclick = () => $("#modal").classList.add("hidden");
$("#modal").onclick = e => { if (e.target.id === "modal") $("#modal").classList.add("hidden"); };

/* ---------------- 그냥 질문 · Claude 앱 ---------------- */
$("#askBtn").onclick = () => {
  curPara = null; ctxTerm = ""; history = [];
  $("#panel").classList.remove("closed");
  setCtx(""); drawChat(); $("#chatInput").focus();
};
$("#claudeBtn").onclick = async () => {
  try {
    const j = await (await fetch("/api/open-claude", { method: "POST" })).json();
    toast(j.error ? j.error : "Claude 앱을 열었습니다");
  } catch (e) { toast("Claude 앱을 열지 못했습니다"); }
};

/* ---------------- 문서 추가 ---------------- */
async function upload(files) {
  const pdfs = [...files].filter(f => /\.pdf$/i.test(f.name));
  if (!pdfs.length) { toast("PDF 파일만 추가할 수 있습니다"); return; }
  for (const f of pdfs) {
    toast(`${f.name} 올리는 중…`, 60000);
    const fd = new FormData(); fd.append("file", f);
    try {
      const r = await fetch("/api/upload", { method: "POST", body: fd });
      const j = await r.json();
      if (j.error) { toast(`${f.name}: ${j.error}`, 5000); continue; }
      await watchJob(j.job, f.name);
    } catch (e) { toast(`${f.name} 추가 실패: ${e}`, 5000); }
  }
}

async function watchJob(jobId, name) {
  for (;;) {
    await new Promise(r => setTimeout(r, 900));
    let s;
    try { s = await (await fetch(`/api/job/${jobId}`)).json(); }
    catch (e) { toast(`${name}: 상태 확인 실패`, 4000); return; }
    if (s.state === "running") {
      const pc = s.progress ? ` (${Math.round(s.progress * 100)}%)` : "";
      toast(`${name} — ${s.msg}${pc}`, 60000);
      continue;
    }
    if (s.state === "error") { toast(`${name}: ${s.msg}`, 7000); return; }
    toast(`${s.title} 추가됨 — ${s.pages}쪽`, 4000);
    await loadDocs();
    if (s.doc_id != null) openDoc(s.doc_id);
    return;
  }
}
$("#addDocBtn").onclick = () => $("#fileInput").click();
$("#fileInput").onchange = e => { upload(e.target.files); e.target.value = ""; };

/* ---------------- 검색 · 단어장 ---------------- */
let tmr;
$("#q").oninput = e => {
  clearTimeout(tmr);
  const q = e.target.value.trim();
  tmr = setTimeout(() => { if (q.length >= 2) runSearch(q); }, 260);
};
async function runSearch(q) {
  closeAllCards();
  const list = await (await fetch("/api/search?q=" + encodeURIComponent(q))).json();
  $("#welcome").style.display = "none";
  $("#more").innerHTML = "";
  const box = $("#paras");
  box.innerHTML = ""; box.classList.remove("pagemode");

  const inEn = list.filter(h => h.where !== "ko");
  const inKo = list.filter(h => h.where !== "en");
  $("#docTitle").textContent = `“${q}” 검색 결과 ${list.length}건`;
  $("#docMeta").textContent = "누르면 그 자리로 갑니다";

  const re = new RegExp("(" + q.replace(/[.*+?^${}()|[\]\\]/g, "\\$&") + ")", "gi");

  const item = (h, side) => {
    // 걸린 쪽의 글을 보여준다. 원문에서 걸렸는데 번역을 보이면 헷갈린다.
    const src = side === "ko" ? (h.ko || "") : (h.en || "");
    const i = src.toLowerCase().indexOf(q.toLowerCase());
    const from = Math.max(0, i - 50);
    const snip = (from ? "…" : "") + src.slice(from, from + 220) + "…";
    const n = el("div", "hit");
    n.innerHTML = `<div class="src">#${h.doc_id} ${esc(h.title)} · p.${h.page}</div>
      <div class="tx ${side === "ko" ? "tx-ko" : "tx-en"}">${
        esc(snip).replace(re, "<mark>$1</mark>")}</div>`;
    n.onclick = () => openDoc(h.doc_id, h.ord, q, side);
    return n;
  };

  // 원문은 왼쪽, 번역은 오른쪽
  const grid = el("div", "search-grid");
  for (const [side, rows, label] of
       [["en", inEn, "원문"], ["ko", inKo, "번역"]]) {
    const col = el("div", "search-col");
    const head = el("div", "search-head");
    head.innerHTML = `<span class="hit-tag tag-${side}">${label}</span> ${rows.length}건`;
    col.appendChild(head);
    if (!rows.length) {
      const e = el("div", "sheet-empty");
      e.textContent = "없음";
      col.appendChild(e);
    } else rows.forEach(h => col.appendChild(item(h, side)));
    grid.appendChild(col);
  }
  box.appendChild(grid);
}

$("#notesBtn").onclick = showNotes;

async function showNotes() {
  closeAllCards();
  const ns = await (await fetch("/api/notes")).json();
  $("#welcome").style.display = "none";
  $("#more").innerHTML = "";
  $("#docTitle").textContent = `메모 모음 (${ns.length})`;
  $("#docMeta").textContent = "적어둔 메모입니다. 눌러 고칠 수 있고, 본문 자리로 갈 수 있습니다.";
  const box = $("#paras");
  box.innerHTML = ""; box.classList.remove("pagemode");

  if (!ns.length) {
    const e = el("div", "sheet-empty");
    e.textContent = "아직 메모가 없습니다. 본문에서 문장을 드래그한 뒤 "
      + "오른쪽 클릭 → 메모를 누르세요.";
    box.appendChild(e);
    return;
  }

  // 교재별로 묶어 보여준다
  const byDoc = new Map();
  for (const n of ns) {
    const k = n.doc_id == null ? "-" : n.doc_id;
    if (!byDoc.has(k)) byDoc.set(k, []);
    byDoc.get(k).push(n);
  }

  for (const [, list] of byDoc) {
    const head = el("div", "note-group");
    head.textContent = list[0].title || "문서 없음";
    box.appendChild(head);

    for (const n of list.sort((x, y) => (x.ord ?? 0) - (y.ord ?? 0))) {
      const d = el("div", "note");
      const memo = el("div", "note-memo");
      memo.contentEditable = "true";
      memo.textContent = n.memo || "";
      memo.dataset.ph = "메모를 적으세요";
      let t;
      memo.oninput = () => {
        clearTimeout(t);
        t = setTimeout(() => fetch(`/api/notes/${n.id}`, {
          method: "PATCH", headers: { "content-type": "application/json" },
          body: JSON.stringify({ memo: memo.textContent })
        }), 600);
      };

      const quote = el("div", "note-quote");
      quote.textContent = n.ko || n.en || n.text;

      // 본문으로 가서 그 메모를 켠 채로 연다
      const goTo = async () => {
        if (n.doc_id == null) return;
        await openDoc(n.doc_id, n.ord);          // 안에서 메모까지 다 깐다
        openNoteCard(NOTES.find(x => x.id === n.id) || n);
      };

      const act = el("div", "act");
      const where = el("span", "note-where");
      where.textContent = n.page ? `p.${n.page}` : "";
      const go = el("button", "ghost"); go.textContent = "본문에서 보기";
      go.onclick = ev => { ev.stopPropagation(); goTo(); };
      const rm = el("button", "ghost"); rm.textContent = "삭제";
      rm.onclick = async ev => {
        ev.stopPropagation();
        await fetch(`/api/notes/${n.id}`, { method: "DELETE" });
        showNotes();
      };
      act.append(where, go, rm);

      // 카드 아무 데나 눌러도 간다. 다만 메모를 고치는 중이면 방해하지 않는다.
      d.classList.add("clickable");
      d.onclick = ev => {
        if (ev.target.closest(".note-memo") || ev.target.closest("button")) return;
        goTo();
      };

      d.append(memo, quote, act);
      box.appendChild(d);
    }
  }
}

/* ---------------- 문서 내 찾기 ---------------- */
let hits = [], hitAt = -1;

function clearFind() {
  // 예전에는 문단에 class 를 붙여 표시했는데 지금은 좌표 위에 상자를
  // 덧그린다(find-box). 남아 있던 옛 이름 정리는 하지 않는다.
  clearPaint("find-box");
  hits = []; hitAt = -1;
  $("#findCount").textContent = "";
  $("#findCount").title = "";
  $("#findPrev").disabled = true;
  $("#findNext").disabled = true;
}

/* 화면에 그려진 글에서 직접 찾는다.

   문단 단위로 세면 한 문단에 여러 번 나와도 한 번으로 잡히고, 그 안에서
   이동할 수도 없다. 실제 등장마다 하나씩 센다. */
function runFind(q) {
  clearFind();
  q = (q || "").trim().toLowerCase();
  if (!q || !cur) return;          // 한 글자도 찾는다

  // 지면 위 글자 층에서도 찾는다. 영어는 지면에 있으니 거기에 표시한다.
  document.querySelectorAll(".pdfpage").forEach(holder => {
    const layer = holder.querySelector(".tlayer");
    if (!layer) return;
    const spans = [...layer.querySelectorAll(".tw")];
    let text = "";
    const map = [];
    for (const sp of spans) {
      const t = sp.textContent;
      map.push([text.length, text.length + t.length, sp]);
      text += t;
    }
    const low = text.toLowerCase();
    let i = low.indexOf(q);
    while (i >= 0) {
      const end = i + q.length;
      const covered = map.filter(([a, b]) => b > i && a < end).map(m => m[2]);
      if (covered.length) hits.push({ onPage: true, holder, spans: covered,
                                      where: "en",
                                      ord: +(covered[0].dataset.ord || -1) });
      i = low.indexOf(q, end);
    }
  });

  document.querySelectorAll("#paras .row").forEach(row => {
    for (const [sel, where] of [[".en", "en"], [".ko", "ko"]]) {
      const box = row.querySelector(sel);
      if (!box || box.offsetParent === null) continue;   // 감춰진 쪽은 건너뛴다
      const txt = box.textContent.toLowerCase();
      let i = txt.indexOf(q);
      while (i >= 0) {
        hits.push({ box, from: i, to: i + q.length, where,
                    ord: +row.dataset.ord });
        i = txt.indexOf(q, i + q.length);
      }
    }
  });

  const c = $("#findCount");
  if (!hits.length) { c.textContent = "없음"; return; }
  $("#findPrev").disabled = false;
  $("#findNext").disabled = false;

  // 찾은 자리마다 표시한다
  for (const h of hits) {
    if (h.onPage) paintSpans(h.holder, h.spans, "find-box");
    else paintRange(h.box, h.from, h.to, "find-box");
  }
  gotoHit(0);
}

const WHERE_LABEL = { en: "원문", ko: "번역" };

function gotoHit(i) {
  if (!hits.length) return;
  hitAt = (i + hits.length) % hits.length;
  document.querySelectorAll(".find-now").forEach(n => n.classList.remove("find-now"));

  const h = hits[hitAt];
  const cur2 = h.onPage
    ? paintSpans(h.holder, h.spans, "find-box find-now")
    : paintRange(h.box, h.from, h.to, "find-box find-now");
  revealIfNeeded(cur2 || h.holder || h.box);

  const nEn = hits.filter(x => x.where === "en").length;
  const nKo = hits.filter(x => x.where === "ko").length;
  $("#findCount").innerHTML =
    `<b class="w-${h.where}">${WHERE_LABEL[h.where]}</b> ` +
    `${hitAt + 1}/${hits.length}` +
    `<span class="split">원문 ${nEn} · 번역 ${nKo}</span>`;
}

let findTimer;
on("#find", "input", e => {
  clearTimeout(findTimer);
  const v = e.target.value;
  findTimer = setTimeout(() => {
    if (v.trim().length >= 2) runFind(v);
    else clearFind();
  }, 220);
});
on("#find", "keydown", e => {
  if (e.key === "Escape") { e.target.value = ""; clearFind(); return; }
  if (e.key !== "Enter") return;
  e.preventDefault();
  if (!hits.length) runFind(e.target.value);
  else gotoHit(hitAt + (e.shiftKey ? -1 : 1));
});
on("#findNext", "click", () => gotoHit(hitAt + 1));
on("#findPrev", "click", () => gotoHit(hitAt - 1));
document.addEventListener("keydown", e => {
  if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === "f") {
    e.preventDefault();
    const f = $("#find");
    if (f) { f.focus(); f.select(); }
  }
});

/* ---------------- 초기 세팅 ---------------- */
async function openSetup() {
  openModal("#setupModal");
  await refreshSetup();
}

async function refreshSetup() {
  const st = await (await fetch("/api/setup")).json();

  const paint = (id, ready, cli, login) => {
    const e = document.getElementById(id);
    if (!e) return;
    e.textContent = ready ? "연결됨"
                  : !cli ? "설치 안 됨"
                  : login === false ? "로그인 필요" : "확인 필요";
    e.classList.toggle("ok", !!ready);
  };
  paint("stClaude", st.ready, st.cli, st.logged_in);
  const g = st.gpt || {};
  paint("stGpt", g.ready, g.cli, g.logged_in);

  // 누구 계정으로 붙었는지, 돈이 나가는 방식인지 보여준다.
  // 남의 컴퓨터에 깔아줄 때 이게 없으면 확인할 방법이 없다.
  const acct = (id, a, ready) => {
    const e = document.getElementById(id);
    if (!e) return;
    if (!ready || !a) { e.textContent = ""; e.className = "conn-acct"; return; }
    if (a.billed) {
      e.className = "conn-acct warn-acct";
      e.innerHTML = "<b>API 키로 연결됨 — 쓴 만큼 요금이 붙습니다</b>";
      return;
    }
    const bits = [];
    if (a.account) bits.push(esc(a.account));
    if (a.plan) bits.push(a.plan === "free" ? "무료" : esc(a.plan));
    e.className = "conn-acct";
    e.textContent = (bits.join(" · ") || "구독으로 연결됨")
                  + (a.plan === "free" ? " (한도가 빨리 찹니다)" : "");
  };
  acct("acctClaude", st.account, st.ready);
  acct("acctGpt", g.account, g.ready);

  const btn = (which, ready, cli) => {
    const b = document.querySelector(`[data-conn="${which}"]`);
    if (!b) return;
    // 카드 머리에 이미 이름이 있다. 단추에까지 넣으면 줄이 접힌다.
    b.textContent = ready ? "다시 연결하기" : !cli ? "연결하기" : "로그인하기";
    b.disabled = false;
  };
  btn("claude", st.ready, st.cli);
  btn("gpt", g.ready, g.cli);

  drawSteps("#manualSteps", st.steps || []);
  drawSteps("#gptSteps", st.gpt_steps || []);
}

/* 직접 설치 안내를 그린다. 명령은 눌러서 복사할 수 있게 한다. */
function drawSteps(sel, steps) {
  const ms = document.querySelector(sel);
  if (!ms) return;
  ms.innerHTML = "";
  for (const step of steps) {
    const d = el("div", "mstep");
    d.innerHTML = `<div class="t">${esc(step.title)}</div>
                   <div class="w">${esc(step.why)}</div>`;
    if (step.cmd) {
      const row = el("div", "c");
      const code = el("code"); code.textContent = step.cmd;
      const cp = el("button", "mini"); cp.textContent = "복사";
      cp.onclick = () => {
        navigator.clipboard.writeText(step.cmd);
        cp.textContent = "복사됨"; setTimeout(() => cp.textContent = "복사", 1200);
      };
      row.append(code, cp); d.appendChild(row);
    }
    if (step.link) {
      const a = el("div", "c");
      a.innerHTML = `<a href="${esc(step.link)}" target="_blank">${esc(step.link)}</a>`;
      d.appendChild(a);
    }
    ms.appendChild(d);
  }
}

on("#aiDot", "click", openSetup);
on("#connectBtn", "click", openSetup);
// 연결 전에는 사용량이 의미 없으므로 라벨도 연결 설정으로 보낸다
on("#aiLab", "click", () => {
  const bad = $("#aiDot").classList.contains("bad");
  bad ? openSetup() : openUsage();
});

/* 본문 위에도 안내를 띄워 연결 방법을 놓치지 않게 한다 */
function showReconnect() {
  if (document.getElementById("reconnectBox")) return;
  const box = el("div", "reconnect");
  box.id = "reconnectBox";
  box.innerHTML = `<b>${esc(lastEngineLabel)}에 연결되어 있지 않습니다.</b><br>` +
    "번역과 해설을 쓰려면 Claude Code를 연결해야 합니다. " +
    "PDF를 넣고 원본 지면을 읽는 것은 연결 없이도 됩니다." +
    "<div><button id=\"reconnectBtn\">연결 설정 열기</button></div>";
  const paras = $("#paras");
  paras.parentElement.insertBefore(box, paras);
  box.querySelector("#reconnectBtn").onclick = openSetup;
}
function hideReconnect() {
  const b = document.getElementById("reconnectBox");
  if (b) b.remove();
}

async function openUsage() {
  const u = await (await fetch("/api/usage")).json();
  const rows = [
    ["최근 1시간", u.hour, true],
    ["최근 5시간 (세션 창)", u.session5h, false],
    ["최근 24시간", u.day, false],
    ["최근 7일", u.week, false],
  ];
  $("#usageRows").innerHTML = rows.map(([k, v, big]) =>
    `<tr><td>${k}</td><td class="${big ? "big" : ""}">` +
    `${fmtTok(v.tokens)} · ${v.calls}회</td></tr>`).join("") +
    `<tr><td>7일 평균 속도</td><td>${fmtTok(u.week_rate_per_hour)}/h</td></tr>`;
  $("#usageNote").innerHTML = esc(u.note).replace(
    "/usage", "<code>/usage</code>");
  openModal("#usageModal");
}
on("#usageClose", "click", () => $("#usageModal").classList.add("hidden"));
on("#usageModal", "click", e => {
  if (e.target.id === "usageModal") $("#usageModal").classList.add("hidden");
});

on("#setupClose", "click", () => $("#setupModal").classList.add("hidden"));
on("#setupRecheck", "click", refreshSetup);
/* 단추 하나로 끝낸다. 설치는 앱이 하고, 로그인만 본인이 한다.
   로그인이 끝나는 것을 서버가 지켜보다가 저절로 넘어간다. */
on("#setupModal", "click", async e => {
  const b = e.target.closest("[data-conn]");
  if (!b) return;
  const which = b.dataset.conn;
  const name = which === "gpt" ? "GPT" : "Claude";
  const log = $("#autoLog");
  log.classList.remove("hidden");
  log.textContent = "확인 중…";
  document.querySelectorAll("[data-conn]").forEach(x => x.disabled = true);
  const was = b.textContent;
  b.textContent = "진행 중…";
  try {
    const j = await (await fetch(`/api/setup/auto?which=${which}`,
                                 { method: "POST" })).json();
    for (;;) {
      await new Promise(r => setTimeout(r, 1500));
      const st = await (await fetch(`/api/job/${j.job}`)).json();
      log.textContent = st.msg || "진행 중…";
      showCodeBox(st);
      if (st.state === "done") {
        toast(`${name} 연결됐습니다`);
        break;
      }
      if (st.state === "error") {
        // 손으로 해야 할 상황이면 그쪽 안내를 펼쳐준다
        const card = b.closest(".conn-card");
        const d = card && card.querySelector("details");
        if (d) d.open = true;
        break;
      }
    }
  } catch (err) {
    log.textContent = "문제가 생겼습니다: " + err;
  } finally {
    b.textContent = was;
    document.querySelectorAll("[data-conn]").forEach(x => x.disabled = false);
    hideCodeBox();
    refreshSetup();
    refreshStatus();
  }
});

/* Claude 로그인은 브라우저에서 받은 코드를 붙여넣어야 끝난다.
   터미널을 쓰지 않으려면 그 칸이 앱 안에 있어야 한다. */
let codeEngine = "claude";
function showCodeBox(st) {
  const box = $("#codeBox");
  if (!st || !st.needs_code || st.state !== "running") return;
  codeEngine = st.which || "claude";
  box.classList.remove("hidden");
  const a = $("#codeLink");
  if (st.url) { a.href = st.url; a.classList.remove("hidden"); }
  const inp = $("#loginCode");
  if (document.activeElement !== inp) inp.focus();
}
function hideCodeBox() {
  $("#codeBox").classList.add("hidden");
  $("#loginCode").value = "";
  $("#codeLink").classList.add("hidden");
}

async function sendLoginCode() {
  const inp = $("#loginCode");
  const code = inp.value.trim();
  if (!code) { inp.focus(); return; }
  const btn = $("#codeSend");
  btn.disabled = true;
  try {
    const r = await fetch("/api/setup/code", {
      method: "POST", headers: { "content-type": "application/json" },
      body: JSON.stringify({ engine: codeEngine, code })
    });
    const j = await r.json();
    $("#autoLog").textContent = j.error || j.msg || "확인 중…";
    if (!j.error) inp.value = "";
  } catch (e) {
    $("#autoLog").textContent = "코드를 전달하지 못했습니다: " + e;
  } finally {
    btn.disabled = false;
  }
}
on("#codeSend", "click", sendLoginCode);
on("#loginCode", "keydown", e => { if (e.key === "Enter") sendLoginCode(); });

/* 처음 켰을 때 — Claude 가 연결돼 있지 않으면 설정을 먼저 안내한다 */
let firstRunDone = false;
async function firstRun() {
  if (firstRunDone) return;
  firstRunDone = true;
  try {
    const st = await (await fetch("/api/status")).json();
    if (st.ai === "none") openSetup();
  } catch (e) { /* 조용히 넘어간다 */ }
}

/* ---------------- 시작 ---------------- */
(function init() {
  mode = localStorage.getItem("mode") || "page";
  if (!["page", "ko", "en"].includes(mode)) mode = "page";
  document.querySelectorAll("#viewmode button")
    .forEach(b => b.classList.toggle("on", b.dataset.mode === mode));
  applyBodyClass();
  applyLinkToggle();
  applyPageZoom();
  applyFs();
  drawChat();
  loadDocs();
  refreshStatus().then(firstRun);
  setInterval(refreshStatus, 20000);
})();
