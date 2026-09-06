"""일괄 번역기.

CLI 호출마다 기동 비용이 있으므로 문단을 묶어 보낸다. 다만 묶으면 응답이
어긋날 위험이 있어, 개수가 맞지 않으면 그 묶음은 문단별로 다시 번역한다.

사용:
    python -m app.translate            # 논문 전체
    python -m app.translate 0 1 2      # 특정 문서만
    python -m app.translate --docs 13 --pages 20-60   # 책의 일부만
"""
from __future__ import annotations

import argparse
import re
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import json

from . import ai, db, prompts

ROOT = Path(__file__).resolve().parent.parent
MARK = re.compile(r'^\s*\[(\d+)\]\s*$')

# 논문(짧은 글) 기본 대상 — 단행본은 챕터 지정이 필요해 제외
ARTICLES = [0, 1, 2, 3, 4, 5, 9, 10, 11, 12, 17, 18]

_lock = threading.Lock()
_done = 0
_failed = 0


def translate_aligned(text: str, title: str, author: str,
                      model: str | None = None,
                      field: str | None = None,
                      engine: str | None = None) -> tuple[str, list]:
    """문단을 문장 단위로 짝지어 번역한다.

    문장 수가 맞지 않으면 짝 없이 통번역으로 물러선다. 짝이 어긋난 채로
    두면 지면에 엉뚱한 곳이 표시되므로, 맞을 때만 짝을 남긴다.
    """
    sents = prompts.split_sentences(text)
    if len(sents) > 1:
        out = ai.ask(prompts.align_prompt(sents, title, author, field),
                     prompts.align_system(field), timeout=300, model=model,
                     engine=engine)
        ko_list = prompts.parse_aligned(out, len(sents))
        if ko_list:
            return " ".join(ko_list), list(map(list, zip(sents, ko_list)))

    ko = ai.ask(prompts.translate_prompt(text, title, author, field),
                prompts.translate_system(field), timeout=300, model=model,
                engine=engine)
    return ko, ([[sents[0], ko]] if len(sents) == 1 else [])


def build_batch_prompt(items: list[tuple[int, str]], title: str, author: str) -> str:
    head = (f"[출처] {author + ', ' if author else ''}{title}\n\n"
            f"아래 {len(items)}개의 영어 학술 문단을 각각 한국어로 번역하라.\n"
            f"출력 형식을 반드시 지켜라: 각 번역 앞에 [번호] 를 단독 줄로 쓰고,\n"
            f"그 아래 줄에 번역문만 쓴다. 번호는 빠짐없이 {len(items)}개 모두 나와야 한다.\n"
            f"원문에 없는 설명을 덧붙이지 마라.")
    gl = prompts.glossary_block(" ".join(t for _, t in items))
    body = "\n\n".join(f"[{i}]\n{t}" for i, t in items)
    return f"{head}{gl}\n\n---\n\n{body}"


def parse_batch(out: str, n: int) -> dict[int, str]:
    """[번호] 로 구분된 응답을 쪼갠다."""
    res: dict[int, str] = {}
    cur, buf = None, []
    for line in out.splitlines():
        m = MARK.match(line)
        if m:
            if cur is not None:
                res[cur] = "\n".join(buf).strip()
            cur, buf = int(m.group(1)), []
        elif cur is not None:
            buf.append(line)
    if cur is not None:
        res[cur] = "\n".join(buf).strip()
    return {k: v for k, v in res.items() if 1 <= k <= n and v}


# 한 번에 보낼 수 있는 양은 '출력' 이 정한다.
# 실측: 12,290자(18문단)는 성공, 35,839자(40문단)는 응답이 잘려 0/40 실패.
# 넉넉히 잡아 12,000자에서 끊는다.
MAX_CHARS = 12_000
MAX_PARAS = 30


def _align_once(texts: list[str], title: str, author: str,
                model: str | None, on_note,
                field: str | None = None,
                engine: str | None = None) -> dict[int, tuple[str, list]]:
    """한 묶음을 한 번의 호출로 번역한다."""
    units, sents_of = [], {}
    for k, t in enumerate(texts, start=1):
        ss = prompts.split_sentences(t) or [t]
        sents_of[k] = ss
        units.append((k, ss))

    ok: dict[int, tuple[str, list]] = {}
    if not units:
        return ok
    try:
        out = ai.ask(prompts.batch_align_prompt(units, title, author, field),
                     prompts.batch_align_system(field), timeout=900, model=model,
                     engine=engine)
        got = prompts.parse_batch_aligned(out)
        for k, ss in sents_of.items():
            g = got.get(k, {})
            if len(g) == len(ss) and set(g) == set(range(1, len(ss) + 1)):
                ko_list = [g[j] for j in range(1, len(ss) + 1)]
                ok[k] = (" ".join(ko_list), list(map(list, zip(ss, ko_list))))
    except ai.AIError as e:
        if on_note:
            on_note(f"묶음 실패 — 개별 번역으로 전환 ({str(e)[:60]})")
    return ok


def translate_many(texts: list[str], title: str, author: str,
                   model: str | None = None,
                   on_note=None,
                   field: str | None = None,
                   engine: str | None = None) -> dict[int, tuple[str, list]]:
    """여러 문단을 번역한다.

    호출 고정비가 커서 묶어 보내야 하지만, 너무 크면 응답이 잘려 통째로
    실패한다(실측: 12,290자 성공 / 35,839자 실패). 정확한 한계는 내용에
    따라 달라지므로 값을 못 박지 않는다. 대신 실패하면 반으로 쪼개
    다시 시도한다 — 한계를 몰라도 스스로 맞춰간다.
    """
    ok: dict[int, tuple[str, list]] = {}

    def attempt(idx: list[int], depth: int = 0) -> None:
        if not idx:
            return
        got = _align_once([texts[i] for i in idx], title, author, model,
                          on_note if depth == 0 else None, field=field,
                          engine=engine)
        done = {idx[pos - 1] for pos in got}
        for pos, i in enumerate(idx, start=1):
            if pos in got:
                ok[i + 1] = got[pos]

        missing = [i for i in idx if i not in done]
        if not missing:
            return
        if len(missing) == 1 or depth >= 4:
            # 더 쪼갤 수 없으면 문단별로 처리한다
            for i in missing:
                try:
                    ok[i + 1] = translate_aligned(texts[i], title, author, model)
                except ai.AIError as e:
                    if on_note:
                        on_note(f"문단 {i + 1} 실패: {str(e)[:70]}")
            return
        if on_note:
            on_note(f"{len(missing)}문단 실패 — 반으로 나눠 다시 시도")
        mid = len(missing) // 2
        attempt(missing[:mid], depth + 1)
        attempt(missing[mid:], depth + 1)

    # 처음에는 넉넉히 잡고, 안 되면 위에서 알아서 줄인다
    start, size, chunk = 0, 0, []
    for i, t in enumerate(texts):
        if chunk and (size + len(t) > MAX_CHARS or len(chunk) >= MAX_PARAS):
            attempt(chunk)
            chunk, size = [], 0
        chunk.append(i)
        size += len(t)
    attempt(chunk)
    return ok


def translate_group(con, rows: list, title: str, author: str, bar,
                    model: str | None = None) -> None:
    """문단 묶음 하나를 번역해 저장한다."""
    global _done, _failed
    ok = translate_many([r["en"] for r in rows], title, author, model, bar)
    with _lock:
        for k, r in enumerate(rows, start=1):
            if k not in ok:
                _failed += 1
                continue
            ko, pairs = ok[k]
            con.execute(
                "INSERT OR REPLACE INTO trans (h,ko,pairs,model) VALUES (?,?,?,?)",
                (r["h"], ko, json.dumps(pairs, ensure_ascii=False),
                 model or ai.MODEL))
            _done += 1
        con.commit()
    bar()


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("docs", nargs="*", type=int, help="문서 번호 (없으면 논문 전체)")
    ap.add_argument("--pages", help="쪽 범위, 예: 20-60")
    ap.add_argument("--batch", type=int, default=18, help="한 번에 보낼 문단 수")
    ap.add_argument("--workers", type=int, default=3, help="동시 요청 수")
    ap.add_argument("--limit", type=int, help="시험용: 처리할 문단 수 제한")
    ap.add_argument("--model", default=None,
                    help="번역 모델 (기본: 설정값). 예: claude-sonnet-5")
    args = ap.parse_args()

    if ai.backend() == "none":
        print("로컬 Claude를 찾지 못했습니다. `claude` 설치 후 로그인하세요.")
        sys.exit(1)

    con = db.connect(ROOT / "data" / "reader.db")
    targets = args.docs or ARTICLES

    where = ["p.doc_id IN (%s)" % ",".join("?" * len(targets)),
             "p.kind IN ('body','heading')",
             "t.h IS NULL",
             "length(p.en) > 12"]
    params: list = list(targets)
    if args.pages:
        a, _, b = args.pages.partition("-")
        where.append("p.page BETWEEN ? AND ?")
        params += [int(a), int(b or a)]

    sql = (f"SELECT p.doc_id, p.ord, p.en, p.h, d.title, d.author "
           f"FROM paras p JOIN docs d ON d.id=p.doc_id "
           f"LEFT JOIN trans t ON t.h=p.h "
           f"WHERE {' AND '.join(where)} ORDER BY p.doc_id, p.ord")
    rows = con.execute(sql, params).fetchall()
    if args.limit:
        rows = rows[:args.limit]
    if not rows:
        print("번역할 문단이 없습니다. (이미 전부 완료)")
        return

    total = len(rows)
    chars = sum(len(r["en"]) for r in rows)
    print(f"대상 {total}문단 / {chars/1000:.0f}K자 · 묶음 {args.batch} · 동시 {args.workers}")

    # 문서별로 묶는다 (출처 정보가 문서마다 다르므로)
    groups: list[tuple] = []
    by_doc: dict[int, list] = {}
    for r in rows:
        by_doc.setdefault(r["doc_id"], []).append(r)
    for did, rs in by_doc.items():
        title, author = rs[0]["title"], rs[0]["author"]
        for i in range(0, len(rs), args.batch):
            groups.append((rs[i:i + args.batch], title, author))

    t0 = time.time()

    def bar(msg: str = ""):
        el = time.time() - t0
        pct = _done / total * 100
        rate = _done / el if el > 0 else 0
        eta = (total - _done) / rate / 60 if rate > 0 else 0
        line = (f"\r  {_done:>5}/{total}  {pct:5.1f}%  "
                f"{el/60:5.1f}분 경과  남은 {eta:5.1f}분  실패 {_failed}   ")
        sys.stdout.write(line)
        if msg:
            sys.stdout.write("\n  ! " + msg + "\n")
        sys.stdout.flush()

    bar()
    with ThreadPoolExecutor(max_workers=args.workers) as ex:
        futs = [ex.submit(translate_group, con, g, t, a, bar, args.model)
                for g, t, a in groups]
        for f in futs:
            f.result()

    print(f"\n완료: {_done}문단 저장, 실패 {_failed}, "
          f"{(time.time()-t0)/60:.1f}분 소요")


if __name__ == "__main__":
    main()
