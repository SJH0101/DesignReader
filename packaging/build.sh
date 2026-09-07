#!/bin/bash
# DesignReader — .app 빌드 후 DMG 패키징
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

APP_NAME="DesignReader"
VOL_NAME="DesignReader"
DIST="packaging/dist"
DMG="packaging/$APP_NAME.dmg"
PY="$ROOT/.venv/bin/python"

echo "==> 1/4 아이콘"
"$PY" packaging/gen_icon.py

# WAL 에 남은 커밋을 본체로 밀어넣는다. 이걸 빼먹으면 최근 번역이
# 번들에 실리지 않는다.
echo "==> 1.5/4 빈 교재함 만들기"
"$PY" -c "
import pathlib, sys
sys.path.insert(0, '.')
from app import db
seed = pathlib.Path('packaging/build/seed.db')
seed.parent.mkdir(parents=True, exist_ok=True)
if seed.exists():
    seed.unlink()
db.connect(seed).close()
print('    빈 DB 생성 (교재는 사용자가 직접 넣습니다)')
"

echo "==> 1.7/4 JS 참조 검사"
"$PY" packaging/check_js.py app/static/app.js || { echo "빌드 중단: 사라진 함수가 있습니다"; exit 1; }

echo "==> 1.8/4 CSS 규칙 검사"
"$PY" packaging/check_css.py || { echo "빌드 중단: 서식 없는 class 가 있습니다"; exit 1; }

echo "==> 2/4 앱 빌드"
"$ROOT/.venv/bin/pyinstaller" packaging/reader.spec \
    --noconfirm --distpath "$DIST" --workpath packaging/work --clean \
    2>&1 | grep -Ei "error|warning: hidden|completed" | tail -5 || true

[ -d "$DIST/$APP_NAME.app" ] || { echo "빌드 실패: 앱 번들이 없습니다"; exit 1; }

echo "==> 3/4 서명"
# 내부 실행 파일 이름은 반드시 ASCII 여야 한다. 한글이면 codesign 이 SIGBUS 로 죽는다.
codesign --force --deep -s - "$DIST/$APP_NAME.app"
codesign --verify --strict "$DIST/$APP_NAME.app" \
    && echo "    서명 검증 통과" \
    || { echo "    서명 검증 실패"; exit 1; }

echo "==> 4/4 DMG"
STAGE="packaging/work/dmg"
rm -rf "$STAGE"; mkdir -p "$STAGE"
cp -R "$DIST/$APP_NAME.app" "$STAGE/"
ln -s /Applications "$STAGE/Applications"      # 끌어다 놓기용

# 설치 안내
cat > "$STAGE/읽어보세요.txt" <<'TXT'
DesignReader 설치 방법


1. 왼쪽 DesignReader 아이콘을 오른쪽 Applications 폴더로 끌어다 놓으세요.

2. 처음 열 때 "손상되었기 때문에 열 수 없습니다" 라는 창이 뜹니다.
   앱이 고장난 게 아니라, 애플에 돈을 내고 등록한 앱이 아니라서 막는 것입니다.
   아래대로 한 번만 풀어주면 그 뒤로는 그냥 열립니다.

   시스템 설정 → 개인정보 보호 및 보안 → 맨 아래까지 스크롤
   → "DesignReader"에 대한 "확인 없이 열기" 클릭

   (예전 macOS 에서 쓰던 "우클릭 → 열기" 는 이제 통하지 않습니다.)

   그래도 안 되면 터미널에 아래 한 줄을 붙여넣으세요.
   xattr -dr com.apple.quarantine /Applications/DesignReader.app


3. 애플 실리콘(M1 이후) 맥 전용입니다. 인텔 맥에서는 실행되지 않습니다.


AI 번역·해설을 쓰려면 (이게 없으면 PDF 만 보입니다)

Claude 를 쓰는 경우 — 터미널에서:
   npm install -g @anthropic-ai/claude-code
   claude
   실행되면 /login 을 쳐서 한 번 로그인하세요.

GPT 를 쓰는 경우 — 터미널에서:
   npm install -g @openai/codex
   codex login
   ChatGPT 계정으로 로그인하세요.
   (무료 계정도 됩니다. 다만 한도가 빨리 차서 많이 번역하려면 Plus·Pro 가 낫습니다)

둘 중 하나만 있어도 되고, 둘 다 깔아두고 설정에서 골라 쓸 수도 있습니다.
각자의 계정을 쓰며 별도 API 요금은 들지 않습니다.


교재는 들어 있지 않습니다. 앱을 열고 "문서 추가"로 자기 PDF 를 넣으면 됩니다.

번역해 둔 내용은 아래에 쌓이며, 앱을 지워도 남습니다.
   ~/Library/Application Support/DesignReader/reader.db
TXT

rm -f "$DMG"
hdiutil create -volname "$VOL_NAME" -srcfolder "$STAGE" -ov -format UDZO "$DMG" \
    >/dev/null

# DMG 를 만들었으면 staging 사본은 쓸모없다. 남겨두면 macOS 앱 목록에
# 등록되어 DesignReader 가 여러 개 있는 것처럼 보인다.
LSREG=/System/Library/Frameworks/CoreServices.framework/Frameworks/LaunchServices.framework/Support/lsregister
"$LSREG" -u "$STAGE/$APP_NAME.app" 2>/dev/null || true
rm -rf "$STAGE"
# 빌드해 둔 .app 은 DMG 안에 이미 들어갔으므로 여기 남길 이유가 없다.
# 남겨두면 macOS 가 이걸 설치된 앱으로 잡아서, 앱 목록에 DesignReader 가
# 두 개로 보인다. 등록 해제만으로는 안 된다 — 다시 훑어서 또 잡는다.
# 설치할 때는 DMG 를 열어서 쓴다.
"$LSREG" -u "$ROOT/$DIST/$APP_NAME.app" 2>/dev/null || true
rm -rf "$ROOT/$DIST"

echo
echo "완료"
echo "  DMG : $DMG  ($(du -h "$DMG" | cut -f1))"
