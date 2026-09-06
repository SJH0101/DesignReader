"""스캔 PDF 글자 인식 — macOS 내장 Vision 사용.

별도 설치가 필요 없고, 오프라인으로 돌며, 토큰을 쓰지 않는다.
(Claude 경유 전사는 저작물 보호 필터에 걸려 쓸 수 없다.)
"""
from __future__ import annotations

import re
import tempfile
from collections import Counter
from pathlib import Path

import pymupdf

SENT_END = re.compile(r'[.!?][")\']?\s*$')
# 쪽번호만 있는 줄
PAGENUM = re.compile(r'^\s*[\[\(]?\s*(?:\d{1,4}|[ivxlcdm]{1,7})\s*[\]\)]?\s*$', re.I)

try:
    import Quartz
    import Vision
    from Foundation import NSURL
    AVAILABLE = True
except ImportError:                                   # macOS 밖
    AVAILABLE = False


def page_lines(png_path: Path, langs=("en-US",)) -> list[dict]:
    """이미지 한 장에서 줄 단위로 글자를 읽는다. 좌표는 좌상단 기준으로 바꾼다."""
    url = NSURL.fileURLWithPath_(str(png_path))
    src = Quartz.CGImageSourceCreateWithURL(url, None)
    if src is None:
        return []
    img = Quartz.CGImageSourceCreateImageAtIndex(src, 0, None)
    if img is None:
        return []

    req = Vision.VNRecognizeTextRequest.alloc().init()
    req.setRecognitionLevel_(0)            # 0=accurate(신경망), 1=fast(모양매칭)
    req.setUsesLanguageCorrection_(True)
    req.setRecognitionLanguages_(list(langs))

    handler = Vision.VNImageRequestHandler.alloc().initWithCGImage_options_(img, None)
    ok, _ = handler.performRequests_error_([req], None)
    if not ok:
        return []

    out = []
    for obs in (req.results() or []):
        cand = obs.topCandidates_(1)
        if not cand:
            continue
        txt = cand[0].string()
        if not txt or not txt.strip():
            continue
        bb = obs.boundingBox()          # 정규화, 원점 좌하단
        x0 = bb.origin.x
        y0 = 1.0 - (bb.origin.y + bb.size.height)      # 좌상단 기준으로 뒤집는다
        out.append({
            "text": txt,
            "x0": x0, "x1": x0 + bb.size.width,
            "y0": y0, "y1": y0 + bb.size.height,
            "conf": float(cand[0].confidence()),
        })
    out.sort(key=lambda l: (round(l["y0"], 3), l["x0"]))
    return out


def _join(prev: str, nxt: str) -> str:
    prev, nxt = prev.rstrip(), nxt.lstrip()
    if prev.endswith("-") and not prev.endswith("--") and nxt[:1].islower():
        return prev[:-1] + nxt
    return prev + " " + nxt


def lines_to_paras(lines: list[dict]) -> list[tuple[str, list]]:
    """줄을 문단으로 묶는다. 줄간격이 벌어지거나 들여쓰기가 나오면 끊는다.

    문단마다 그 문단을 이루는 줄들의 좌표도 함께 돌려준다. 지면 위에
    형광펜을 칠하려면 이 좌표가 필요하다. Vision 은 이미 0~1 로 정규화된
    값을 주므로 그대로 쓴다.
    """
    if not lines:
        return []
    heights = sorted(l["y1"] - l["y0"] for l in lines)
    lh = heights[len(heights) // 2] or 0.02
    left = min(l["x0"] for l in lines)

    paras: list[tuple[str, list]] = []
    buf = ""
    box: list = []
    prev = None

    def box_of(l):
        # 글자 층을 그리려면 줄의 글자도 좌표와 함께 필요하다
        return [round(l["x0"], 5), round(l["y0"], 5),
                round(l["x1"], 5), round(l["y1"], 5), l["text"]]

    for l in lines:
        if prev is None:
            buf = l["text"]; box = [box_of(l)]
        else:
            gap = l["y0"] - prev["y1"]
            indent = l["x0"] - left > 0.035
            short_prev = prev["x1"] < max(x["x1"] for x in lines) - 0.10
            ended = bool(SENT_END.search(buf[-3:]))
            if (l.get("listrow") or prev.get("listrow")
                    or gap > lh * 1.15 or (indent and ended)
                    or (short_prev and ended and gap > lh * .4)):
                if buf.strip():
                    paras.append((buf.strip(), box))
                buf = l["text"]; box = [box_of(l)]
            else:
                buf = _join(buf, l["text"])
                box.append(box_of(l))
        prev = l
    if buf.strip():
        paras.append((buf.strip(), box))
    return [(re.sub(r'\s+', ' ', t).strip(), b)
            for t, b in paras if len(t.strip()) > 1]


def _running_heads(pages: list[list[dict]]) -> set[str]:
    """여러 쪽 같은 자리에 반복되는 줄 = 머릿말/꼬릿말."""
    top, bot = Counter(), Counter()
    for lines in pages:
        for l in lines:
            key = re.sub(r'\d+', '#', l["text"].strip())[:60]
            if len(key) < 3:
                continue
            if l["y0"] < 0.085:
                top[key] += 1
            elif l["y1"] > 0.925:
                bot[key] += 1
    thr = max(3, len(pages) * 0.25)
    return {k for k, c in list(top.items()) + list(bot.items()) if c >= thr}


def _columns(lines: list[dict]) -> int:
    """줄이 좌우로 갈려 있으면 2단으로 본다."""
    if len(lines) < 8:
        return 1
    crossing = sum(1 for l in lines if l["x0"] < 0.46 and l["x1"] > 0.54)
    if crossing > len(lines) * 0.18:
        return 1
    left = sum(1 for l in lines if l["x1"] <= 0.54)
    right = sum(1 for l in lines if l["x0"] >= 0.46)
    if left >= 4 and right >= 4 and min(left, right) >= len(lines) * 0.22:
        return 2
    return 1


def _merge_baselines(lines: list[dict]) -> list[dict]:
    """같은 가로줄에 흩어진 조각을 한 줄로 합친다.

    목차·색인은 '제목 ......... 쪽번호' 처럼 한 줄이 좌우로 멀리 떨어져 있다.
    합치지 않으면 쪽번호 무리가 다른 단으로 잘못 잡혀, 제목은 제목끼리
    번호는 번호끼리 뭉쳐버린다.
    """
    if not lines:
        return []
    hs = sorted(l["y1"] - l["y0"] for l in lines)
    lh = hs[len(hs) // 2] or 0.02
    ordered = sorted(lines, key=lambda l: (round(l["y0"], 4), l["x0"]))

    out: list[dict] = []
    for l in ordered:
        if out:
            prev = out[-1]
            same_row = abs(l["y0"] - prev["y0"]) < lh * 0.55
            after = l["x0"] >= prev["x1"] - 0.01
            gap = l["x0"] - prev["x1"]
            narrow = (l["x1"] - l["x0"]) < 0.16      # 쪽번호처럼 짧은 조각
            # 본문 2단을 잘못 잇지 않도록: 붙어 있거나, 짧은 조각일 때만 합친다
            if same_row and after and (gap < 0.05 or narrow):
                prev["text"] = prev["text"].rstrip() + " " + l["text"].lstrip()
                prev["x1"] = max(prev["x1"], l["x1"])
                prev["y1"] = max(prev["y1"], l["y1"])
                prev["boxes"] = prev.get("boxes", [prev]) + [l]
                if gap > 0.10:
                    prev["listrow"] = True           # 목차 같은 한 항목
                continue
        out.append(dict(l))
    return out


def _clean_page(lines: list[dict], heads: set[str]) -> list[dict]:
    keep = []
    for l in lines:
        key = re.sub(r'\d+', '#', l["text"].strip())[:60]
        if key in heads:
            continue
        # 여백에 홀로 있는 쪽번호
        if PAGENUM.match(l["text"]) and (l["y0"] < 0.11 or l["y1"] > 0.89):
            continue
        keep.append(l)
    if not keep:
        return []
    # 줄을 먼저 합쳐야 단 감지가 어긋나지 않는다
    keep = _merge_baselines(keep)
    ncol = _columns(keep)
    for l in keep:
        l["col"] = 1 if (ncol == 2 and l["x0"] >= 0.46) else 0
    keep.sort(key=lambda l: (l["col"], round(l["y0"], 4), l["x0"]))
    return keep


def is_spread(page) -> bool:
    """가로로 길면 두 쪽을 한 장에 스캔한 펼침면으로 본다."""
    r = page.rect
    return r.width > r.height * 1.15


def find_gutter(pages: list[list[dict]]) -> float:
    """펼침면에서 두 쪽을 가르는 책등의 가로 위치를 찾는다.

    스캔은 여백이 한쪽으로 치우치는 일이 많아 정중앙으로 자르면 글자가
    잘린다. 글자가 전혀 없는 세로 띠 중 가운데에 가장 가까운 것을 고른다.
    """
    BINS = 400
    ratios = []
    for lines in pages:
        if len(lines) < 10:
            continue
        covered = [False] * BINS
        for l in lines:
            a = max(0, int(l["x0"] * BINS))
            b = min(BINS - 1, int(l["x1"] * BINS))
            for i in range(a, b + 1):
                covered[i] = True
        best, run_start = None, None
        for i in range(int(BINS * 0.32), int(BINS * 0.68) + 1):
            if not covered[i]:
                if run_start is None:
                    run_start = i
            else:
                if run_start is not None:
                    if best is None or (i - run_start) > (best[1] - best[0]):
                        best = (run_start, i)
                    run_start = None
        if run_start is not None:
            if best is None or (int(BINS * 0.68) + 1 - run_start) > (best[1] - best[0]):
                best = (run_start, int(BINS * 0.68) + 1)
        if best and best[1] - best[0] >= 3:
            ratios.append((best[0] + best[1]) / 2 / BINS)
    if not ratios:
        return 0.5
    ratios.sort()
    return ratios[len(ratios) // 2]


def _half(lines: list[dict], right: bool, split: float = 0.5) -> list[dict]:
    """펼침면의 한쪽만 골라 좌표를 그 반쪽 기준으로 다시 잡는다."""
    out = []
    lo, hi = (split, 1.0) if right else (0.0, split)
    span = max(hi - lo, 1e-6)
    for l in lines:
        cx = (l["x0"] + l["x1"]) / 2
        if (cx >= split) != right:
            continue
        m = dict(l)
        m["x0"] = min(max((l["x0"] - lo) / span, 0.0), 1.0)
        m["x1"] = min(max((l["x1"] - lo) / span, 0.0), 1.0)
        out.append(m)
    return out


def ocr_pdf(pdf_path: Path, zoom: float = 3.5, progress=None) -> dict:
    """스캔 PDF 전체를 읽어 extract.py 와 같은 형태로 돌려준다."""
    if not AVAILABLE:
        raise RuntimeError("이 기능은 macOS에서만 동작합니다 (Vision 프레임워크 필요)")

    doc = pymupdf.open(pdf_path)
    n = len(doc)
    spread = is_spread(doc[0]) if n else False

    # 1단계: 쪽마다 글자를 읽는다
    per_page: list[list[dict]] = []
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td) / "pg.png"
        for pno in range(n):
            doc[pno].get_pixmap(matrix=pymupdf.Matrix(zoom, zoom)).save(tmp)
            per_page.append(page_lines(tmp))
            if progress:
                progress(pno + 1, n)

    # 2단계: 머릿말·꼬릿말은 여러 쪽을 견줘야 알 수 있다
    heads = _running_heads(per_page)
    split = find_gutter(per_page) if spread else 0.5

    paras: list[dict] = []
    order = 0

    def emit(logical_page: int, lines: list[dict]):
        """한 쪽의 줄들을 단별로 나눠 문단으로 만든다."""
        nonlocal order
        kept = _clean_page(lines, heads)
        if not kept:
            return
        # 단이 둘이면 단마다 따로 묶어야 한다. 한꺼번에 처리하면
        # 들여쓰기·줄끝 판단이 옆 단에 오염돼 문단이 통째로 뭉친다.
        cols = sorted({l.get("col", 0) for l in kept})
        for c in cols:
            group = [l for l in kept if l.get("col", 0) == c]
            for text, boxes in lines_to_paras(group):
                paras.append({
                    "id": f"p{order:05d}", "page": logical_page, "order": order,
                    "kind": "body", "text": text,
                    "bbox": (boxes[0][:4] if boxes else [0, 0, 0, 0]),
                    "spans": [[logical_page, *b[:4]] for b in boxes],
                    "lines": [[logical_page, *b[:4], b[4]] for b in boxes],
                })
                order += 1

    for pno, lines in enumerate(per_page, start=1):
        if spread:
            emit((pno - 1) * 2 + 1, _half(lines, False, split))
            emit((pno - 1) * 2 + 2, _half(lines, True, split))
        else:
            emit(pno, lines)

    pages_out = n * 2 if spread else n
    doc.close()
    return {"meta": {"file": pdf_path.name, "pages": pages_out,
                     "body_size": 0, "n_paras": len(paras),
                     "ocr": True, "spread": spread, "split": split},
            "paras": paras}


def needs_ocr(pdf_path: Path) -> bool:
    """텍스트 레이어가 사실상 없으면 True."""
    doc = pymupdf.open(pdf_path)
    n = len(doc)
    idx = [int(n * r) for r in (0.15, 0.35, 0.55, 0.75)] if n > 4 else range(n)
    chars = sum(len(doc[i].get_text().strip()) for i in idx)
    doc.close()
    return chars / max(len(list(idx)), 1) < 80
