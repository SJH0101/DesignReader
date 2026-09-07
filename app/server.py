"""DesignReader — 로컬 서버."""
from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import subprocess
import threading
import time
import uuid
from pathlib import Path

from fastapi import FastAPI, HTTPException, UploadFile, File
from fastapi.responses import (FileResponse, HTMLResponse, JSONResponse,
                               Response)
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from . import ai, db, extract, ocr, prompts, setup as setup_mod
from . import translate as tr

ROOT = Path(__file__).resolve().parent.parent
DATA = Path(os.environ.get("READER_DATA", ROOT / "data"))
STATIC = Path(os.environ.get("READER_STATIC",
                             Path(__file__).resolve().parent / "static"))

con = db.connect(DATA / "reader.db")
_lock = threading.Lock()

app = FastAPI(title="DesignReader")

DEFAULTS = {"model_chat": ai.MODEL, "model_trans": ai.MODEL,
            "engine": ai.DEFAULT_ENGINE, "gpt_model": "",
            "library_dir": ""}


def engine() -> str:
    """지금 쓰기로 한 AI. claude 또는 gpt."""
    e = setting("engine")
    return e if e in {k for k, _, _ in ai.ENGINES} else ai.DEFAULT_ENGINE


def cur_model(kind: str) -> str | None:
    """엔진에 맞는 모델 이름. 엉뚱한 엔진의 모델을 넘기면 무시된다."""
    if engine() == "gpt":
        return setting("gpt_model") or None
    return setting("model_trans" if kind == "trans" else "model_chat")


# 대화가 길어지면 이력을 통째로 다시 보내느라 호출 비용이 계속 커진다.
# 최근 몇 마디만 남기고 잘라낸다.
MAX_TURNS = 6


def record_usage(kind: str = "chat") -> None:
    u = ai.LAST_USAGE
    if not u:
        return
    with _lock:
        con.execute(
            "INSERT INTO usage_log (kind,inp,outp,cost) VALUES (?,?,?,?)",
            (kind, u.get("input", 0), u.get("output", 0), u.get("cost", 0)))
        con.commit()
    u.clear()


def _window(hours: float) -> dict:
    r = con.execute(
        "SELECT COUNT(*) calls, COALESCE(SUM(inp),0) inp, "
        "COALESCE(SUM(outp),0) outp FROM usage_log "
        "WHERE ts >= datetime('now', ?)", (f"-{hours} hours",)).fetchone()
    return {"calls": r["calls"], "inp": r["inp"], "outp": r["outp"],
            "tokens": r["inp"] + r["outp"]}


@app.get("/api/usage")
def usage():
    """구간별 소모량과 속도.

    요금제의 실제 잔여량은 CLI 가 알려주지 않는다. 여기 숫자는 이 앱이
    직접 쓴 양이며, 공식 잔여량은 `claude` 에서 /usage 로 봐야 한다.
    """
    w1, w5, w24, w7d = (_window(1), _window(5), _window(24), _window(168))
    first = con.execute("SELECT MIN(ts) m FROM usage_log").fetchone()["m"]
    return {
        "hour": w1, "session5h": w5, "day": w24, "week": w7d,
        "rate_per_hour": w1["tokens"],
        "week_rate_per_hour": round(w7d["tokens"] / 168, 1),
        "since": first,
        # 지금 쓰는 쪽에 맞는 안내를 낸다. GPT 로 쓰는 동안 Claude 사용법을
        # 안내하면 엉뚱한 곳을 보게 된다.
        "note": ("Codex 는 사용량을 알려주지 않아 아래 수치에 GPT 사용분은 "
                 "잡히지 않습니다. ChatGPT 한도는 chatgpt.com 에서 확인하세요."
                 if engine() == "gpt" else
                 "요금제 잔여량은 앱에서 알 수 없습니다. "
                 "정확한 잔여량은 터미널에서 claude 실행 후 /usage 로 "
                 "확인하세요."),
    }


def setting(k: str) -> str:
    r = con.execute("SELECT v FROM settings WHERE k=?", (k,)).fetchone()
    return r["v"] if r else DEFAULTS.get(k, "")


@app.get("/api/settings")
def get_settings():
    return {"models": [{"id": m, "label": l} for m, l in ai.MODELS],
            "gpt_models": [{"id": m, "label": l} for m, l in ai.GPT_MODELS],
            "engines": [{"id": k, "label": l, "hint": h,
                         "ready": ai.engine_ready(k)} for k, l, h in ai.ENGINES],
            "engine": engine(),
            "gpt_model": setting("gpt_model"),
            "model_chat": setting("model_chat"),
            "model_trans": setting("model_trans")}


class SettingsReq(BaseModel):
    model_chat: str | None = None
    model_trans: str | None = None
    engine: str | None = None
    gpt_model: str | None = None


@app.post("/api/settings")
def put_settings(req: SettingsReq):
    ok = {
        "model_chat": ai.VALID, "model_trans": ai.VALID,
        "engine": {k for k, _, _ in ai.ENGINES},
        # 빈 값은 '기본값에 맡김' 이라 허용해야 한다
        "gpt_model": ai.GPT_VALID | {""},
    }
    with _lock:
        for k, v in req.model_dump().items():
            if v is None or v not in ok[k]:
                continue
            con.execute("INSERT INTO settings (k,v) VALUES (?,?) "
                        "ON CONFLICT(k) DO UPDATE SET v=excluded.v", (k, v))
        con.commit()
    return get_settings()


def _with_ver(d: dict) -> dict:
    """지면 주소에 붙일 표. 문서가 바뀌면 값이 달라진다.

    번호를 다시 쓰지 않게 고쳤지만, 이미 옛 방식으로 번호가 겹친 채
    브라우저에 남은 그림이 있다. 주소에 이 표를 달아 두면 그런 것을
    확실히 걷어낼 수 있고, 같은 문서를 볼 때는 계속 캐시를 쓴다.
    """
    key = f"{d.get('id')}|{d.get('filename') or ''}|{d.get('pages') or 0}"
    d["ver"] = hashlib.sha1(key.encode("utf-8")).hexdigest()[:8]
    d.pop("filename", None)
    return d


def _doc_field(doc_id: int) -> str:
    """문서의 분야. 번역 캐시 키이자 용어집·말투를 고르는 값이다."""
    r = con.execute("SELECT field FROM docs WHERE id=?", (doc_id,)).fetchone()
    return (r["field"] if r and r["field"] else prompts.DEFAULT_FIELD)


@app.get("/api/docs")
def list_docs():
    rows = con.execute("""
        SELECT d.id, d.title, d.author, d.pages, d.scanned, d.spread,
               d.folder_id, COALESCE(d.field,'general') AS field, d.filename,
               (SELECT COUNT(*) FROM paras p
                 WHERE p.doc_id=d.id AND p.kind IN ('body','heading')) AS total,
               (SELECT COUNT(*) FROM paras p JOIN trans t
                 ON t.h=p.h AND t.field=COALESCE(d.field,'general')
                 WHERE p.doc_id=d.id AND p.kind IN ('body','heading')) AS done,
               (SELECT ord FROM progress g WHERE g.doc_id=d.id) AS last_ord
        FROM docs d ORDER BY d.id""").fetchall()
    return [_with_ver(dict(r)) for r in rows]


@app.get("/api/doc/{doc_id}")
def get_doc(doc_id: int, start: int = 0, limit: int = 400):
    d = con.execute("SELECT * FROM docs WHERE id=?", (doc_id,)).fetchone()
    if not d:
        raise HTTPException(404, "문서를 찾을 수 없습니다")
    rows = con.execute("""
        SELECT p.ord, p.page, p.kind, p.en, p.boxes, t.ko, t.pairs
        FROM paras p LEFT JOIN trans t
          ON t.h=p.h AND t.field=(SELECT COALESCE(field,'general')
                                  FROM docs WHERE id=p.doc_id)
        WHERE p.doc_id=? AND p.ord>=? ORDER BY p.ord LIMIT ?""",
        (doc_id, start, limit)).fetchall()
    out = []
    for r in rows:
        p = dict(r)
        p["boxes"] = json.loads(p["boxes"]) if p.get("boxes") else []
        p["pairs"] = json.loads(p["pairs"]) if p.get("pairs") else []
        out.append(p)
    return {"doc": _with_ver(dict(d)), "paras": out}


class ExplainReq(BaseModel):
    doc_id: int
    ord: int
    term: str = ""
    mode: str = "term"      # term | para | grammar


@app.post("/api/explain")
def explain(req: ExplainReq):
    row = con.execute("SELECT en FROM paras WHERE doc_id=? AND ord=?",
                      (req.doc_id, req.ord)).fetchone()
    if not row:
        raise HTTPException(404, "문단을 찾을 수 없습니다")
    doc = con.execute("SELECT title, author FROM docs WHERE id=?",
                      (req.doc_id,)).fetchone()

    key = hashlib.sha1(
        f"{req.doc_id}|{req.ord}|{req.mode}|{_doc_field(req.doc_id)}|"
        f"{req.term.strip().lower()}".encode()).hexdigest()
    hit = con.execute("SELECT answer FROM explain WHERE key=?", (key,)).fetchone()
    if hit:
        return {"answer": hit["answer"], "cached": True}

    p = prompts.explain_prompt(req.term, row["en"], doc["title"], doc["author"],
                              req.mode)
    try:
        ans = ai.ask(p, prompts.explain_system(_doc_field(req.doc_id)),
                     timeout=120,
                     model=cur_model("chat"), engine=engine())
        record_usage("chat")
    except ai.AIError as e:
        return JSONResponse({"error": str(e)}, status_code=503)
    with _lock:
        con.execute(
            "INSERT OR REPLACE INTO explain (key,doc_id,ord,term,answer) "
            "VALUES (?,?,?,?,?)", (key, req.doc_id, req.ord, req.term, ans))
        con.commit()
    return {"answer": ans, "cached": False}


class TransReq(BaseModel):
    doc_id: int
    ord: int


@app.post("/api/translate")
def translate_one(req: TransReq):
    """아직 번역되지 않은 문단을 즉석에서 번역한다(일괄 번역 보완용)."""
    row = con.execute("SELECT en, h FROM paras WHERE doc_id=? AND ord=?",
                      (req.doc_id, req.ord)).fetchone()
    if not row:
        raise HTTPException(404, "문단을 찾을 수 없습니다")
    fld = _doc_field(req.doc_id)
    hit = con.execute("SELECT ko, pairs FROM trans WHERE h=? AND field=?",
                      (row["h"], fld)).fetchone()
    if hit:
        return {"ko": hit["ko"], "cached": True,
                "pairs": json.loads(hit["pairs"]) if hit["pairs"] else []}
    doc = con.execute("SELECT title, author FROM docs WHERE id=?",
                      (req.doc_id,)).fetchone()
    try:
        model = cur_model("trans")
        ko, pairs = tr.translate_aligned(row["en"], doc["title"], doc["author"],
                                         model, field=fld, engine=engine())
        record_usage("translate")
    except ai.AIError as e:
        return JSONResponse({"error": str(e)}, status_code=503)
    with _lock:
        con.execute("INSERT OR REPLACE INTO trans (h,field,ko,pairs,model,engine) "
                    "VALUES (?,?,?,?,?,?)",
                    (row["h"], fld, ko,
                     json.dumps(pairs, ensure_ascii=False), model, engine()))
        con.commit()
    return {"ko": ko, "pairs": pairs}


class ChatMsg(BaseModel):
    role: str
    content: str


class ChatReq(BaseModel):
    doc_id: int | None = None
    ord: int | None = None          # 없으면 문단에 매이지 않은 일반 질문
    term: str = ""
    page: int | None = None         # 스캔본에서 보고 있는 쪽
    messages: list[ChatMsg]


@app.post("/api/chat")
def chat(req: ChatReq):
    para_en, title, author = "", "", ""
    if req.doc_id is not None and req.ord is not None:
        row = con.execute("SELECT en FROM paras WHERE doc_id=? AND ord=?",
                          (req.doc_id, req.ord)).fetchone()
        if not row:
            raise HTTPException(404, "문단을 찾을 수 없습니다")
        para_en = row["en"]
    if req.doc_id is not None:
        d = con.execute("SELECT title, author FROM docs WHERE id=?",
                        (req.doc_id,)).fetchone()
        if d:
            title, author = d["title"], d["author"]

    hist = [m.model_dump() for m in req.messages][-MAX_TURNS:]
    # 첫 질문이고 문단에 매여 있을 때만 캐시가 의미 있다
    cacheable = len(hist) == 1 and bool(para_en)
    key = hashlib.sha1(
        f"chat|{req.doc_id}|{req.ord}|{req.term.strip().lower()}|"
        f"{hist[0]['content'].strip().lower() if hist else ''}".encode()).hexdigest()
    if cacheable:
        hit = con.execute("SELECT answer FROM explain WHERE key=?", (key,)).fetchone()
        if hit:
            return {"answer": hit["answer"], "cached": True}

    p = prompts.chat_prompt(hist, para_en, req.term, title, author)

    # 스캔본은 글자 인식이 부정확하다. 지면 그림을 직접 보게 해야 답이 맞는다.
    img = None
    if req.doc_id is not None and req.page:
        d = con.execute("SELECT scanned FROM docs WHERE id=?",
                        (req.doc_id,)).fetchone()
        if d and d["scanned"]:
            try:
                img = _render_page(req.doc_id, req.page, 1600)
            except Exception:                           # noqa: BLE001
                img = None
    if img:
        p = (f"먼저 아래 이미지 파일을 읽어라. 지금 학생이 보고 있는 지면이다.\n"
             f"{img}\n\n"
             f"이 지면을 눈으로 확인한 뒤 질문에 답하라. 본문을 그대로 옮겨 "
             f"적지 말고, 묻는 것에만 답하라.\n\n{p}")
    try:
        ans = ai.ask(p, prompts.chat_system(_doc_field(req.doc_id)),
                     timeout=240,
                     model=cur_model("chat"), read_files=bool(img),
                     engine=engine())
        record_usage("chat")
    except ai.AIError as e:
        return JSONResponse({"error": str(e)}, status_code=503)
    if cacheable:
        with _lock:
            con.execute(
                "INSERT OR REPLACE INTO explain (key,doc_id,ord,term,answer) "
                "VALUES (?,?,?,?,?)", (key, req.doc_id, req.ord, req.term, ans))
            con.commit()
    return {"answer": ans, "cached": False}


class BatchReq(BaseModel):
    doc_id: int
    ords: list[int]


@app.post("/api/translate-batch")
def translate_batch(req: BatchReq):
    """읽고 있는 쪽의 문단들을 한 번의 호출로 번역한다.

    문단마다 따로 부르면 호출 고정비(약 4.3만 토큰)가 문단 수만큼 든다.
    묶으면 문단당 2.6천 토큰으로 내려간다.
    """
    if not req.ords:
        return {"items": {}}
    qs = ",".join("?" * len(req.ords))
    fld = _doc_field(req.doc_id)
    rows = con.execute(
        f"SELECT p.ord, p.en, p.h, t.ko, t.pairs FROM paras p "
        f"LEFT JOIN trans t ON t.h=p.h AND t.field=? "
        f"WHERE p.doc_id=? AND p.ord IN ({qs}) ORDER BY p.ord",
        [fld, req.doc_id, *req.ords]).fetchall()

    items: dict[str, dict] = {}
    todo = []
    for r in rows:
        if r["ko"]:                       # 이미 번역해 둔 것은 그대로 돌려준다
            items[str(r["ord"])] = {
                "ko": r["ko"],
                "pairs": json.loads(r["pairs"]) if r["pairs"] else [],
            }
        else:
            todo.append(r)
    if not todo:
        return {"items": items, "cached": True}

    doc = con.execute("SELECT title, author FROM docs WHERE id=?",
                      (req.doc_id,)).fetchone()
    model = cur_model("trans")
    try:
        ok = tr.translate_many([r["en"] for r in todo],
                               doc["title"], doc["author"], model,
                               field=fld, engine=engine())
        record_usage("translate")
    except Exception as e:                              # noqa: BLE001
        return JSONResponse({"error": str(e)}, status_code=503)

    with _lock:
        for k, r in enumerate(todo, start=1):
            if k not in ok:
                continue
            ko, pairs = ok[k]
            con.execute(
                "INSERT OR REPLACE INTO trans (h,field,ko,pairs,model,engine) "
                "VALUES (?,?,?,?,?,?)",
                (r["h"], fld, ko,
                 json.dumps(pairs, ensure_ascii=False), model, engine()))
            items[str(r["ord"])] = {"ko": ko, "pairs": pairs}
        con.commit()
    return {"items": items}


class NoteReq(BaseModel):
    text: str
    en: str = ""
    ko: str = ""
    memo: str = ""
    doc_id: int | None = None
    ord: int | None = None
    page: int | None = None
    sis: list[int] = []
    kfrom: int | None = None
    kto: int | None = None


@app.post("/api/notes")
def add_note(req: NoteReq):
    with _lock:
        cur = con.execute(
            "INSERT INTO notes (text,en,ko,memo,doc_id,ord,page,sis,kfrom,kto) "
            "VALUES (?,?,?,?,?,?,?,?,?,?)",
            (req.text, req.en, req.ko, req.memo, req.doc_id, req.ord,
             req.page, json.dumps(req.sis), req.kfrom, req.kto))
        con.commit()
    return {"ok": True, "id": cur.lastrowid}


@app.get("/api/notes")
def get_notes(doc_id: int | None = None):
    if doc_id is None:
        rows = con.execute(
            "SELECT n.*, d.title FROM notes n LEFT JOIN docs d ON d.id=n.doc_id "
            "ORDER BY n.id DESC LIMIT 800").fetchall()
    else:
        rows = con.execute(
            "SELECT n.*, d.title FROM notes n LEFT JOIN docs d ON d.id=n.doc_id "
            "WHERE n.doc_id=? ORDER BY n.ord", (doc_id,)).fetchall()
    out = []
    for r in rows:
        d = dict(r)
        d["sis"] = json.loads(d["sis"]) if d.get("sis") else []
        out.append(d)
    return out


@app.delete("/api/notes/{nid}")
def del_note(nid: int):
    with _lock:
        con.execute("DELETE FROM notes WHERE id=?", (nid,))
        con.commit()
    return {"ok": True}


class MemoReq(BaseModel):
    memo: str


@app.patch("/api/notes/{nid}")
def edit_note(nid: int, req: MemoReq):
    with _lock:
        con.execute("UPDATE notes SET memo=? WHERE id=?", (req.memo, nid))
        con.commit()
    return {"ok": True}


# ---------------- 폴더 ----------------
class FolderReq(BaseModel):
    name: str


@app.get("/api/folders")
def list_folders():
    rows = con.execute(
        "SELECT f.*, (SELECT COUNT(*) FROM docs d WHERE d.folder_id=f.id) n "
        "FROM folders f ORDER BY f.ord, f.id").fetchall()
    return [dict(r) for r in rows]


@app.post("/api/folders")
def add_folder(req: FolderReq):
    name = req.name.strip() or "새 폴더"
    with _lock:
        cur = con.execute("INSERT INTO folders (name) VALUES (?)", (name,))
        con.commit()
    return {"id": cur.lastrowid, "name": name}


@app.patch("/api/folders/{fid}")
def rename_folder(fid: int, req: FolderReq):
    with _lock:
        con.execute("UPDATE folders SET name=? WHERE id=?",
                    (req.name.strip() or "새 폴더", fid))
        con.commit()
    return {"ok": True}


@app.delete("/api/folders/{fid}")
def del_folder(fid: int):
    """폴더만 지우고 안에 있던 문서는 남긴다."""
    with _lock:
        con.execute("UPDATE docs SET folder_id=NULL WHERE folder_id=?", (fid,))
        con.execute("DELETE FROM folders WHERE id=?", (fid,))
        con.commit()
    return {"ok": True}


class MoveReq(BaseModel):
    folder_id: int | None = None


@app.post("/api/docs/{doc_id}/folder")
def move_doc(doc_id: int, req: MoveReq):
    with _lock:
        con.execute("UPDATE docs SET folder_id=? WHERE id=?",
                    (req.folder_id, doc_id))
        con.commit()
    return {"ok": True}


class ProgReq(BaseModel):
    doc_id: int
    ord: int


@app.post("/api/progress")
def set_progress(req: ProgReq):
    with _lock:
        con.execute("INSERT INTO progress (doc_id,ord) VALUES (?,?) "
                    "ON CONFLICT(doc_id) DO UPDATE SET ord=excluded.ord, "
                    "ts=datetime('now')", (req.doc_id, req.ord))
        con.commit()
    return {"ok": True}


# ---------------- 원본 PDF 보기 ----------------
PAGE_CACHE = DATA / "pagecache"


def _library_dirs() -> list[Path]:
    """원본 PDF 를 찾아볼 폴더들.

    PDF 는 앱에 넣지 않는다(용량이 크고, 남의 저작물을 함께 배포하게 된다).
    대신 쓰는 사람이 자기 교재 폴더를 한 번 지정하면 그 경로를 기억한다.
    """
    dirs = []
    lib = setting("library_dir")
    if lib:
        dirs.append(Path(lib))
    dirs.append(DATA / "pdfs")     # 앱으로 넣은 PDF 는 여기에 보관된다
    return dirs


def find_pdf(doc_id: int) -> Path | None:
    r = con.execute("SELECT path, filename FROM docs WHERE id=?",
                    (doc_id,)).fetchone()
    if not r:
        return None
    if r["path"] and Path(r["path"]).exists():
        return Path(r["path"])
    name = r["filename"] or ""
    for base in _library_dirs():
        cand = base / name
        if cand.exists():
            # 다음부터 바로 찾도록 경로를 고쳐 둔다
            with _lock:
                con.execute("UPDATE docs SET path=? WHERE id=?",
                            (str(cand), doc_id))
                con.commit()
            return cand
    return None


def _pdf_path(doc_id: int) -> Path:
    p = find_pdf(doc_id)
    if p is None:
        raise HTTPException(
            404, "원본 PDF를 찾을 수 없습니다. 설정에서 교재 폴더를 지정하세요.")
    return p


@app.get("/api/docs/{doc_id}/impact")
def delete_impact(doc_id: int):
    """지우면 무엇이 없어지는지 미리 알려준다.

    메모는 사용자가 직접 쓴 것이라 되살릴 수 없다. 몇 개가 사라지는지
    반드시 보여주고 지우게 한다.
    """
    d = con.execute("SELECT title, filename FROM docs WHERE id=?",
                    (doc_id,)).fetchone()
    if not d:
        raise HTTPException(404, "문서를 찾을 수 없습니다")
    q = lambda sql: con.execute(sql, (doc_id,)).fetchone()[0]
    return {
        "title": d["title"],
        "notes": q("SELECT COUNT(*) FROM notes WHERE doc_id=?"),
        "paras": q("SELECT COUNT(*) FROM paras WHERE doc_id=?"),
        "explains": q("SELECT COUNT(*) FROM explain WHERE doc_id=?"),
        # 다른 문서가 같은 대목을 쓰고 있으면 그 번역은 남는다
        "trans": q("""SELECT COUNT(*) FROM trans t
            WHERE t.h IN (SELECT h FROM paras WHERE doc_id=?)
              AND t.h NOT IN (SELECT h FROM paras WHERE doc_id<>?)"""
            .replace("doc_id<>?", "doc_id<>" + str(doc_id))),
    }


@app.delete("/api/docs/{doc_id}/translations")
def reset_translations(doc_id: int):
    """이 문서의 번역만 지운다. 문서와 메모는 그대로 둔다.

    다른 문서가 같은 대목을 쓰고 있으면 그 번역은 건드리지 않는다.
    번역은 원문 글자로 묶여 있어서, 같은 글이 실린 다른 교재의 번역까지
    같이 날아가면 안 된다.
    """
    if not con.execute("SELECT 1 FROM docs WHERE id=?", (doc_id,)).fetchone():
        raise HTTPException(404, "문서를 찾을 수 없습니다")
    with _lock:
        cur = con.execute("""DELETE FROM trans WHERE h IN (
                SELECT h FROM paras WHERE doc_id=?)
              AND h NOT IN (SELECT h FROM paras WHERE doc_id<>?)""",
            (doc_id, doc_id))
        n = cur.rowcount
        # 해설도 그 번역을 보고 만든 것이므로 같이 지운다
        con.execute("DELETE FROM explain WHERE doc_id=?", (doc_id,))
        con.commit()
    return {"ok": True, "removed": max(n, 0)}


@app.get("/api/docs/{doc_id}/translations")
def translation_count(doc_id: int):
    """초기화하면 몇 개가 사라지는지 미리 알려준다."""
    q = lambda sql: con.execute(sql, (doc_id, doc_id)).fetchone()[0]
    return {
        "mine": q("""SELECT COUNT(*) FROM trans t
            WHERE t.h IN (SELECT h FROM paras WHERE doc_id=?)
              AND t.h NOT IN (SELECT h FROM paras WHERE doc_id<>?)"""),
        "shared": q("""SELECT COUNT(*) FROM trans t
            WHERE t.h IN (SELECT h FROM paras WHERE doc_id=?)
              AND t.h IN (SELECT h FROM paras WHERE doc_id<>?)"""),
    }


@app.delete("/api/docs/{doc_id}")
def delete_doc(doc_id: int):
    """문서를 목록에서 지운다. 번역해 둔 것도 함께 지운다.

    단, 다른 문서가 같은 대목을 쓰고 있으면 그 번역은 건드리지 않는다.
    번역은 원문 글자로 묶여 있어서, 같은 글이 실린 다른 교재의 번역까지
    같이 날아가면 안 된다.
    """
    d = con.execute("SELECT filename FROM docs WHERE id=?",
                    (doc_id,)).fetchone()
    if not d:
        raise HTTPException(404, "문서를 찾을 수 없습니다")

    with _lock:
        con.execute("""DELETE FROM trans WHERE h IN (
                SELECT h FROM paras WHERE doc_id=?)
              AND h NOT IN (SELECT h FROM paras WHERE doc_id<>?)""",
            (doc_id, doc_id))
        for t in ("paras", "lines", "explain", "notes", "progress"):
            con.execute(f"DELETE FROM {t} WHERE doc_id=?", (doc_id,))
        con.execute("DELETE FROM docs WHERE id=?", (doc_id,))
        con.commit()

    shutil.rmtree(PAGE_CACHE / str(doc_id), ignore_errors=True)

    # 앱이 복사해 둔 PDF 는 아무 문서도 안 쓰게 됐을 때만 지운다.
    # 같은 파일을 다른 문서가 가리키고 있으면 그대로 둔다.
    still = con.execute("SELECT 1 FROM docs WHERE filename=?",
                        (d["filename"],)).fetchone()
    if not still:
        f = PDF_DIR / d["filename"]
        try:
            if f.exists() and f.parent == PDF_DIR:
                f.unlink()
        except OSError:
            pass
    return {"ok": True}


@app.get("/api/fields")
def list_fields():
    return [{"id": k, "label": v["label"], "hint": v["hint"]}
            for k, v in prompts.FIELDS.items()]


class FieldReq(BaseModel):
    field: str


@app.patch("/api/docs/{doc_id}/field")
def set_field(doc_id: int, req: FieldReq):
    """문서의 분야를 바꾼다.

    번역 캐시는 (원문, 분야) 로 묶여 있으므로, 바꾸면 그 분야로 번역된 것만
    보인다. 이미 받아둔 다른 분야 번역은 지우지 않고 남겨두므로 되돌리면
    다시 나타난다.
    """
    if req.field not in prompts.FIELDS:
        raise HTTPException(400, "알 수 없는 분야입니다")
    with _lock:
        con.execute("UPDATE docs SET field=? WHERE id=?", (req.field, doc_id))
        con.commit()
    n = con.execute(
        "SELECT COUNT(*) c FROM paras p JOIN trans t "
        "ON t.h=p.h AND t.field=? WHERE p.doc_id=?",
        (req.field, doc_id)).fetchone()["c"]
    return {"field": req.field, "ready": n}


@app.get("/api/library")
def library_status():
    rows = con.execute("SELECT id FROM docs").fetchall()
    missing = [r["id"] for r in rows if find_pdf(r["id"]) is None]
    return {"dir": setting("library_dir"), "total": len(rows),
            "missing": missing}


@app.post("/api/library/pick")
def library_pick():
    """폴더 선택 창을 띄워 교재 위치를 지정받는다."""
    script = ('POSIX path of (choose folder with prompt '
              '"교재 PDF가 들어 있는 폴더를 고르세요")')
    r = subprocess.run(["osascript", "-e", script],
                       capture_output=True, text=True, timeout=180)
    if r.returncode != 0:
        return JSONResponse({"error": "폴더를 고르지 않았습니다"}, status_code=400)
    path = r.stdout.strip().rstrip("/")
    with _lock:
        con.execute("INSERT INTO settings (k,v) VALUES ('library_dir',?) "
                    "ON CONFLICT(k) DO UPDATE SET v=excluded.v", (path,))
        con.commit()

    # 고른 폴더를 제자리에서 읽으면, 그게 Documents·Desktop 처럼 macOS 가
    # 보호하는 곳일 때 열 때마다 접근 권한을 묻는다. 앱 폴더로 복사해 둔다.
    PDF_DIR.mkdir(parents=True, exist_ok=True)
    rows = con.execute("SELECT id, filename FROM docs").fetchall()
    found = copied = 0
    for row in rows:
        src = find_pdf(row["id"])
        if src is None:
            continue
        found += 1
        if src.parent == PDF_DIR:
            continue
        dest = PDF_DIR / src.name
        try:
            if not dest.exists():
                shutil.copy2(src, dest)
                copied += 1
            with _lock:
                con.execute("UPDATE docs SET path=? WHERE id=?",
                            (str(dest), row["id"]))
                con.commit()
        except Exception:                               # noqa: BLE001
            pass                                        # 원본을 그대로 쓴다
    return {"dir": path, "found": found, "total": len(rows), "copied": copied}


def _render_page(doc_id: int, page: int, w: int = 1400) -> str:
    """PDF 한 쪽을 PNG 로 그려 파일 경로를 돌려준다. 한 번 그리면 재사용한다.

    가로 펼침면(두 쪽이 한 장)인 문서는 논리 쪽 번호로 좌·우 반쪽을 잘라
    그린다. 그래야 읽을 때 책의 쪽과 화면의 쪽이 맞는다.
    """
    w = max(400, min(w, 2400))
    cached = PAGE_CACHE / str(doc_id) / f"{page}_{w}.png"
    if cached.exists():
        return str(cached)

    import pymupdf
    d = con.execute("SELECT spread, split FROM docs WHERE id=?",
                    (doc_id,)).fetchone()
    spread = bool(d and d["spread"])
    split = float(d["split"]) if d and d["split"] else 0.5

    doc = pymupdf.open(_pdf_path(doc_id))
    real = (page + 1) // 2 if spread else page
    if not 1 <= real <= len(doc):
        doc.close()
        raise HTTPException(404, "그런 쪽이 없습니다")

    pg = doc[real - 1]
    r = pg.rect
    clip = None
    if spread:
        mid = r.x0 + r.width * split
        clip = (pymupdf.Rect(r.x0, r.y0, mid, r.y1) if page % 2 == 1
                else pymupdf.Rect(mid, r.y0, r.x1, r.y1))
    zoom = w / (clip.width if clip else r.width)
    pix = pg.get_pixmap(matrix=pymupdf.Matrix(zoom, zoom), clip=clip)
    cached.parent.mkdir(parents=True, exist_ok=True)
    pix.save(cached)
    doc.close()
    return str(cached)


@app.get("/api/textlayer/{doc_id}/{page}")
def text_layer(doc_id: int, page: int):
    """지면 위에 얹을 투명한 글자 조각들.

    스캔본은 인식해 둔 줄을 쓰고, 글자가 살아 있는 PDF 는 그 자리에서
    단어 좌표를 뽑는다. 좌표는 그 쪽 크기로 0~1 정규화한다.
    """
    d = con.execute("SELECT scanned, spread, split FROM docs WHERE id=?",
                    (doc_id,)).fetchone()
    if not d:
        raise HTTPException(404, "문서를 찾을 수 없습니다")

    if d["scanned"]:
        rows = con.execute(
            "SELECT ord, text, x0, y0, x1, y1 FROM lines "
            "WHERE doc_id=? AND page=? ORDER BY ord, idx",
            (doc_id, page)).fetchall()
        return {"items": [dict(r) for r in rows]}

    # 글자가 살아 있는 PDF — 단어 단위로 뽑는다
    import pymupdf
    doc = pymupdf.open(_pdf_path(doc_id))
    if not 1 <= page <= len(doc):
        doc.close()
        raise HTTPException(404, "그런 쪽이 없습니다")
    pg = doc[page - 1]
    r = pg.rect
    words = pg.get_text("words")
    doc.close()

    # 문단 상자로 각 단어가 어느 문단에 속하는지 찾는다
    paras = con.execute(
        "SELECT ord, boxes FROM paras WHERE doc_id=? AND page=?",
        (doc_id, page)).fetchall()
    boxes = []
    for row in paras:
        for b in (json.loads(row["boxes"]) if row["boxes"] else []):
            if b[0] == page:
                boxes.append((row["ord"], b[1], b[2], b[3], b[4]))

    items = []
    for x0, y0, x1, y1, w, *_ in words:
        nx0, ny0 = x0 / r.width, y0 / r.height
        nx1, ny1 = x1 / r.width, y1 / r.height
        cx, cy = (nx0 + nx1) / 2, (ny0 + ny1) / 2
        owner = None
        for o, bx0, by0, bx1, by1 in boxes:
            if bx0 - 0.01 <= cx <= bx1 + 0.01 and by0 - 0.004 <= cy <= by1 + 0.004:
                owner = o
                break
        items.append({"ord": owner, "text": w,
                      "x0": round(nx0, 5), "y0": round(ny0, 5),
                      "x1": round(nx1, 5), "y1": round(ny1, 5)})
    return {"items": items}


@app.get("/api/page/{doc_id}/{page}")
def page_image(doc_id: int, page: int, w: int = 1400):
    return FileResponse(_render_page(doc_id, page, w), media_type="image/png")


@app.post("/api/pretranslate/{doc_id}")
def pretranslate(doc_id: int):
    """교재 하나를 통째로 미리 번역한다.

    읽을 때마다 부르면 호출이 잦고, 호출마다 키체인을 읽어 권한을 묻는다.
    미리 돌려두면 읽는 동안에는 호출이 0 이 된다.
    """
    d = con.execute("SELECT title, author FROM docs WHERE id=?",
                    (doc_id,)).fetchone()
    if not d:
        raise HTTPException(404, "문서를 찾을 수 없습니다")
    for jid, j in JOBS.items():
        if j.get("pre_doc") == doc_id and j.get("state") == "running":
            return {"job": jid, "state": "running"}

    rows = con.execute(
        "SELECT p.ord, p.en, p.h FROM paras p LEFT JOIN trans t "
        "ON t.h=p.h AND t.field=? "
        "WHERE p.doc_id=? AND p.kind IN ('body','heading') AND t.h IS NULL "
        "AND LENGTH(p.en)>12 ORDER BY p.ord",
        (_doc_field(doc_id), doc_id)).fetchall()
    if not rows:
        return {"state": "done", "msg": "이미 다 번역되어 있습니다"}

    job_id = uuid.uuid4().hex[:12]
    JOBS[job_id] = {"state": "running", "msg": f"{len(rows)}문단 번역 준비 중…",
                    "progress": 0.0, "pre_doc": doc_id, "total": len(rows),
                    "done": 0}

    def work():
        job = JOBS[job_id]
        model = cur_model("trans")
        fld = _doc_field(doc_id)
        eng = engine()
        CH = 60                      # 한 번에 이만큼씩 넘기면 안에서 다시 쪼갠다
        try:
            for i in range(0, len(rows), CH):
                part = rows[i:i + CH]
                ok = tr.translate_many([r["en"] for r in part],
                                       d["title"], d["author"], model,
                                       field=fld, engine=eng)
                record_usage("translate")
                with _lock:
                    for k, r in enumerate(part, start=1):
                        if k not in ok:
                            continue
                        ko, pairs = ok[k]
                        con.execute(
                            "INSERT OR REPLACE INTO trans "
                            "(h,field,ko,pairs,model,engine) "
                            "VALUES (?,?,?,?,?,?)",
                            (r["h"], fld, ko,
                             json.dumps(pairs, ensure_ascii=False), model, eng))
                    con.commit()
                job["done"] = min(i + CH, len(rows))
                job["progress"] = job["done"] / len(rows)
                job["msg"] = f"번역 중… {job['done']}/{len(rows)}문단"
            job.update(state="done", msg="번역 완료", progress=1.0)
        except Exception as e:                          # noqa: BLE001
            job.update(state="error", msg=f"{type(e).__name__}: {e}")

    threading.Thread(target=work, daemon=True).start()
    return {"job": job_id, "state": "running", "total": len(rows)}


@app.post("/api/ocr/{doc_id}")
def start_ocr(doc_id: int):
    """스캔본의 글자를 뒤에서 조용히 읽어들인다. 읽는 데는 지장이 없다."""
    d = con.execute("SELECT scanned FROM docs WHERE id=?", (doc_id,)).fetchone()
    if not d:
        raise HTTPException(404, "문서를 찾을 수 없습니다")
    have = con.execute("SELECT COUNT(*) c FROM paras WHERE doc_id=?",
                       (doc_id,)).fetchone()["c"]
    if have:
        return {"state": "done", "msg": "이미 읽어두었습니다"}
    for jid, j in JOBS.items():
        if j.get("doc_id_target") == doc_id and j.get("state") == "running":
            return {"job": jid, "state": "running"}

    path = _pdf_path(doc_id)
    job_id = uuid.uuid4().hex[:12]
    JOBS[job_id] = {"state": "running", "msg": "글자 인식 준비 중…",
                    "progress": 0.0, "ocr": True, "doc_id_target": doc_id}

    def work():
        job = JOBS[job_id]
        try:
            def prog(done, total):
                job["progress"] = done / total
                job["msg"] = f"글자 인식 중… {done}/{total}쪽"

            data = ocr.ocr_pdf(path, progress=prog)
            with _lock:
                con.execute("DELETE FROM paras WHERE doc_id=?", (doc_id,))
                con.executemany(
                    "INSERT INTO paras (doc_id,ord,page,kind,en,h,boxes) "
                    "VALUES (?,?,?,?,?,?,?)",
                    [(doc_id, p["order"], p["page"], p["kind"], p["text"],
                      db.text_hash(p["text"]),
                      json.dumps(p.get("spans") or []))
                     for p in data["paras"]])
                con.execute("DELETE FROM lines WHERE doc_id=?", (doc_id,))
                con.executemany(
                    "INSERT INTO lines (doc_id,page,ord,idx,text,x0,y0,x1,y1) "
                    "VALUES (?,?,?,?,?,?,?,?,?)",
                    [(doc_id, ln[0], p["order"], i, ln[5],
                      ln[1], ln[2], ln[3], ln[4])
                     for p in data["paras"]
                     for i, ln in enumerate(p.get("lines") or [])])
                con.execute(
                    "UPDATE docs SET n_paras=?, pages=?, spread=?, split=? "
                    "WHERE id=?",
                    (len(data["paras"]), data["meta"]["pages"],
                     1 if data["meta"].get("spread") else 0,
                     float(data["meta"].get("split", 0.5)), doc_id))
                con.commit()
            # 쪽 나눔이 바뀌었으니 그려둔 지면 그림을 버린다
            shutil.rmtree(PAGE_CACHE / str(doc_id), ignore_errors=True)
            job.update(state="done", msg="글자 인식 완료",
                       paras=len(data["paras"]), progress=1.0)
        except Exception as e:                          # noqa: BLE001
            job.update(state="error", msg=f"{type(e).__name__}: {e}")

    threading.Thread(target=work, daemon=True).start()
    return {"job": job_id, "state": "running"}


# ---------------- 초기 세팅 ----------------
@app.get("/api/setup")
def setup_status():
    st = setup_mod.probe()
    st["steps"] = setup_mod.MANUAL_STEPS
    st["gpt"] = setup_mod.probe_gpt()
    st["gpt_steps"] = setup_mod.GPT_STEPS
    st["account"] = setup_mod.account_info("claude")
    st["gpt"]["account"] = setup_mod.account_info("gpt")
    return st


@app.post("/api/setup/install")
def setup_install():
    """자동 세팅 1단계 — CLI 설치. 시간이 걸리므로 뒤에서 돌린다."""
    job_id = uuid.uuid4().hex[:12]
    JOBS[job_id] = {"state": "running", "msg": "준비 중…", "progress": 0.0}

    def work():
        job = JOBS[job_id]
        ok, msg = setup_mod.install_cli(lambda m: job.update(msg=m))
        job.update(state="done" if ok else "error", msg=msg, progress=1.0,
                   ok=ok)

    threading.Thread(target=work, daemon=True).start()
    return {"job": job_id}


@app.post("/api/setup/auto")
def setup_auto(which: str = "claude"):
    """단추 하나로 끝내는 자동 연결.

    설치까지는 앱이 다 한다. 로그인만은 대신할 수 없다 — 브라우저에서
    본인이 계정으로 들어가야 하는 절차라서, 그 자리에서 창을 띄워준다.
    """
    job_id = uuid.uuid4().hex[:12]
    JOBS[job_id] = {"state": "running", "msg": "확인 중…", "progress": 0.05,
                    "stage": "probe"}

    gpt = which == "gpt"
    who = "ChatGPT" if gpt else "Claude"

    def work():
        job = JOBS[job_id]
        try:
            st = setup_mod.probe_gpt() if gpt else setup_mod.probe()

            if not st["cli"]:
                job.update(stage="install", progress=0.15)
                installer = (setup_mod.install_codex if gpt
                             else setup_mod.install_cli)
                ok, msg = installer(
                    lambda m: job.update(msg=m, progress=0.35))
                if not ok:
                    job.update(state="error", msg=msg, progress=1.0)
                    return
                st = setup_mod.probe_gpt() if gpt else setup_mod.probe()
                if not st["cli"]:
                    job.update(state="error", progress=1.0,
                               msg="설치는 됐는데 실행 파일을 찾지 못했습니다. "
                                   "앱을 껐다 켜고 다시 눌러 주세요.")
                    return

            if st["logged_in"] is True:
                job.update(state="done", ok=True, progress=1.0,
                           stage="done", msg="이미 연결되어 있습니다.")
                return

            job.update(stage="login", progress=0.7,
                       msg=f"{who} 로그인 창을 여는 중…")
            ok, msg, url = (setup_mod.open_login_gpt() if gpt
                            else setup_mod.open_login())
            if not ok:
                job.update(state="error", msg=msg, progress=1.0)
                return

            # 로그인은 사람이 브라우저에서 해야 한다. 끝날 때까지 지켜본다.
            # 확인은 0.1초도 안 걸리므로 자주 봐도 된다. 늦게 보면
            # 이미 끝났는데도 멈춘 것처럼 보인다.
            job.update(stage="waiting", progress=0.8, url=url,
                       msg=f"브라우저가 열렸습니다. {who} 계정으로 로그인해 "
                           "주세요. 끝나면 여기서 저절로 넘어갑니다.")
            for _ in range(220):                # 최대 약 7분 20초
                time.sleep(2)
                chk = (setup_mod.logged_in_gpt() if gpt
                       else setup_mod.logged_in())
                if chk is True:
                    job.update(state="done", ok=True, progress=1.0,
                               stage="done", msg="연결됐습니다.")
                    return
            job.update(state="error", progress=1.0, url=url,
                       msg="로그인이 확인되지 않았습니다. 브라우저에서 끝까지 "
                           "진행한 뒤 ‘다시 확인’을 눌러 주세요.")
        except Exception as e:                          # noqa: BLE001
            job.update(state="error", progress=1.0,
                       msg=f"{type(e).__name__}: {e}")

    threading.Thread(target=work, daemon=True).start()
    return {"job": job_id}


@app.post("/api/setup/login")
def setup_login():
    """자동 세팅 2단계 — 터미널을 띄워 브라우저 로그인으로 넘긴다."""
    ok, msg = setup_mod.open_login()
    if not ok:
        return JSONResponse({"error": msg}, status_code=400)
    return {"ok": True, "msg": msg}


# ---------------- Claude 앱 열기 ----------------
@app.post("/api/open-claude")
def open_claude():
    for target in ("Claude", "Claude Code"):
        try:
            r = subprocess.run(["open", "-a", target], capture_output=True,
                               text=True, timeout=12)
            if r.returncode == 0:
                return {"ok": True, "opened": target}
        except Exception:                              # noqa: BLE001
            pass
    return JSONResponse({"error": "Claude 앱을 찾지 못했습니다"}, status_code=404)


# ---------------- 문서 추가 ----------------
PDF_DIR = DATA / "pdfs"
JOBS: dict[str, dict] = {}


def _next_doc_id() -> int:
    """한 번 쓴 번호는 다시 쓰지 않는다.

    MAX(id)+1 로 주면 문서를 지웠을 때 그 번호가 비고, 다음 문서가 그 번호를
    물려받는다. 그러면 /api/page/19/0 같은 주소가 다른 문서를 가리키게 되어
    브라우저가 캐시해 둔 이전 문서의 지면을 그대로 내놓는다. 실제로 번역은
    새 문서인데 지면만 옛 문서로 보이는 일이 생긴다.
    """
    mx = con.execute("SELECT COALESCE(MAX(id), -1) m FROM docs").fetchone()["m"]
    seen = int(setting("doc_seq") or -1)
    nid = max(int(mx), seen) + 1
    with _lock:
        con.execute("INSERT INTO settings (k,v) VALUES ('doc_seq',?) "
                    "ON CONFLICT(k) DO UPDATE SET v=excluded.v", (str(nid),))
        con.commit()
    return nid


def _run_ingest(job_id: str, path: Path) -> None:
    job = JOBS[job_id]
    try:
        scanned = ocr.needs_ocr(path)
        job["ocr"] = scanned
        if scanned:
            job["msg"] = "스캔본입니다. 글자 인식 중…"

            def prog(done, total):
                job["progress"] = done / total
                job["msg"] = f"글자 인식 중… {done}/{total}쪽"

            data = ocr.ocr_pdf(path, progress=prog)
        else:
            job["msg"] = "본문 추출 중…"
            data = extract.extract(path)

        if not data["paras"]:
            job.update(state="error", msg="본문을 찾지 못했습니다")
            return

        doc_id = _next_doc_id()
        title, author = db.parse_name(path.name)[1:]

        # 어느 분야 글인지 한 번만 물어본다. 용어집과 말투가 여기서 갈린다.
        # 실패해도 추가 자체는 막지 않는다 — 기본값으로 두고 넘어간다.
        job["msg"] = "분야 확인 중…"
        field = prompts.DEFAULT_FIELD
        sample = " ".join(x["text"] for x in data["paras"][:12])
        if len(sample) > 200:
            try:
                field = prompts.parse_field(
                    ai.ask(prompts.detect_prompt(sample), prompts.DETECT_SYSTEM,
                           timeout=90, model=cur_model("chat"),
                           engine=engine()))
                record_usage("chat")
            except Exception:                   # noqa: BLE001
                pass

        with _lock:
            con.execute(
                "INSERT INTO docs (id,title,author,filename,pages,n_paras) "
                "VALUES (?,?,?,?,?,?)",
                (doc_id, title or path.stem, author, path.name,
                 data["meta"]["pages"], len(data["paras"])))
            con.execute(
                "UPDATE docs SET path=?, scanned=?, spread=?, field=? "
                "WHERE id=?",
                (str(path), 1 if scanned else 0,
                 1 if data["meta"].get("spread") else 0, field, doc_id))
            con.executemany(
                "INSERT INTO paras (doc_id,ord,page,kind,en,h,boxes) "
                "VALUES (?,?,?,?,?,?,?)",
                [(doc_id, p["order"], p["page"], p["kind"], p["text"],
                  db.text_hash(p["text"]), json.dumps(p.get("spans") or []))
                 for p in data["paras"]])
            con.commit()
        job.update(state="done", doc_id=doc_id, pages=data["meta"]["pages"],
                   paras=len(data["paras"]), title=title or path.stem,
                   field=field, msg="추가 완료", progress=1.0)
    except Exception as e:                      # noqa: BLE001
        job.update(state="error", msg=f"{type(e).__name__}: {e}")


@app.post("/api/upload")
async def upload(file: UploadFile = File(...)):
    name = re.sub(r'[/\\]', "_", file.filename or "document.pdf")
    if not name.lower().endswith(".pdf"):
        return JSONResponse({"error": "PDF 파일만 받습니다"}, status_code=400)
    if con.execute("SELECT 1 FROM docs WHERE filename=?", (name,)).fetchone():
        return JSONResponse({"error": "이미 추가된 문서입니다"}, status_code=409)

    PDF_DIR.mkdir(parents=True, exist_ok=True)
    dest = PDF_DIR / name
    with dest.open("wb") as f:
        shutil.copyfileobj(file.file, f)

    job_id = uuid.uuid4().hex[:12]
    JOBS[job_id] = {"state": "running", "msg": "파일 확인 중…",
                    "progress": 0.0, "ocr": None}
    threading.Thread(target=_run_ingest, args=(job_id, dest), daemon=True).start()
    return {"job": job_id}


@app.get("/api/job/{job_id}")
def job_status(job_id: str):
    j = JOBS.get(job_id)
    if not j:
        raise HTTPException(404, "작업을 찾을 수 없습니다")
    return j


@app.get("/api/search")
def search(q: str, limit: int = 60):
    if len(q.strip()) < 2:
        return []
    like = f"%{q.strip()}%"
    rows = con.execute("""
        SELECT p.doc_id, p.ord, p.page, p.en, t.ko, d.title
        FROM paras p JOIN docs d ON d.id=p.doc_id
        LEFT JOIN trans t ON t.h=p.h
                          AND t.field=COALESCE(d.field,'general')
        WHERE p.kind='body' AND (p.en LIKE ? OR t.ko LIKE ?)
        ORDER BY p.doc_id, p.ord LIMIT ?""", (like, like, limit)).fetchall()
    ql = q.strip().lower()
    out = []
    for r in rows:
        d = dict(r)
        inEn = ql in (d.get("en") or "").lower()
        inKo = ql in (d.get("ko") or "").lower()
        d["where"] = "both" if inEn and inKo else ("en" if inEn else "ko")
        out.append(d)
    return out


@app.get("/api/status")
def status():
    tot = con.execute(
        "SELECT COUNT(*) c FROM paras WHERE kind IN ('body','heading')").fetchone()["c"]
    done = con.execute("""SELECT COUNT(*) c FROM paras p
        JOIN docs d ON d.id=p.doc_id
        JOIN trans t ON t.h=p.h AND t.field=COALESCE(d.field,'general')
        WHERE p.kind IN ('body','heading')""").fetchone()["c"]
    eng = engine()
    return {"ai": ai.backend(eng), "cli": ai.find_cli(),
            "engine": eng,
            "engine_label": next((l for k, l, _ in ai.ENGINES if k == eng), eng),
            "total": tot, "translated": done,
            "hour": _window(1), "session5h": _window(5), "week": _window(168),
            "model_chat": setting("model_chat"),
            "model_trans": setting("model_trans"),
            # 화면 이름표는 '지금 무엇으로 답하는지'를 보여야 한다.
            # 엔진과 무관하게 Claude 모델을 띄우면 GPT 로 답하는데
            # opus-5 라고 적히는 꼴이 된다.
            "model_now": (setting("gpt_model") or "GPT"
                          if eng == "gpt" else setting("model_chat")),
            # Codex 는 사용량을 알려주지 않는다. 없는 수치를 보여주면 안 된다.
            "usage_known": eng != "gpt"}


@app.get("/")
def index():
    """첫 화면. app.js/style.css 주소에 파일이 바뀐 시각을 붙여 내보낸다.

    헤더만으로는 브라우저가 <script src> 를 계속 캐시해서, 앱을 고쳐도
    옛 화면이 그대로 뜨는 일이 생긴다. 주소 자체를 바꾸면 확실하다.
    """
    html = (STATIC / "index.html").read_text(encoding="utf-8")
    for name in ("app.js", "style.css"):
        f = STATIC / name
        v = int(f.stat().st_mtime) if f.exists() else 0
        html = html.replace(f'"{name}"', f'"{name}?v={v}"')
    return HTMLResponse(html, headers={"Cache-Control": "no-store"})


@app.middleware("http")
async def no_cache(request, call_next):
    """화면 파일은 캐시하지 않는다.

    앱을 고쳐도 브라우저가 옛 파일을 붙들고 있으면 고친 게 반영되지 않아
    원인을 찾기 어렵다. 로컬에서만 도는 서버라 캐시 이득도 없다.
    """
    resp = await call_next(request)
    path = request.url.path
    if path == "/" or path.endswith((".js", ".css", ".html")):
        resp.headers["Cache-Control"] = "no-store, must-revalidate"
    return resp


app.mount("/", StaticFiles(directory=STATIC, html=True), name="static")
