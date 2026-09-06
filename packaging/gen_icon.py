"""앱 아이콘 생성 — 펼친 책 위에 한/영 두 단."""
import subprocess
from pathlib import Path

import pymupdf

OUT = Path(__file__).resolve().parent / "build"
SIZES = [16, 32, 64, 128, 256, 512, 1024]


def draw(px: int) -> bytes:
    S = 1024.0
    doc = pymupdf.open()
    page = doc.new_page(width=S, height=S)
    sh = page.new_shape()

    # 둥근 사각 배경
    sh.draw_rect(pymupdf.Rect(0, 0, S, S), radius=0.225)
    sh.finish(fill=(0.153, 0.118, 0.086), color=None)

    # 책 펼침면
    pg = pymupdf.Rect(S * .13, S * .20, S * .87, S * .80)
    sh.draw_rect(pg, radius=0.03)
    sh.finish(fill=(0.976, 0.965, 0.945), color=None)

    # 가운데 접힘선
    sh.draw_line(pymupdf.Point(S / 2, S * .20), pymupdf.Point(S / 2, S * .80))
    sh.finish(color=(0.85, 0.83, 0.80), width=S * .006)

    # 왼쪽=영문(진한 줄), 오른쪽=한글(주황 줄)
    y = S * .285
    step = S * .072
    for i in range(6):
        w_l = [.30, .27, .31, .24, .29, .19][i]
        w_r = [.28, .30, .25, .28, .22, .26][i]
        sh.draw_line(pymupdf.Point(S * .175, y), pymupdf.Point(S * (.175 + w_l), y))
        sh.finish(color=(0.28, 0.25, 0.22), width=S * .026, lineCap=1)
        sh.draw_line(pymupdf.Point(S * .525, y), pymupdf.Point(S * (.525 + w_r), y))
        sh.finish(color=(0.65, 0.40, 0.16), width=S * .026, lineCap=1)
        y += step

    sh.commit()
    pix = page.get_pixmap(matrix=pymupdf.Matrix(px / S, px / S), alpha=True)
    data = pix.tobytes("png")
    doc.close()
    return data


def main() -> None:
    iconset = OUT / "icon.iconset"
    iconset.mkdir(parents=True, exist_ok=True)
    for s in SIZES:
        (iconset / f"icon_{s}x{s}.png").write_bytes(draw(s))
        if s <= 512:
            (iconset / f"icon_{s}x{s}@2x.png").write_bytes(draw(s * 2))
    icns = OUT / "icon.icns"
    subprocess.run(["iconutil", "-c", "icns", str(iconset), "-o", str(icns)],
                   check=True)
    print("아이콘 생성:", icns, f"({icns.stat().st_size/1024:.0f}KB)")


if __name__ == "__main__":
    main()
