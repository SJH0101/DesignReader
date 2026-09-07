"""PDF -> 문단 단위 구조화 추출.

학술 PDF의 골칫거리를 처리한다:
  - 페이지별 단(column) 자동 감지 (1단/2단 혼재)
  - 머리말/꼬리말/쪽번호 제거 (여러 쪽에 반복되는 줄)
  - 각주 분리 (본문보다 작은 폰트)
  - 줄바꿈 병합 + 하이픈 복원 (experi-\nence -> experience)
  - 쪽을 넘어가는 문단 이어붙이기
"""
from __future__ import annotations

import json
import re
import statistics
import sys
from collections import Counter
from dataclasses import dataclass, asdict
from pathlib import Path

import pymupdf

# 문장이 끝났다고 볼 수 있는 종결부
SENT_END = re.compile(r'[.!?][")\']?\s*$|[:;]\s*$')
# 쪽번호만 있는 줄
PAGENUM = re.compile(r'^\s*[\[\(]?\s*(?:\d{1,4}|[ivxlcdm]{1,7})\s*[\]\)]?\s*$', re.I)


@dataclass
class Para:
    id: str
    page: int          # 1-based
    order: int         # 문서 전체 순서
    kind: str          # body | heading | note | caption
    text: str
    bbox: list         # [x0, y0, x1, y1] 첫 조각 기준
    spans: list        # 여러 쪽/단에 걸친 경우 [[page, x0,y0,x1,y1], ...]


def _lines_of(page) -> list[dict]:
    """페이지에서 줄 단위 정보를 뽑는다."""
    out = []
    d = page.get_text("dict")
    for blk in d.get("blocks", []):
        if blk.get("type") != 0:
            continue
        for ln in blk.get("lines", []):
            raw = ln.get("spans", [])
            # 공백만 있는 span 도 글자에는 반드시 포함해야 한다.
            # 빼면 단어가 붙어버린다 (Nowheredoweencounter).
            txt = "".join(s.get("text", "") for s in raw)
            if not txt.strip():
                continue
            spans = [s for s in raw if s.get("text", "").strip()]
            if not spans:
                continue
            sizes = [round(s["size"], 1) for s in spans]
            nb = sum(len(s["text"]) for s in spans
                     if "bold" in s.get("font", "").lower()
                     or "black" in s.get("font", "").lower())
            nall = sum(len(s["text"]) for s in spans) or 1
            x0 = min(s["bbox"][0] for s in spans)
            y0 = min(s["bbox"][1] for s in spans)
            x1 = max(s["bbox"][2] for s in spans)
            y1 = max(s["bbox"][3] for s in spans)
            out.append({
                "text": txt,
                "size": statistics.median(sizes),
                "bold": nb / nall > 0.6,
                "x0": x0, "y0": y0, "x1": x1, "y1": y1,
                "blk": blk["number"],
            })
    return out


def _detect_columns(lines: list[dict], width: float) -> int:
    """줄의 x 시작점 분포로 단 수를 추정한다."""
    if len(lines) < 8:
        return 1
    mid = width / 2
    # 페이지 중앙을 가로지르는 줄이 많으면 1단
    crossing = sum(1 for l in lines if l["x0"] < mid - 12 and l["x1"] > mid + 12)
    if crossing > len(lines) * 0.18:
        return 1
    left = sum(1 for l in lines if l["x1"] <= mid + 12)
    right = sum(1 for l in lines if l["x0"] >= mid - 12)
    # 양쪽에 고르게 있어야 2단
    if left >= 4 and right >= 4 and min(left, right) >= len(lines) * 0.22:
        return 2
    return 1


def _find_running_heads(pages_lines: list[list[dict]], page_h: float) -> set[str]:
    """여러 쪽에 반복 등장하는 머리말/꼬리말 텍스트를 찾는다."""
    top, bot = Counter(), Counter()
    for lines in pages_lines:
        for l in lines:
            key = re.sub(r'\d+', '#', l["text"].strip())[:80]
            if len(key) < 3:
                continue
            if l["y0"] < page_h * 0.09:
                top[key] += 1
            elif l["y1"] > page_h * 0.92:
                bot[key] += 1
    n = max(len(pages_lines), 1)
    thr = max(3, n * 0.25)
    return {k for k, c in list(top.items()) + list(bot.items()) if c >= thr}


def _join(prev: str, nxt: str) -> str:
    """줄 이어붙이기 — 하이픈으로 끊긴 단어는 복원."""
    prev = prev.rstrip()
    nxt = nxt.lstrip()
    if prev.endswith(("-", "‐", "­")) and not prev.endswith(("--", "—")):
        # 소문자로 이어지면 한 단어가 쪼개진 것
        if nxt[:1].islower():
            return prev[:-1] + nxt
    return prev + " " + nxt


def _clean(t: str) -> str:
    t = t.replace("ﬁ", "fi").replace("ﬂ", "fl").replace("­", "")
    t = re.sub(r'\s+', ' ', t)
    return t.strip()


def extract(pdf_path: Path) -> dict:
    doc = pymupdf.open(pdf_path)
    pages_lines = [_lines_of(p) for p in doc]
    page_h = doc[0].rect.height if len(doc) else 792

    heads = _find_running_heads(pages_lines, page_h)

    # 본문 폰트 크기 = 전체에서 가장 흔한 크기
    size_counter = Counter()
    for lines in pages_lines:
        for l in lines:
            size_counter[round(l["size"], 1)] += len(l["text"])
    body_size = size_counter.most_common(1)[0][0] if size_counter else 10.0

    paras: list[Para] = []
    cur: dict | None = None
    order = 0

    def flush():
        nonlocal cur, order
        if cur is None:
            return
        txt = _clean(cur["text"])
        if len(txt) >= 2:
            paras.append(Para(
                id=f"p{order:05d}", page=cur["page"], order=order,
                kind=cur["kind"], text=txt,
                bbox=cur["bbox"], spans=cur["spans"],
            ))
            order += 1
        cur = None

    for pno, lines in enumerate(pages_lines, start=1):
        if not lines:
            continue
        w = doc[pno - 1].rect.width
        ncol = _detect_columns(lines, w)

        # 머리말/꼬리말/쪽번호 제거
        keep = []
        for l in lines:
            key = re.sub(r'\d+', '#', l["text"].strip())[:80]
            if key in heads:
                continue
            if PAGENUM.match(l["text"]) and (l["y0"] < page_h * 0.12 or l["y1"] > page_h * 0.88):
                continue
            keep.append(l)
        if not keep:
            continue

        blk_lines = Counter(l["blk"] for l in keep)

        # 크기 차이는 비율로 봐야 한다. ±1pt 라는 절대값은 본문이 10pt 인
        # 책에 맞춘 것이라, 본문이 24pt 인 발표자료에서는 조금만 작아도
        # 주석으로, 조금만 커도 제목으로 떨어진다. 그러면 슬라이드 전체가
        # 잘게 부서져 본문이 거의 남지 않는다.
        note_gap = max(0.9, body_size * 0.12)
        head_gap = max(1.0, body_size * 0.12)

        # 단 배정 후 읽기 순서로 정렬
        mid = w / 2
        for l in keep:
            l["col"] = 1 if (ncol == 2 and l["x0"] >= mid - 12) else 0
            # 본문/주석을 별도 스트림으로 나눈다. 여백에 참고문헌 단을 두는
            # 조판(She-Ji 등)에서 본문 줄 사이로 주석이 끼어드는 것을 막는다.
            l["stream"] = 1 if l["size"] < body_size - note_gap else 0
        keep.sort(key=lambda l: (l["stream"], l["col"], round(l["y0"], 1), l["x0"]))

        for l in keep:
            sz = l["size"]
            is_note = sz < body_size - note_gap
            # 제목: 글자가 크거나(확실), 굵으면서 블록이 2줄 이하일 때만.
            # 본문 중간의 굵은 강조어가 제목으로 새는 걸 막는다.
            big = sz > body_size + head_gap
            boldish = l["bold"] and blk_lines[l["blk"]] <= 2
            is_head = (big or boldish) and len(l["text"].strip()) < 120
            kind = "note" if is_note else ("heading" if is_head else "body")

            new_para = False
            if cur is None:
                new_para = True
            elif cur["kind"] != kind:
                new_para = True
            elif kind == "heading":
                # 이어지는 제목 줄은 한 덩어리로 합친다 (같은 크기 + 바로 아랫줄)
                gap = l["y0"] - cur["last_y1"]
                same_size = abs(sz - cur["size"]) < 0.6
                adjacent = -2 <= gap <= sz * 1.4
                new_para = not (same_size and adjacent and pno == cur["page"])
            else:
                # 들여쓰기 시작 or 이전 줄이 짧게 끝남(=문단 끝) => 새 문단
                indent = l["x0"] - cur["x_ref"] > 7
                short_prev = cur["last_x1"] < cur["x_max"] - 42
                gap = l["y0"] - cur["last_y1"]
                far = gap > cur["lh"] * 1.9 if cur["lh"] else False
                ended = bool(SENT_END.search(cur["text"][-3:]))
                if (indent and ended) or (short_prev and ended) or far:
                    new_para = True

            if new_para:
                flush()
                cur = {
                    "page": pno, "kind": kind, "text": l["text"],
                    "bbox": [l["x0"], l["y0"], l["x1"], l["y1"]],
                    "spans": [[pno, l["x0"], l["y0"], l["x1"], l["y1"]]],
                    "size": sz,
                    "x_ref": l["x0"], "x_max": l["x1"],
                    "last_x1": l["x1"], "last_y1": l["y1"],
                    "lh": None,
                }
            else:
                if cur["lh"] is None and l["y0"] > cur["last_y1"] - 2:
                    cur["lh"] = max(l["y0"] - cur["spans"][-1][2], 1)
                cur["text"] = _join(cur["text"], l["text"])
                cur["spans"].append([pno, l["x0"], l["y0"], l["x1"], l["y1"]])
                cur["x_ref"] = min(cur["x_ref"], l["x0"])
                cur["x_max"] = max(cur["x_max"], l["x1"])
                cur["last_x1"] = l["x1"]
                cur["last_y1"] = l["y1"]
    flush()

    # 너무 짧은 조각은 앞 문단에 흡수 (본문에 한함)
    merged: list[Para] = []
    for p in paras:
        if (merged and p.kind == "body" and merged[-1].kind == "body"
                and len(p.text) < 45 and not SENT_END.search(merged[-1].text[-3:])):
            merged[-1].text = _clean(_join(merged[-1].text, p.text))
            merged[-1].spans += p.spans
            continue
        merged.append(p)
    for i, p in enumerate(merged):
        p.order = i
        p.id = f"p{i:05d}"

    # 지면 위에 표시하려면 쪽 크기로 정규화한 좌표가 필요하다
    sizes = {i + 1: (doc[i].rect.width, doc[i].rect.height) for i in range(len(doc))}
    for para in merged:
        boxes = []
        for pg, x0, y0, x1, y1 in para.spans:
            W, H = sizes.get(pg, (612, 792))
            boxes.append([pg, round(x0 / W, 5), round(y0 / H, 5),
                          round(x1 / W, 5), round(y1 / H, 5)])
        para.spans = boxes

    meta = {
        "file": pdf_path.name,
        "pages": len(doc),
        "body_size": body_size,
        "n_paras": len(merged),
    }
    doc.close()
    return {"meta": meta, "paras": [asdict(p) for p in merged]}


def main():
    src = Path(sys.argv[1])
    out_dir = Path(sys.argv[2]) if len(sys.argv) > 2 else Path("data/extracted")
    out_dir.mkdir(parents=True, exist_ok=True)
    pdfs = sorted(src.glob("*.pdf"), key=lambda p: int(re.match(r'\d+', p.name).group()))
    for pdf in pdfs:
        res = extract(pdf)
        m = res["meta"]
        if m["n_paras"] == 0:
            print(f"  SKIP(스캔본) {pdf.name[:50]}")
            continue
        stem = re.match(r'\d+', pdf.name).group()
        (out_dir / f"{stem}.json").write_text(
            json.dumps(res, ensure_ascii=False, indent=1), encoding="utf-8")
        body = [p for p in res["paras"] if p["kind"] == "body"]
        avg = sum(len(p["text"]) for p in body) / max(len(body), 1)
        print(f"  #{stem:<3} {m['pages']:>4}p  문단 {m['n_paras']:>5} "
              f"(본문 {len(body):>5}, 평균 {avg:>5.0f}자)  {pdf.name[:44]}")


if __name__ == "__main__":
    main()
