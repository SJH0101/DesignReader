"""Claude Code 초기 세팅 — 상태 점검, 자동 설치, 로그인 띄우기."""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import tempfile
import time
from pathlib import Path

from . import ai

def data_dir() -> Path:
    """앱이 자기 물건을 두는 자리."""
    d = os.environ.get("READER_DATA")
    if d:
        return Path(d)
    return Path.home() / "Library" / "Application Support" / "DesignReader"


def node_bin() -> Path:
    """앱이 직접 받아둔 Node 의 실행 파일 자리."""
    return data_dir() / "node" / "bin"


def ensure_node(progress=None) -> str | None:
    """npm 을 손에 넣는다. 없으면 Node 를 받아서 앱 폴더에 둔다.

    관리자 암호가 필요한 설치 프로그램(.pkg)은 쓰지 않는다. 공식 tar 를
    풀어 홈 폴더에 두면 암호 없이도 되고, 앱을 지우면 같이 사라진다.
    시스템 Node 는 건드리지 않는다.
    """
    npm = _find("npm")
    if npm:
        return npm

    mine = node_bin() / "npm"
    if mine.exists():
        return str(mine)

    def note(m):
        if progress:
            progress(m)

    note("Node.js 를 내려받는 중… (2~3분 걸립니다)")
    import json as _json
    import platform
    import tarfile
    import urllib.request

    arch = "arm64" if platform.machine() == "arm64" else "x64"
    try:
        with urllib.request.urlopen("https://nodejs.org/dist/index.json",
                                    timeout=60) as r:
            ver = next(v["version"] for v in _json.load(r) if v.get("lts"))
    except Exception as e:                              # noqa: BLE001
        return None if progress is None else _fail_note(note, e)

    url = f"https://nodejs.org/dist/{ver}/node-{ver}-darwin-{arch}.tar.gz"
    dest = data_dir() / "node"
    tmp = data_dir() / "node.tar.gz"
    try:
        dest.parent.mkdir(parents=True, exist_ok=True)
        with urllib.request.urlopen(url, timeout=600) as r, tmp.open("wb") as f:
            shutil.copyfileobj(r, f)
        note("Node.js 를 푸는 중…")
        stage = data_dir() / "node.tmp"
        shutil.rmtree(stage, ignore_errors=True)
        with tarfile.open(tmp) as t:
            t.extractall(stage)
        inner = next(stage.iterdir())          # node-vX-darwin-arm64/
        shutil.rmtree(dest, ignore_errors=True)
        inner.rename(dest)
        shutil.rmtree(stage, ignore_errors=True)
    except Exception:                                   # noqa: BLE001
        return None
    finally:
        try:
            tmp.unlink()
        except OSError:
            pass

    got = dest / "bin" / "npm"
    return str(got) if got.exists() else None


def _fail_note(note, e) -> None:
    note(f"Node.js 를 받지 못했습니다: {e}")
    return None


# npm 이 PATH 에 없을 때 흔히 있는 자리
_NPM_DIRS = [
    Path.home() / ".local/bin", Path.home() / ".volta/bin",
    Path.home() / ".nvm/versions/node", Path("/opt/homebrew/bin"),
    Path("/usr/local/bin"), Path("/usr/bin"),
]


def _find(name: str) -> str | None:
    p = shutil.which(name)
    if p:
        return p
    for d in _NPM_DIRS:
        c = d / name
        if c.exists() and os.access(c, os.X_OK):
            return str(c)
    # nvm 처럼 버전 폴더 아래 있는 경우
    nvm = Path.home() / ".nvm/versions/node"
    if nvm.exists():
        for v in sorted(nvm.iterdir(), reverse=True):
            c = v / "bin" / name
            if c.exists():
                return str(c)
    return None


def _env() -> dict:
    env = dict(os.environ)
    for k in ("ANTHROPIC_BASE_URL", "CLAUDE_CODE_SSE_PORT", "CLAUDECODE"):
        env.pop(k, None)
    # 앱이 직접 받아둔 Node 를 먼저 태운다. codex 는 node 로 도는 스크립트라
    # 이걸 빼면 설치는 되고 실행이 안 된다.
    extra = [str(node_bin())] + [str(d) for d in _NPM_DIRS if d.exists()]
    env["PATH"] = os.pathsep.join(
        dict.fromkeys(env.get("PATH", "").split(os.pathsep) + extra))
    return env


def claude_auth() -> dict | None:
    """claude auth status 가 주는 것을 그대로 돌려준다.

    예전에는 `claude -p ok` 를 실제로 한 번 돌려 로그인 여부를 봤다.
    그러면 화면을 열 때마다 진짜 호출이 나가 토큰을 쓰고 10초 넘게 걸렸다.
    auth status 는 0.2초면 되고 계정·요금제까지 알려준다.
    """
    exe = ai.find_cli()
    if not exe:
        return None
    try:
        r = subprocess.run([exe, "auth", "status"], capture_output=True,
                           text=True, timeout=30, env=_env())
        return json.loads(r.stdout or "{}")
    except Exception:                                   # noqa: BLE001
        return None


def logged_in() -> bool | None:
    """True=로그인됨, False=안 됨, None=CLI 자체가 없음."""
    if not ai.find_cli():
        return None
    d = claude_auth()
    if d is None:
        return False
    if "loggedIn" in d:
        return bool(d["loggedIn"])
    # auth status 가 없는 옛 버전이면 직접 한 번 불러 본다
    try:
        r = subprocess.run([ai.find_cli(), "-p", "ok", "--output-format", "json"],
                           capture_output=True, text=True, timeout=60,
                           env=_env())
        j = json.loads(r.stdout or "{}")
        msg = (j.get("result") or "").lower()
        if j.get("is_error") and ("not logged in" in msg or "/login" in msg):
            return False
        return not j.get("is_error", False)
    except Exception:                                   # noqa: BLE001
        return False


def probe_gpt() -> dict:
    """GPT(Codex) 쪽 상태. codex 는 npm 으로만 깔 수 있다."""
    if os.environ.get("READER_NO_AI"):
        return {"cli": None, "logged_in": None, "ready": False,
                "can_auto": bool(_find("npm")), "npm": None}
    cli = ai.find_codex()
    login = None
    if cli:
        try:
            r = subprocess.run([cli, "login", "status"], capture_output=True,
                               text=True, timeout=45, env=_env())
            low = ((r.stdout or "") + (r.stderr or "")).lower()
            login = not ("not logged in" in low or "no codex credentials" in low)
        except Exception:                               # noqa: BLE001
            login = None
    npm = _find("npm") or (str(node_bin() / "npm")
                           if (node_bin() / "npm").exists() else None)
    # Node 가 없어도 앱이 받아올 수 있으므로 늘 자동으로 할 수 있다
    return {"cli": cli, "logged_in": login, "npm": npm,
            "ready": bool(cli) and login is True,
            "can_auto": True}


def install_codex(progress=None) -> tuple[bool, str]:
    npm = ensure_node(progress)
    if not npm:
        return False, ("Node.js 를 받지 못했습니다.\n"
                       "인터넷 연결을 확인하고 다시 눌러 주세요. 그래도 안 되면 "
                       "nodejs.org 에서 LTS 를 직접 설치한 뒤 다시 눌러 주세요.")
    if progress:
        progress("Codex CLI 를 내려받는 중… (1~2분 걸립니다)")
    try:
        r = subprocess.run([npm, "install", "-g", "@openai/codex"],
                           capture_output=True, text=True, timeout=900,
                           env=_env())
    except subprocess.TimeoutExpired:
        return False, "설치가 너무 오래 걸려 중단했습니다."
    if r.returncode != 0:
        tail = (r.stderr or r.stdout or "").strip()[-400:]
        return False, f"설치에 실패했습니다.\n{tail}"
    return True, "설치가 끝났습니다."


_URL_RE = re.compile(r"https://\S+")


# 로그인 절차가 진행 중인 프로세스. 코드를 받아 넣어줘야 하는 경우가 있다.
_PENDING: dict[str, subprocess.Popen] = {}


def _spawn_login(cmd: list[str], key: str = "") -> tuple[subprocess.Popen, Path]:
    """로그인 명령을 뒤에서 돌린다.

    claude 는 브라우저에서 받은 코드를 붙여넣으라고 기다린다. 그래서
    표준입력을 열어두고, 나중에 앱에서 받은 코드를 그리로 넣어준다.
    닫아버리면 영영 끝나지 않는다.
    """
    out = Path(tempfile.gettempdir()) / f"dr-login-{os.getpid()}-{key or 'x'}.log"
    f = out.open("w")
    proc = subprocess.Popen(cmd, stdout=f, stderr=subprocess.STDOUT,
                            stdin=subprocess.PIPE, env=_env(),
                            cwd=tempfile.gettempdir(), start_new_session=True)
    if key:
        old = _PENDING.pop(key, None)
        if old and old.poll() is None:
            try:
                old.kill()
            except Exception:                           # noqa: BLE001
                pass
        _PENDING[key] = proc
    return proc, out


def send_login_code(engine: str, code: str) -> tuple[bool, str]:
    """브라우저에서 받은 코드를 로그인 절차에 넣어준다."""
    proc = _PENDING.get(engine)
    if proc is None or proc.poll() is not None:
        return False, "로그인 절차가 끝났거나 시작되지 않았습니다. 다시 눌러 주세요."
    code = (code or "").strip()
    if not code:
        return False, "코드를 붙여넣어 주세요."
    try:
        proc.stdin.write(code + "\n")
        proc.stdin.flush()
    except Exception as e:                              # noqa: BLE001
        return False, f"코드를 전달하지 못했습니다: {e}"
    return True, "코드를 넣었습니다. 확인 중…"


def _login_url(out: Path, wait: float = 12.0) -> str | None:
    """로그인 주소가 찍히기를 기다렸다 뽑아낸다."""
    end = time.time() + wait
    while time.time() < end:
        try:
            txt = out.read_text(encoding="utf-8", errors="replace")
        except OSError:
            txt = ""
        m = _URL_RE.search(txt)
        if m:
            return m.group(0).rstrip(".,")
        time.sleep(0.4)
    return None


def open_login_gpt() -> tuple[bool, str]:
    """ChatGPT 로그인. 터미널 창을 띄우지 않는다.

    codex login 은 스스로 로컬 서버를 띄우고 브라우저를 연다. 그러니
    터미널을 대신 조작할 이유가 없다 — 뒤에서 돌리고 끝나기를 기다리면 된다.
    브라우저가 저절로 안 열리는 경우를 대비해 주소를 뽑아 직접 열어준다.
    """
    exe = ai.find_codex()
    if not exe:
        return False, "먼저 Codex CLI 를 설치해야 합니다.", None
    try:
        _proc, out = _spawn_login([exe, "login"], key="gpt")
    except Exception as e:                              # noqa: BLE001
        return False, f"로그인을 시작하지 못했습니다: {e}", None

    url = _login_url(out)
    if url:
        # codex 가 이미 열었더라도 한 번 더 여는 것은 해가 없다
        subprocess.run(["open", url], capture_output=True, timeout=15)
        return True, "브라우저에서 ChatGPT 계정으로 로그인해 주세요.", url
    return True, "브라우저가 열립니다. ChatGPT 계정으로 로그인해 주세요.", None


def logged_in_gpt() -> bool | None:
    return probe_gpt()["logged_in"]


def probe() -> dict:
    # 처음 쓰는 사람이 보는 화면을 그대로 확인하기 위한 스위치.
    # 이게 켜졌는데 여기만 '설치됨'이라고 하면 화면끼리 말이 어긋난다.
    if os.environ.get("READER_NO_AI"):
        return {"node": None, "npm": None, "cli": None, "logged_in": None,
                "ready": False, "can_auto": True}
    node, npm = _find("node"), _find("npm")
    cli = ai.find_cli()
    login = logged_in() if cli else None
    return {
        "node": node, "npm": npm, "cli": cli,
        "logged_in": login,
        "ready": bool(cli) and login is True,
        # 공식 설치 스크립트는 Node 없이도 되므로 늘 자동 설치를 권할 수 있다
        "can_auto": True,
    }


# Anthropic 이 내주는 공식 설치 스크립트. Node 도 관리자 암호도 필요 없고
# 홈 폴더(~/.local/bin) 안에만 넣는다. npm 방식보다 걸리는 게 훨씬 적다.
NATIVE_URL = "https://claude.ai/install.sh"


def install_cli(progress=None) -> tuple[bool, str]:
    """Claude Code CLI 를 설치한다.

    공식 설치 스크립트를 먼저 쓴다. Node 가 없어도 되기 때문이다.
    그게 안 되면 npm 으로 물러선다.
    """
    def note(m):
        if progress:
            progress(m)

    note("Claude Code 를 내려받는 중… (1~2분 걸립니다)")
    try:
        got = subprocess.run(["curl", "-fsSL", "--max-time", "120", NATIVE_URL],
                             capture_output=True, text=True, timeout=180)
        if got.returncode == 0 and got.stdout.strip().startswith("#!"):
            r = subprocess.run(["bash", "-s", "stable"], input=got.stdout,
                               capture_output=True, text=True, timeout=900,
                               env=_env())
            if r.returncode == 0 and ai.find_cli():
                return True, "설치가 끝났습니다."
            native_err = (r.stderr or r.stdout or "").strip()[-300:]
        else:
            native_err = "설치 스크립트를 받지 못했습니다."
    except Exception as e:                              # noqa: BLE001
        native_err = str(e)[:200]

    npm = _find("npm")
    if not npm:
        return False, ("설치하지 못했습니다.\n" + native_err +
                       "\n\n인터넷 연결을 확인하고 다시 눌러보세요. "
                       "그래도 안 되면 ‘직접 설치’ 쪽 안내를 따라 주세요.")
    note("다른 방법으로 다시 시도하는 중…")
    try:
        r = subprocess.run([npm, "install", "-g", "@anthropic-ai/claude-code"],
                           capture_output=True, text=True, timeout=900,
                           env=_env())
    except subprocess.TimeoutExpired:
        return False, "설치가 너무 오래 걸려 중단했습니다."
    if r.returncode != 0:
        tail = (r.stderr or r.stdout or "").strip()[-400:]
        return False, f"설치에 실패했습니다.\n{tail}"
    return True, "설치가 끝났습니다."


def open_login() -> tuple[bool, str]:
    """Claude 로그인. 터미널 창을 띄우지 않는다."""
    exe = ai.find_cli()
    if not exe:
        return False, "먼저 Claude Code CLI 를 설치해야 합니다.", None
    # setup-token 은 자동화용 토큰을 만드는 명령이지 로그인이 아니다.
    # 로그인은 auth login 이다.
    try:
        _proc, out = _spawn_login([exe, "auth", "login", "--claudeai"],
                                  key="claude")
    except Exception as e:                              # noqa: BLE001
        return False, f"로그인을 시작하지 못했습니다: {e}", None

    url = _login_url(out)
    if url:
        subprocess.run(["open", url], capture_output=True, timeout=15)
        return True, "브라우저에서 Claude 계정으로 로그인해 주세요.", url
    return True, "브라우저가 열립니다. Claude 계정으로 로그인해 주세요.", None


MANUAL_STEPS = [
    {
        "title": "1. Claude Code 설치",
        "why": "터미널을 열고 아래를 붙여넣어 실행하세요. "
               "홈 폴더에만 깔리고 관리자 암호는 필요 없습니다.",
        "cmd": "curl -fsSL https://claude.ai/install.sh | bash",
        "link": "",
    },
    {
        "title": "2. 로그인",
        "why": "실행하면 브라우저가 열립니다. Claude 계정으로 로그인하면 끝입니다. "
               "로그인 후 이 창의 ‘다시 확인’을 누르세요.",
        "cmd": "claude auth login",
        "link": "",
    },
]


GPT_STEPS = [
    {
        "title": "1. Node.js 설치",
        "why": "Codex CLI 는 npm 으로만 깔 수 있습니다. 이미 있다면 건너뛰세요.",
        "cmd": "",
        "link": "https://nodejs.org",
    },
    {
        "title": "2. Codex 설치",
        "why": "터미널을 열고 아래를 붙여넣어 실행하세요.",
        "cmd": "npm install -g @openai/codex",
        "link": "",
    },
    {
        "title": "3. 로그인",
        "why": "브라우저가 열리면 ChatGPT 계정으로 로그인하세요. "
               "무료 계정도 되지만 한도가 빨리 찹니다. "
               "많이 번역하려면 Plus·Pro 가 낫습니다.",
        "cmd": "codex login",
        "link": "",
    },
]


# ---------------- 연결된 계정 ----------------
def _jwt_claims(tok: str) -> dict:
    """서명은 확인하지 않는다. 화면에 보여줄 값만 꺼내 쓴다."""
    import base64
    import json as _json
    try:
        body = tok.split(".")[1]
        body += "=" * (-len(body) % 4)
        return _json.loads(base64.urlsafe_b64decode(body))
    except Exception:                                   # noqa: BLE001
        return {}


def account_info(engine: str) -> dict:
    """어느 계정으로 붙어 있는지, 구독인지 종량과금인지.

    남의 컴퓨터에 깔아줄 때 이걸 못 보면 곤란하다. API 키로 붙어 있으면
    쓴 만큼 돈이 나가는데, 화면에는 그냥 '연결됨' 으로만 보이기 때문이다.
    토큰 자체는 절대 내보내지 않는다.
    """
    out = {"engine": engine, "account": None, "plan": None,
           "mode": None, "billed": None}

    if engine == "gpt":
        if os.environ.get("OPENAI_API_KEY"):
            out.update(mode="apikey", billed=True)
            return out
        f = Path.home() / ".codex" / "auth.json"
        if not f.exists():
            return out
        try:
            d = json.loads(f.read_text(encoding="utf-8"))
        except Exception:                               # noqa: BLE001
            return out
        mode = d.get("auth_mode")
        if d.get("OPENAI_API_KEY") or mode == "apikey":
            out.update(mode="apikey", billed=True)
            return out
        c = _jwt_claims((d.get("tokens") or {}).get("id_token") or "")
        auth = c.get("https://api.openai.com/auth", {}) or {}
        out.update(mode="chatgpt", billed=False,
                   account=c.get("email") or c.get("preferred_username"),
                   plan=auth.get("chatgpt_plan_type"))
        return out

    # Claude
    if os.environ.get("ANTHROPIC_API_KEY") or os.environ.get("ANTHROPIC_AUTH_TOKEN"):
        out.update(mode="apikey", billed=True)
        return out
    d = claude_auth() or {}
    if d.get("loggedIn"):
        api = d.get("authMethod") in ("apiKey", "api_key") or \
            d.get("apiProvider") in ("bedrock", "vertex")
        out.update(mode="apikey" if api else "subscription", billed=bool(api),
                   account=d.get("email"), plan=d.get("subscriptionType"))
    elif ai.find_cli():
        out.update(mode="subscription", billed=False)
    return out
