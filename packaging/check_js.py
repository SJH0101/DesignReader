"""app.js 에서 정의 없이 호출되는 함수를 찾는다.

node --check 는 문법만 본다. 코드 구간을 갈아끼우다 함수가 통째로
사라지는 사고(jumpTo, pageSeen 등)를 빌드 전에 잡기 위한 검사다.
"""
import re, sys, pathlib
src = pathlib.Path(sys.argv[1]).read_text(encoding="utf-8")
defined = set(re.findall(r'\bfunction\s+([A-Za-z_$][\w$]*)\s*\(', src))
defined |= set(re.findall(r'\b(?:const|let|var)\s+([A-Za-z_$][\w$]*)\s*=', src))
# 매개변수 이름 (function f(a, b) / (a, b) => / a =>)
for params in re.findall(r'\bfunction\s*[\w$]*\s*\(([^)]*)\)', src) + \
              re.findall(r'\(([^()]*)\)\s*=>', src):
    for name in re.findall(r'[A-Za-z_$][\w$]*', params):
        defined.add(name)
defined |= set(re.findall(r'\b([A-Za-z_$][\w$]*)\s*=>', src))
defined |= {"fetch","setTimeout","clearTimeout","setInterval","clearInterval","parseFloat",
            "parseInt","encodeURIComponent","prompt","alert","Math","JSON",
            "Promise","Image","Set","Map","Error","MouseEvent","Event",
            "RegExp","String","Number","Object","Array","Date","document",
            "window","localStorage","navigator","getComputedStyle","console",
            "IntersectionObserver","NodeFilter","requestAnimationFrame",
            "FormData","Boolean","isNaN","Symbol","Number"}
# 함수 호출처럼 보이는 식별자 (앞에 . 이 없는 것만 — 메서드 호출 제외)
called = set(re.findall(r'(?<![\w$.])([A-Za-z_$][\w$]*)\s*\(', src))
missing = sorted(c for c in called - defined
                 if not c[0].isupper() and c not in {"if","for","while","switch",
                    "catch","return","function","async","await","typeof","new",
                    "else","do","try","of","in","delete","void","var","let","const"})
if missing:
    print("정의 없이 호출되는 함수:", ", ".join(missing)); sys.exit(1)
print("함수 참조 검사 통과")
