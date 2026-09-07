"""로컬 Claude 연결부.

우선순위:
  1) Claude Code CLI (`claude -p`)  — 구독으로 동작, 추가 비용 없음
  2) ANTHROPIC_API_KEY 로 REST 호출 — 키가 있을 때만
둘 다 없으면 사용자에게 설치 안내를 돌려준다.
"""
from __future__ import annotations

import json
import os
import tempfile
import shutil
import subprocess
import urllib.request
from pathlib import Path

MODEL = "claude-opus-5"

# 화면에 보여줄 선택지 (Claude Code 기본은 Opus 5)
MODELS = [
    ("claude-opus-5",            "Opus 5 — 가장 정확 (기본)"),
    ("claude-sonnet-5",          "Sonnet 5 — 빠르고 저렴"),
    ("claude-haiku-4-5-20251001", "Haiku 4.5 — 가장 빠름"),
]
VALID = {m for m, _ in MODELS}

# GPT 쪽 선택지. Codex CLI 는 ChatGPT 유료 구독으로 로그인해 쓴다.
GPT_MODELS = [
    ("", "기본값 (Codex 가 고른 모델)"),
    ("gpt-5.1-codex", "GPT-5.1 Codex"),
    ("gpt-5.1", "GPT-5.1"),
]
GPT_VALID = {m for m, _ in GPT_MODELS if m}

ENGINES = [
    ("claude", "Claude", "Claude Code CLI · claude 로그인"),
    ("gpt", "GPT", "Codex CLI · ChatGPT 구독 로그인"),
]
DEFAULT_ENGINE = "claude"

_CLI_CANDIDATES = [
    "claude",
    str(Path.home() / ".claude" / "local" / "claude"),
    str(Path.home() / ".local" / "bin" / "claude"),
    "/opt/homebrew/bin/claude",
    "/usr/local/bin/claude",
]
_CODEX_CANDIDATES = [
    "codex",
    str(Path.home() / ".local" / "bin" / "codex"),
    "/opt/homebrew/bin/codex",
    "/usr/local/bin/codex",
]


def _bundled_bin(name: str) -> str | None:
    """앱이 직접 받아둔 Node 안에 깔린 것.

    Node 가 없는 사람에게는 앱이 Node 를 받아 그 안에 codex 를 깐다.
    그러면 /usr/local/bin 같은 흔한 자리에는 없으므로 여기도 봐야 한다.
    """
    d = os.environ.get("READER_DATA")
    if not d:
        return None
    c = os.path.join(d, "node", "bin", name)
    return c if os.path.exists(c) else None


def _first_existing(cands: list[str]) -> str | None:
    for c in cands:
        p = shutil.which(c) if "/" not in c else (c if os.path.exists(c) else None)
        if p:
            return p
    return None


def find_cli() -> str | None:
    return _first_existing(_CLI_CANDIDATES) or _bundled_bin("claude")


def find_codex() -> str | None:
    return _first_existing(_CODEX_CANDIDATES) or _bundled_bin("codex")


def engine_ready(engine: str) -> bool:
    if os.environ.get("READER_NO_AI"):
        return False
    if engine == "gpt":
        return bool(find_codex())
    return bool(find_cli()) or bool(os.environ.get("ANTHROPIC_API_KEY"))


def backend(engine: str | None = None) -> str:
    # 처음 설치한 사람이 보는 화면을 그대로 확인하기 위한 스위치
    if os.environ.get("READER_NO_AI"):
        return "none"
    if engine == "gpt":
        return "codex" if find_codex() else "none"
    if find_cli():
        return "cli"
    if os.environ.get("ANTHROPIC_API_KEY"):
        return "api"
    return "none"


class AIError(RuntimeError):
    pass


# 마지막 호출의 사용량. 서버가 읽어 누적한다.
LAST_USAGE: dict = {}


def _note_usage(d: dict) -> None:
    u = d.get("usage") or {}
    LAST_USAGE.clear()
    LAST_USAGE.update({
        "input": (u.get("input_tokens", 0)
                  + u.get("cache_creation_input_tokens", 0)
                  + u.get("cache_read_input_tokens", 0)),
        "output": u.get("output_tokens", 0),
        "cost": d.get("total_cost_usd", 0) or 0,
    })


def _work_dir() -> str:
    """claude 를 띄울 자리.

    현재 폴더를 물려주면 그 자리를 프로젝트로 보고 주변을 훑는다.
    앱은 '/' 에서 도는데, 그러면 사진·음악·다운로드·네트워크 볼륨까지
    건드려 macOS 가 접근 권한을 계속 묻는다. 빈 전용 폴더를 준다.
    """
    d = os.path.join(
        os.environ.get("READER_DATA", tempfile.gettempdir()), "cli-home")
    try:
        os.makedirs(d, exist_ok=True)
    except OSError:
        return tempfile.gettempdir()
    return d


def _via_cli(prompt: str, system: str | None, timeout: int,
             model: str | None = None, read_files: bool = False) -> str:
    exe = find_cli()
    cmd = [exe, "-p", prompt, "--output-format", "json",
           # 설정·MCP 탐색을 좁혀 엉뚱한 곳을 뒤지지 않게 한다
           "--strict-mcp-config", "--setting-sources", "user"]

    # 호출 고정비의 대부분은 도구 정의다. 번역·해설에는 도구가 필요 없으므로
    # 전부 끈다. 실측: 도구 켠 채 34,569 토큰 -> 끄면 6,906 토큰.
    # 지면 그림을 봐야 할 때만 Read 하나를 허용한다.
    cmd += ["--tools", "Read"] if read_files else ["--tools", ""]

    if model:
        cmd += ["--model", model]
    if system:
        # 덧붙이지 않고 통째로 갈아끼운다. 기본 시스템 프롬프트가
        # 그대로 실리면 매 호출에 1만 토큰 가까이 낭비된다.
        cmd += ["--system-prompt", system]
    env = dict(os.environ)
    # 데스크탑 앱이 심어둔 라우팅 변수를 물려받으면 CLI가 오작동할 수 있어 정리한다
    env.pop("ANTHROPIC_BASE_URL", None)
    env.pop("CLAUDE_CODE_SSE_PORT", None)
    env.pop("CLAUDECODE", None)
    try:
        r = subprocess.run(cmd, capture_output=True, text=True,
                           timeout=timeout, env=env, cwd=_work_dir())
    except subprocess.TimeoutExpired:
        raise AIError("시간 초과")
    out = (r.stdout or "").strip()
    try:
        data = json.loads(out)
    except json.JSONDecodeError:
        data = None

    if isinstance(data, dict):
        _note_usage(data)
        msg = (data.get("result") or "").strip()
        if data.get("is_error"):
            # 로그인 안 됨이 가장 흔하다. 안내를 붙여준다.
            if "not logged in" in msg.lower() or "/login" in msg:
                raise AIError(
                    "Claude CLI에 로그인되어 있지 않습니다.\n"
                    "터미널에서 `claude` 를 실행하고 `/login` 으로 한 번만 "
                    "로그인하면 됩니다.")
            raise AIError(msg or "claude 실행 실패")
        if msg:
            return msg
        raise AIError("빈 응답을 받았습니다")

    if r.returncode != 0:
        raise AIError((r.stderr or out or "claude 실행 실패").strip()[:400])
    return out


def _via_codex(prompt: str, system: str | None, timeout: int,
               model: str | None = None, read_files: bool = False) -> str:
    """Codex CLI 로 묻는다. ChatGPT 유료 구독 로그인으로 동작한다.

    claude 와 달리 시스템 프롬프트를 갈아끼우는 스위치가 없다. Codex 는
    본래 코딩 도우미라 그쪽 지시가 이미 실려 있으므로, 우리 지시를 맨 앞에
    두고 '시키는 일만 하라'고 못박는다.

    파일을 건드리거나 명령을 실행할 이유가 없으므로 read-only 로 묶는다.
    마지막 답만 파일로 받아 적으면 진행 로그를 헤집지 않아도 된다.
    """
    exe = find_codex()
    if not exe:
        raise AIError("codex 를 찾을 수 없습니다")

    body = prompt if not system else (
        f"{system}\n\n"
        f"위 지시만 따르라. 파일을 읽거나 고치지 말고, 명령을 실행하지 말고, "
        f"되묻지 말고, 결과만 그대로 내놓아라.\n\n{prompt}")

    out_file = None
    try:
        fd, out_file = tempfile.mkstemp(suffix=".txt", dir=_work_dir())
        os.close(fd)
        cmd = [exe, "exec", "--skip-git-repo-check", "--ephemeral",
               "-s", "read-only", "--color", "never",
               "-C", _work_dir(), "-o", out_file]
        if model:
            cmd += ["-m", model]
        cmd += ["-"]                      # 프롬프트는 표준입력으로 넘긴다
        env = dict(os.environ)
        env.pop("OPENAI_BASE_URL", None)
        # codex 는 node 로 도는 스크립트다. 앱이 직접 받아둔 Node 가 있으면
        # 그걸 태워야 한다. 안 그러면 설치는 됐는데 실행이 안 된다.
        nb = os.path.join(os.environ.get("READER_DATA", ""), "node", "bin")
        if nb and os.path.isdir(nb):
            env["PATH"] = nb + os.pathsep + env.get("PATH", "")
        try:
            r = subprocess.run(cmd, input=body, capture_output=True, text=True,
                               timeout=timeout, env=env, cwd=_work_dir())
        except subprocess.TimeoutExpired:
            raise AIError("시간 초과")

        try:
            msg = Path(out_file).read_text(encoding="utf-8").strip()
        except OSError:
            msg = ""
        if msg:
            LAST_USAGE.clear()            # Codex 는 사용량을 따로 주지 않는다
            return msg

        err = ((r.stderr or "") + "\n" + (r.stdout or "")).strip()
        low = err.lower()
        # 로그인이 안 됐을 때 codex 는 "not logged in" 이 아니라
        # 401 Unauthorized 로 답한다. 둘 다 잡는다.
        if ("401" in low or "unauthorized" in low
                or "not logged in" in low or "codex login" in low):
            raise AIError(
                "Codex CLI에 로그인되어 있지 않습니다.\n"
                "터미널에서 `codex login` 을 실행하고 ChatGPT 계정으로 "
                "한 번만 로그인하면 됩니다. (유료 플랜이어야 구독 로그인이 됩니다)")
        if "usage limit" in low or "rate limit" in low or "429" in low \
                or "quota" in low:
            raise AIError("ChatGPT 사용 한도에 걸렸습니다. 잠시 뒤 다시 해보세요.")
        # 배너가 길게 붙으므로 뒤쪽(실제 오류)만 보여준다
        tail = "\n".join(l for l in err.splitlines()
                          if l.strip() and not l.startswith("--"))[-400:]
        raise AIError(tail or "codex 실행 실패")
    finally:
        if out_file:
            try:
                os.unlink(out_file)
            except OSError:
                pass


def _via_api(prompt: str, system: str | None, timeout: int,
             model: str | None = None) -> str:
    body = {
        "model": model or MODEL,
        "max_tokens": 4096,
        "messages": [{"role": "user", "content": prompt}],
    }
    if system:
        body["system"] = system
    req = urllib.request.Request(
        "https://api.anthropic.com/v1/messages",
        data=json.dumps(body).encode(),
        headers={
            "content-type": "application/json",
            "x-api-key": os.environ["ANTHROPIC_API_KEY"],
            "anthropic-version": "2023-06-01",
        })
    with urllib.request.urlopen(req, timeout=timeout) as r:
        data = json.loads(r.read())
    return "".join(b.get("text", "") for b in data.get("content", [])).strip()


def ask(prompt: str, system: str | None = None, timeout: int = 180,
        model: str | None = None, read_files: bool = False,
        engine: str | None = None) -> str:
    engine = engine or DEFAULT_ENGINE
    if engine == "gpt":
        if model and model not in GPT_VALID:
            model = None
        if backend("gpt") == "codex":
            return _via_codex(prompt, system, timeout, model, read_files)
        raise AIError(
            "GPT(Codex CLI)를 찾지 못했습니다.\n"
            "터미널에서 아래를 실행해 설치하세요:\n"
            "  npm install -g @openai/codex\n"
            "설치 후 `codex login` 으로 ChatGPT 계정 로그인이 필요합니다. "
            "(유료 플랜이어야 구독 로그인이 됩니다)")

    if model and model not in VALID:
        model = None
    b = backend()
    if b == "cli":
        return _via_cli(prompt, system, timeout, model, read_files)
    if b == "api":
        return _via_api(prompt, system, timeout, model)
    raise AIError(
        "로컬 Claude를 찾지 못했습니다.\n"
        "터미널에서 아래를 실행해 설치하세요:\n"
        "  npm install -g @anthropic-ai/claude-code\n"
        "설치 후 `claude` 로 한 번 로그인하면 연결됩니다.")
