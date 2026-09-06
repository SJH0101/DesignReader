#!/usr/bin/env python3
"""화면에서 쓰는 class 중 CSS 규칙이 없는 것을 찾는다.

이 프로그램은 규칙이 통째로 사라지는 일이 여러 번 있었다. 그럴 때마다
화면이 서식 없이 흘러버리는데, 눈으로 보기 전에는 알 수가 없다.
빌드 전에 기계가 먼저 잡는다.

완벽할 수는 없다. 만들어 쓰는 이름과 남의 규칙에 얹히는 이름이 섞여 있어서,
'없다'고 나온 것 중 일부는 일부러 그런 것이다. 그런 것은 IGNORE 에 적어둔다.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

# 서식이 필요 없는 이름 — 자바스크립트가 상태만 표시하려고 붙이는 것들
# 상태만 나타내는 이름. 그 자체로는 그림이 없고, 늘 다른 규칙에 얹혀 쓰인다.
# 서식을 주는 이름(big·wide·mini 같은 것)은 여기 넣지 않는다 —
# 빠지면 화면이 깨지는데 검사가 눈감아 주면 잡을 방법이 없다.
IGNORE = {
    "hidden", "on", "open", "closed", "ok", "bad", "dragging",
    "running", "done", "error", "sel", "hover", "saved", "draft",
}


def used_classes(paths: list[Path]) -> dict[str, set[str]]:
    """어디서 쓰였는지까지 같이 모은다."""
    out: dict[str, set[str]] = {}

    def add(name: str, where: str) -> None:
        for n in name.split():
            n = n.strip()
            # "hl-" + ch 처럼 이어붙여 만드는 이름은 판정할 수 없다
            if n.endswith("-"):
                continue
            if n and not n.startswith("$") and re.fullmatch(r"[a-zA-Z][\w-]*", n):
                out.setdefault(n, set()).add(where)

    for p in paths:
        src = p.read_text(encoding="utf-8")
        w = p.name
        # class="a b c"  /  class='a b'
        for m in re.finditer(r'class=["\']([^"\'${}]+)["\']', src):
            add(m.group(1), w)
        # el("div", "a b")
        for m in re.finditer(r'\bel\(\s*["\'][^"\']+["\']\s*,\s*["\']([^"\']+)["\']', src):
            add(m.group(1), w)
        # classList.add("a","b") / .remove("a") 는 인자 전부가 class 다.
        for m in re.finditer(r'classList\.(?:add|remove)\(([^)]*)\)', src):
            for lit in re.findall(r'["\']([^"\']+)["\']', m.group(1)):
                add(lit, w)
        # toggle 은 첫 인자만 class 다. 둘째는 참/거짓 식이라
        # 그 안의 문자열을 집으면 엉뚱한 이름이 섞인다.
        for m in re.finditer(r'classList\.toggle\(\s*["\']([^"\']+)["\']', src):
            add(m.group(1), w)
        # className = "a b"
        for m in re.finditer(r'className\s*=\s*["\']([^"\'${}]+)["\']', src):
            add(m.group(1), w)
    return out


def defined_classes(css: Path) -> set[str]:
    src = css.read_text(encoding="utf-8")
    src = re.sub(r"/\*.*?\*/", " ", src, flags=re.S)     # 주석 제거
    names: set[str] = set()
    # 선언 블록 밖(선택자 자리)의 .이름 만 센다
    for block in re.split(r"\{[^{}]*\}", src):
        for m in re.finditer(r"\.([a-zA-Z][\w-]*)", block):
            names.add(m.group(1))
    # [class~="x"] 형태도 인정한다
    for m in re.finditer(r'\[class~?=["\']([\w-]+)["\']\]', src):
        names.add(m.group(1))
    return names


def main() -> int:
    root = Path(__file__).resolve().parent.parent
    static = root / "app" / "static"
    css = static / "style.css"
    srcs = [static / "index.html", static / "app.js"]
    for f in [css, *srcs]:
        if not f.exists():
            print(f"파일이 없습니다: {f}")
            return 1

    used = used_classes(srcs)
    have = defined_classes(css)
    missing = {k: v for k, v in used.items()
               if k not in have and k not in IGNORE}

    if not missing:
        print(f"CSS 검사 통과 (쓰는 class {len(used)}개 모두 규칙 있음)")
        return 0

    print(f"CSS 규칙이 없는 class {len(missing)}개:")
    for name in sorted(missing):
        print(f"  .{name:<22} ({', '.join(sorted(missing[name]))})")
    print("\n서식이 필요 없는 이름이면 packaging/check_css.py 의 IGNORE 에 넣으세요.")
    return 1


if __name__ == "__main__":
    sys.exit(main())
