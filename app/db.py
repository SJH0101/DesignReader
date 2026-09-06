"""SQLite 저장소 — 문단, 번역, 해설 캐시, 읽기 진도."""
from __future__ import annotations

import hashlib
import json
import re
import sqlite3
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS docs (
    id        INTEGER PRIMARY KEY,
    title     TEXT NOT NULL,
    author    TEXT,
    filename  TEXT NOT NULL,
    pages     INTEGER,
    n_paras   INTEGER,
    path      TEXT,          -- 원본 PDF 실제 경로 (원본 보기용)
    scanned   INTEGER DEFAULT 0,
    spread    INTEGER DEFAULT 0,  -- 두 쪽이 한 장에 스캔된 가로 펼침면
    split     REAL DEFAULT 0.5,   -- 책등의 가로 위치 (0~1)
    field     TEXT DEFAULT 'general'  -- 분야. 용어집과 말투를 고른다
);

CREATE TABLE IF NOT EXISTS paras (
    doc_id  INTEGER NOT NULL,
    ord     INTEGER NOT NULL,
    page    INTEGER NOT NULL,
    kind    TEXT NOT NULL,
    en      TEXT NOT NULL,
    h       TEXT NOT NULL,
    boxes   TEXT,           -- 지면 위 위치 [[page,x0,y0,x1,y1], ...] 0~1 정규화
    PRIMARY KEY (doc_id, ord)
);
CREATE INDEX IF NOT EXISTS idx_paras_h ON paras(h);
CREATE INDEX IF NOT EXISTS idx_paras_page ON paras(doc_id, page);

-- 지면 위에 투명한 글자를 얹기 위한 줄 단위 좌표
CREATE TABLE IF NOT EXISTS lines (
    doc_id INTEGER NOT NULL,
    page   INTEGER NOT NULL,
    ord    INTEGER NOT NULL,      -- 이 줄이 속한 문단
    idx    INTEGER NOT NULL,
    text   TEXT NOT NULL,
    x0 REAL, y0 REAL, x1 REAL, y1 REAL
);
CREATE INDEX IF NOT EXISTS idx_lines_page ON lines(doc_id, page);

CREATE TABLE IF NOT EXISTS trans (
    h       TEXT NOT NULL,
    field   TEXT NOT NULL DEFAULT 'general',
    ko      TEXT NOT NULL,
    pairs   TEXT,           -- 문장 짝 [[영어, 한국어], ...]
    model   TEXT,
    engine  TEXT,           -- 어느 AI 가 만든 번역인지 (claude / gpt)
    ts      TEXT DEFAULT (datetime('now')),
    PRIMARY KEY (h, field)
);

-- 클릭 해설 캐시: 같은 용어를 다시 눌러도 API를 재호출하지 않는다
CREATE TABLE IF NOT EXISTS explain (
    key     TEXT PRIMARY KEY,
    doc_id  INTEGER,
    ord     INTEGER,
    term    TEXT,
    answer  TEXT NOT NULL,
    ts      TEXT DEFAULT (datetime('now'))
);

-- 사용자가 저장한 단어장
-- 중요 문장 기록: 드래그한 대목을 원문·번역·메모와 함께 남긴다
CREATE TABLE IF NOT EXISTS notes (
    id      INTEGER PRIMARY KEY AUTOINCREMENT,
    text    TEXT NOT NULL,          -- 드래그한 그대로
    en      TEXT,                   -- 대응하는 원문
    ko      TEXT,                   -- 대응하는 번역
    memo    TEXT,
    doc_id  INTEGER,
    ord     INTEGER,
    page    INTEGER,
    sis     TEXT,                   -- 걸친 문장 번호 [0,1,...]
    kfrom   INTEGER,                -- 번역문 안에서 드래그한 시작 글자
    kto     INTEGER,                -- 끝 글자
    ts      TEXT DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS folders (
    id   INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    ord  INTEGER DEFAULT 0
);

-- 호출 하나하나를 시각과 함께 남긴다.
-- 요금제 잔여량은 CLI 가 알려주지 않으므로, 우리가 쓴 양과 속도만 잰다.
CREATE TABLE IF NOT EXISTS usage_log (
    id    INTEGER PRIMARY KEY AUTOINCREMENT,
    ts    TEXT DEFAULT (datetime('now')),
    kind  TEXT,                     -- translate | chat
    inp   INTEGER DEFAULT 0,
    outp  INTEGER DEFAULT 0,
    cost  REAL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_usage_ts ON usage_log(ts);

CREATE TABLE IF NOT EXISTS settings (
    k  TEXT PRIMARY KEY,
    v  TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS progress (
    doc_id  INTEGER PRIMARY KEY,
    ord     INTEGER NOT NULL,
    ts      TEXT DEFAULT (datetime('now'))
);
"""

# 파일명 -> 보기 좋은 제목/저자
def parse_name(fn: str) -> tuple[int, str, str]:
    stem = re.sub(r'\.pdf$', '', fn, flags=re.I)
    m = re.match(r'\s*(\d+)\.\s*(.*)', stem)
    num, rest = (int(m.group(1)), m.group(2)) if m else (999, stem)
    if "_" in rest:
        head, _, tail = rest.partition("_")
        # 앞부분이 사람 이름처럼 짧으면 저자로 본다
        if len(head) < 46 and not head.lower().startswith(("the ", "a ", "on ")):
            return num, tail.replace("_", " — ").strip(), head.strip()
    return num, rest.replace("_", " — ").strip(), ""


def text_hash(t: str) -> str:
    """번역을 원문에 묶는 키. 공백 차이는 무시한다."""
    norm = re.sub(r'\s+', ' ', t).strip().lower()
    return hashlib.sha1(norm.encode("utf-8")).hexdigest()


def connect(path: Path) -> sqlite3.Connection:
    path.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(path, check_same_thread=False)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA journal_mode=WAL")
    con.executescript(SCHEMA)
    have = {r[1] for r in con.execute("PRAGMA table_info(docs)")}
    for col, decl in (("path", "TEXT"), ("scanned", "INTEGER DEFAULT 0"),
                      ("folder_id", "INTEGER"), ("spread", "INTEGER DEFAULT 0"),
                      ("split", "REAL DEFAULT 0.5"),
                      ("field", "TEXT DEFAULT 'general'")):
        if col not in have:
            con.execute(f"ALTER TABLE docs ADD COLUMN {col} {decl}")
            if col == "field":
                # 분야가 없던 시절의 문서는 전부 디자인 설정으로 번역해 둔 것이다.
                # general 로 두면 쌓아둔 번역이 딴 칸에 있는 셈이 되어 사라진다.
                con.execute("UPDATE docs SET field='design'")
    pcols = {r[1] for r in con.execute("PRAGMA table_info(paras)")}
    if "boxes" not in pcols:
        con.execute("ALTER TABLE paras ADD COLUMN boxes TEXT")
    ncols = {r[1] for r in con.execute("PRAGMA table_info(notes)")}
    for col in ("sis TEXT", "kfrom INTEGER", "kto INTEGER"):
        name = col.split()[0]
        if name not in ncols:
            con.execute(f"ALTER TABLE notes ADD COLUMN {col}")
    tcols = {r[1] for r in con.execute("PRAGMA table_info(trans)")}
    if "pairs" not in tcols:
        con.execute("ALTER TABLE trans ADD COLUMN pairs TEXT")
    if "engine" not in tcols:
        # 어느 AI 가 만든 번역인지 남겨둔다. 키는 아니다 —
        # 엔진을 바꿨다고 멀쩡한 번역을 다시 받을 이유는 없다.
        con.execute("ALTER TABLE trans ADD COLUMN engine TEXT")
    if "field" not in tcols:
        # 기본키를 (h) 에서 (h, field) 로 넓힌다. SQLite 는 기본키를 못 고치므로
        # 새 표를 만들어 옮긴다. 이미 있던 번역은 전부 디자인 설정으로 받은
        # 것이므로 design 으로 표시해 둔다.
        con.executescript("""
            CREATE TABLE trans_new (
                h TEXT NOT NULL,
                field TEXT NOT NULL DEFAULT 'general',
                ko TEXT NOT NULL, pairs TEXT, model TEXT,
                ts TEXT DEFAULT (datetime('now')),
                PRIMARY KEY (h, field));
            INSERT INTO trans_new (h, field, ko, pairs, model, ts)
                SELECT h, 'design', ko, pairs, model, ts FROM trans;
            DROP TABLE trans;
            ALTER TABLE trans_new RENAME TO trans;
        """)
    con.commit()
    return con


def ingest(con: sqlite3.Connection, extracted_dir: Path,
           pdf_dir: Path | None = None) -> None:
    """추출 JSON을 DB에 적재한다. 번역/해설은 보존한다."""
    files = sorted(extracted_dir.glob("*.json"), key=lambda p: int(p.stem))
    for jf in files:
        data = json.loads(jf.read_text(encoding="utf-8"))
        meta, paras = data["meta"], data["paras"]
        num, title, author = parse_name(meta["file"])
        src = str(pdf_dir / meta["file"]) if pdf_dir else None
        con.execute(
            "INSERT INTO docs (id,title,author,filename,pages,n_paras,path) "
            "VALUES (?,?,?,?,?,?,?) "
            "ON CONFLICT(id) DO UPDATE SET title=excluded.title, author=excluded.author, "
            "filename=excluded.filename, pages=excluded.pages, "
            "n_paras=excluded.n_paras, path=COALESCE(excluded.path, docs.path)",
            (num, title, author, meta["file"], meta["pages"], len(paras), src))
        con.execute("DELETE FROM paras WHERE doc_id=?", (num,))
        con.executemany(
            "INSERT INTO paras (doc_id,ord,page,kind,en,h,boxes) "
            "VALUES (?,?,?,?,?,?,?)",
            [(num, p["order"], p["page"], p["kind"], p["text"],
              text_hash(p["text"]), json.dumps(p.get("spans") or []))
             for p in paras])
    con.commit()


if __name__ == "__main__":
    import sys
    root = Path(__file__).resolve().parent.parent
    con = connect(root / "data" / "reader.db")
    import sys as _s
    pdfs = Path(_s.argv[1]) if len(_s.argv) > 1 else None
    ingest(con, root / "data" / "extracted", pdfs)
    rows = con.execute(
        "SELECT d.id,d.title,d.pages,"
        " (SELECT COUNT(*) FROM paras p WHERE p.doc_id=d.id AND p.kind='body') b,"
        " (SELECT COUNT(*) FROM paras p JOIN trans t ON t.h=p.h"
        "   WHERE p.doc_id=d.id) t"
        " FROM docs d ORDER BY d.id").fetchall()
    for r in rows:
        print(f"  #{r['id']:<3} {r['pages']:>4}p  본문 {r['b']:>4}  번역 {r['t']:>4}  {r['title'][:46]}")
    print(f"\n총 {len(rows)}개 문서")
