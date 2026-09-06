"""번역·해설 프롬프트와 분야별 용어집.

용어집이 이 프로그램의 품질을 좌우한다. 일반 번역기가 반드시 틀리는
학술 용어를 고정해 두되, 분야를 가려서 쓴다.

한 가지가 중요하다. field·agency·artifact 처럼 분야가 바뀌면 뜻이
통째로 달라지는 단어가 있다. 이런 것을 "반드시 이렇게 옮겨라"로 묶으면
의학 논문의 imaging artifact 가 '인공물'이 된다. 그래서 용어를 두 갈래로
나눈다.

  fixed   — 그 분야에서 뜻이 하나뿐인 용어. 강제한다.
  context — 뜻이 갈리는 용어. 후보만 알려주고 판단은 맡긴다.
"""
import re


# 분야를 가리지 않는 학술 문장 규칙
_BASE_RULES = """\
원칙:
1. 의역하지 말고 논지의 구조를 보존하라. 저자의 논증 순서, 조건절, 유보
   표현(may, tends to, arguably)을 임의로 단정형으로 바꾸지 마라.
2. 학술 문어체 '~이다/~한다'로 쓴다. 존댓말과 구어체를 쓰지 마라.
3. 핵심 이론 용어는 처음 나올 때 '한국어역(English)' 형태로 병기하고,
   이후에는 한국어역만 쓴다.
4. 인용부호 안의 인용문은 인용임이 드러나게 그대로 옮긴다.
5. 고유명사(인명·지명·기관명)는 원어를 괄호 병기한다. 예: 레이너 밴험(Reyner Banham)
6. 원문이 한 문단이면 번역도 한 문단이다. 임의로 쪼개거나 합치지 마라.
7. 번역문만 출력하라. 설명, 머리말, 따옴표 감싸기를 하지 마라."""


# ---------------- 분야별 용어집 ----------------

_DESIGN_FIXED = {
    "technological mediation": "기술적 매개",
    "postphenomenology": "포스트현상학",
    "posthuman": "포스트휴먼",
    "affordance": "행위유도성(어포던스)",
    "actor-network theory": "행위자-연결망 이론",
    "materiality": "물질성",
    "embodiment": "체현",
    "intentionality": "지향성",
    "mediation paradigm": "매개 패러다임",
    "channels of mediation": "매개 경로",
    "domesticity": "가정성",
    "subculture": "하위문화",
    "bricolage": "브리콜라주",
    "habitus": "아비투스",
    "cultural capital": "문화자본",
    "symbolic capital": "상징자본",
    "hegemony": "헤게모니",
    "signification": "의미작용",
    "signifier": "기표",
    "signified": "기의",
    "commodification": "상품화",
    "technology-in-use": "사용 중인 기술",
    "creole technology": "크레올 기술",
    "obsolescence": "노후화/진부화",
    "modernism": "모더니즘",
    "modernity": "근대성",
    "the Modern Movement": "근대운동",
    "functionalism": "기능주의",
    "Arts and Crafts": "미술공예운동",
    "Bauhaus": "바우하우스",
    "vernacular": "토착적",
    "design thinking": "디자인 씽킹",
    "designerly ways of knowing": "디자이너다운 앎의 방식",
    "wicked problem": "고약한 문제(wicked problem)",
    "reflective practice": "성찰적 실천",
    "co-design": "코디자인",
    "participatory design": "참여 디자인",
    "pluriversal": "복수세계적(플루리버설)",
    "pluriverse": "복수세계(플루리버스)",
    "decolonial": "탈식민적",
    "coloniality": "식민성",
    "ontological design": "존재론적 디자인",
    "platform urbanism": "플랫폼 도시주의",
}
_DESIGN_CONTEXT = {
    "mediation": "매개",
    "script": "스크립트(사물에 새겨진 사용 각본)",
    "delegation": "위임",
    "agency": "행위성",
    "artifact": "인공물",
    "artefact": "인공물",
    "thing": "사물",
    "object": "사물/대상",
    "field": "장(場)",
    "distinction": "구별짓기",
    "representation": "재현",
    "discourse": "담론",
    "taste": "취향",
    "craft": "공예",
    "ornament": "장식",
    "commodity": "상품",
}

_HCI_FIXED = {
    "affordance": "행위유도성(어포던스)",
    "signifier": "기표(시그니파이어)",
    "usability": "사용성",
    "user experience": "사용자 경험",
    "mental model": "심성 모형",
    "think-aloud": "발화사고법",
    "within-subjects": "피험자 내 설계",
    "between-subjects": "피험자 간 설계",
    "wizard of oz": "오즈의 마법사 기법",
    "cognitive walkthrough": "인지적 워크스루",
    "heuristic evaluation": "휴리스틱 평가",
    "grounded theory": "근거이론",
    "thematic analysis": "주제 분석",
    "ecological validity": "생태적 타당도",
    "technology probe": "기술 프로브",
    "participatory design": "참여 디자인",
    "value sensitive design": "가치 민감 디자인",
    "situated action": "상황적 행위",
    "distributed cognition": "분산 인지",
    "sensemaking": "의미형성(센스메이킹)",
    "breakdown": "단절(브레이크다운)",
    "appropriation": "전유",
    "end-user": "최종 사용자",
    "crowdsourcing": "크라우드소싱",
    "informed consent": "사전 동의",
}
_HCI_CONTEXT = {
    "task": "과제",
    "trial": "시행",
    "condition": "조건",
    "probe": "프로브",
    "artifact": "산출물",
    "interface": "인터페이스",
    "agent": "에이전트",
    "field": "현장",
    "study": "연구",
    "prototype": "프로토타입",
}

_PHILSCI_FIXED = {
    "underdetermination": "미결정성",
    "incommensurability": "공약불가능성",
    "paradigm shift": "패러다임 전환",
    "normal science": "정상과학",
    "falsifiability": "반증가능성",
    "demarcation": "구획",
    "research programme": "연구 프로그램",
    "inference to the best explanation": "최선의 설명으로의 추론",
    "scientific realism": "과학적 실재론",
    "anti-realism": "반실재론",
    "instrumentalism": "도구주의",
    "empirical adequacy": "경험적 적합성",
    "theory-ladenness": "이론 적재성",
    "natural kind": "자연종",
    "supervenience": "수반",
    "ceteris paribus": "다른 조건이 같다면",
    "explanandum": "피설명항",
    "explanans": "설명항",
    "epistemic": "인식적",
    "ontological": "존재론적",
    "social construction": "사회적 구성",
    "boundary object": "경계 대상",
    "tacit knowledge": "암묵지",
    "situated knowledge": "상황적 지식",
    "standpoint theory": "입장 이론",
}
_PHILSCI_CONTEXT = {
    "law": "법칙",
    "account": "설명/견해",
    "warrant": "정당화 근거",
    "evidence": "증거",
    "entity": "존재자",
    "kind": "종(種)",
    "object": "대상",
    "agency": "행위성",
    "field": "분야",
    "observation": "관찰",
}

_HUM_FIXED = {
    "hermeneutics": "해석학",
    "phenomenology": "현상학",
    "dialectic": "변증법",
    "genealogy": "계보학",
    "historiography": "역사서술",
    "periodization": "시대구분",
    "primary source": "1차 사료",
    "secondary source": "2차 사료",
    "close reading": "정독",
    "intertextuality": "상호텍스트성",
    "canon": "정전(正典)",
    "aesthetics": "미학",
    "sublime": "숭고",
    "mimesis": "미메시스",
    "alterity": "타자성",
    "subjectivity": "주체성",
    "positionality": "위치성",
    "hegemony": "헤게모니",
    "habitus": "아비투스",
    "cultural capital": "문화자본",
    "orientalism": "오리엔탈리즘",
    "postcolonial": "탈식민",
}
_HUM_CONTEXT = {
    "discourse": "담론",
    "representation": "재현",
    "subject": "주체",
    "text": "텍스트",
    "reading": "독해",
    "figure": "형상/인물",
    "sense": "의미/감각",
    "field": "분야",
    "work": "작품",
}


FIELDS: dict[str, dict] = {
    "general": {
        "label": "일반 학술",
        "hint": "분야를 특정하지 않는 논문·보고서·문서",
        "persona": "당신은 영어 학술 문헌을 한국어로 옮기는 전문 번역자다.",
        "tutor": "당신은 영어 학술 문헌 강독을 돕는 조교다.",
        "fixed": {},
        "context": {},
    },
    "design": {
        "label": "디자인·기술철학",
        "hint": "디자인사, 기술철학, 문화연구, STS",
        "persona": ("당신은 디자인사·기술철학·문화연구 분야의 전문 학술 "
                    "번역자다."),
        "tutor": "당신은 디자인사·기술철학 강독 수업의 조교다.",
        "fixed": _DESIGN_FIXED,
        "context": _DESIGN_CONTEXT,
    },
    "hci": {
        "label": "HCI·인터랙션",
        "hint": "인간-컴퓨터 상호작용, 사용자 연구, 인터랙션 디자인",
        "persona": ("당신은 HCI(인간-컴퓨터 상호작용) 분야의 전문 학술 "
                    "번역자다."),
        "tutor": "당신은 HCI 논문 강독을 돕는 조교다.",
        "fixed": _HCI_FIXED,
        "context": _HCI_CONTEXT,
    },
    "philsci": {
        "label": "과학철학·STS",
        "hint": "과학철학, 과학기술학, 인식론",
        "persona": "당신은 과학철학·과학기술학 분야의 전문 학술 번역자다.",
        "tutor": "당신은 과학철학 강독 수업의 조교다.",
        "fixed": _PHILSCI_FIXED,
        "context": _PHILSCI_CONTEXT,
    },
    "humanities": {
        "label": "인문학",
        "hint": "문학, 역사, 철학, 미학, 문화이론",
        "persona": "당신은 인문학 분야의 전문 학술 번역자다.",
        "tutor": "당신은 인문학 원서 강독 수업의 조교다.",
        "fixed": _HUM_FIXED,
        "context": _HUM_CONTEXT,
    },
}
DEFAULT_FIELD = "general"


def field_of(name: str | None) -> dict:
    return FIELDS.get(name or "", FIELDS[DEFAULT_FIELD])


def glossary_block(text: str, field: str | None = None) -> str:
    """문단에 실제로 등장하는 용어만 골라 넣는다(토큰 절약).

    강제할 것과 판단을 맡길 것을 나눠서 준다. 뜻이 갈리는 단어까지
    강제하면 다른 분야 글에서 오역이 된다.
    """
    prof = field_of(field)
    low = (text or "").lower()

    def hit(d):
        got = [(en, ko) for en, ko in d.items() if en.lower() in low]
        got.sort(key=lambda x: -len(x[0]))
        return got

    fixed, ctx = hit(prof["fixed"])[:16], hit(prof["context"])[:10]
    out = ""
    if fixed:
        lines = "\n".join(f"- {en} → {ko}" for en, ko in fixed)
        out += f"\n\n[용어 고정 — 반드시 이 번역어를 쓸 것]\n{lines}"
    if ctx:
        lines = "\n".join(f"- {en} → {ko}" for en, ko in ctx)
        out += ("\n\n[문맥 참고 — 이 분야의 전문적 의미로 쓰였을 때만 "
                "아래 번역어를 쓰고, 일상적·일반적 의미로 쓰였으면 "
                "문맥에 맞게 옮겨라]\n" + lines)
    return out


def translate_system(field: str | None = None) -> str:
    p = field_of(field)
    return f"{p['persona']}\n\n{_BASE_RULES}"


def explain_system(field: str | None = None) -> str:
    p = field_of(field)
    return p["tutor"] + """ 학생이 원서를 읽다가
모르는 부분을 눌렀을 때 짧고 정확하게 풀어준다.

원칙:
- 한국어로 답한다. 400자 이내로 압축한다.
- 반드시 '이 문단 안에서의 쓰임'을 기준으로 설명한다. 일반 사전 뜻만
  나열하지 마라.
- 이론 용어라면 누가 쓴 개념인지(학자·계보)를 한 줄로 짚어준다.
- 마크다운 제목(#)을 쓰지 마라. 굵은 글씨와 짧은 목록만 허용한다.
- 확실하지 않으면 추측하지 말고 모른다고 말하라."""


def chat_system(field: str | None = None) -> str:
    p = field_of(field)
    return p["tutor"] + """ 학생이 원서를 읽다가 묻는 것에 답한다.

원칙:
- 한국어로 답한다. 묻지 않은 것까지 늘어놓지 마라.
- 항상 주어진 문단의 맥락 안에서 답한다. 일반론으로 흐르지 마라.
- 이론 용어는 누구의 개념인지(학자·계보) 한 줄로 짚어준다.
- 마크다운 제목(#)을 쓰지 마라. 굵은 글씨와 짧은 목록만 쓴다.
- 확실하지 않으면 추측하지 말고 모른다고 말하라."""


# 예전 이름 — 분야를 안 넘기는 자리를 위해 남겨둔다
TRANSLATE_SYSTEM = translate_system()
EXPLAIN_SYSTEM = explain_system()
CHAT_SYSTEM = chat_system()


def translate_prompt(text: str, title: str = "", author: str = "",
                     field: str | None = None) -> str:
    ctx = ""
    if title:
        ctx = f"[출처] {author + ', ' if author else ''}{title}\n\n"
    return (f"{ctx}다음 영어 학술 문단을 한국어로 번역하라."
            f"{glossary_block(text, field)}\n\n[원문]\n{text}")


def explain_prompt(term: str, para_en: str, title: str = "", author: str = "",
                   mode: str = "term") -> str:
    src = f"[출처] {author + ', ' if author else ''}{title}\n" if title else ""
    if mode == "para":
        return (f"{src}[문단]\n{para_en}\n\n"
                f"이 문단이 하는 말을 논지 중심으로 풀어서 설명하라. "
                f"저자가 무엇에 반대하거나 무엇을 주장하려는 맥락인지 짚어라.")
    if mode == "grammar":
        return (f"{src}[문단]\n{para_en}\n\n[학생이 고른 부분]\n{term}\n\n"
                f"이 부분의 문장 구조를 분해해 왜 이렇게 해석되는지 설명하라. "
                f"주절/종속절, 수식 관계, 까다로운 어순을 짚어라.")
    return (f"{src}[문단]\n{para_en}\n\n[학생이 누른 표현]\n{term}\n\n"
            f"이 표현이 이 문단에서 무슨 뜻인지 설명하라.")


def chat_prompt(history: list, para_en: str, term: str = "",
                title: str = "", author: str = "") -> str:
    """대화 이력을 하나의 프롬프트로 합친다."""
    src = f"[출처] {author + ', ' if author else ''}{title}\n" if title else ""
    ctx = f"{src}[읽고 있는 문단]\n{para_en}\n" if para_en else src
    if term:
        ctx += f"\n[학생이 지목한 부분]\n{term}\n"
    turns = []
    for m in history:
        who = "학생" if m.get("role") == "user" else "조교"
        turns.append(f"{who}: {m.get('content','').strip()}")
    return ctx + "\n---\n\n" + "\n\n".join(turns) + "\n\n조교:"


# ---------------- 분야 판별 ----------------
DETECT_SYSTEM = """\
당신은 학술 문헌의 분야를 가려내는 분류기다. 주어진 목록의 코드 하나만
출력한다. 설명·문장부호·따옴표를 붙이지 마라."""


def detect_prompt(sample: str) -> str:
    opts = "\n".join(f"- {k}: {v['label']} — {v['hint']}"
                      for k, v in FIELDS.items())
    return (f"다음은 어떤 학술 문헌의 첫 부분이다. 어느 분야인가?\n\n"
            f"[고를 수 있는 코드]\n{opts}\n\n"
            f"확신이 서지 않으면 general 을 골라라.\n\n"
            f"[문헌]\n{sample[:2500]}\n\n코드만 출력:")


def parse_field(out: str) -> str:
    t = (out or "").strip().lower()
    for k in FIELDS:
        if k in t:
            return k
    return DEFAULT_FIELD


# ---------------- 문장 단위 짝맞춤 ----------------
_SENT = "\x00"          # 문장 끝이 아닌 마침표를 잠시 숨겨둘 표시

# 뒤에 무엇이 오든 문장 끝이 아닌 약어
_ABBR = (r"e\.g|i\.e|etc|cf|vs|viz|ibid|op\.\s*cit|et\s+al|al|approx|esp|"
         r"repr|trans|rev|eds?|figs?|vols?|Dr|Prof|Mr|Mrs|Ms|St|Jr|Sr|Inc|Ltd|Co")
_RE_ABBR = re.compile(rf"\b({_ABBR})\.", re.I)
# 숫자가 뒤따를 때만 약어인 것 (p. 14, n. 3, c. 1900, no. 5)
_RE_NUMREF = re.compile(r"\b(pp?|nos?|n|c|ca|ch|vol)\.(?=\s*\d)", re.I)
# 이름 머리글자 (J. Doe)
_RE_INITIAL = re.compile(r"\b([A-Z])\.(?=\s+[A-Z])")

_SPLIT = re.compile(r'(?<=[.!?])["\'’”\)\]]*\s+')


def split_sentences(text: str) -> list[str]:
    """영어 문단을 문장으로 나눈다. 약어·쪽 표기·머리글자에서는 끊지 않는다."""
    text = (text or "").strip()
    if not text:
        return []
    masked = _RE_ABBR.sub(lambda m: m.group(0)[:-1] + _SENT, text)
    masked = _RE_NUMREF.sub(lambda m: m.group(0)[:-1] + _SENT, masked)
    masked = _RE_INITIAL.sub(lambda m: m.group(1) + _SENT, masked)

    out = [p.replace(_SENT, ".").strip()
           for p in _SPLIT.split(masked) if p.strip()]
    # 너무 짧은 조각은 앞 문장에 붙인다
    merged: list[str] = []
    for s in out:
        if merged and len(s) < 12 and not s[:1].isupper():
            merged[-1] += " " + s
        else:
            merged.append(s)
    return merged


_ALIGN_EXTRA = """

추가 규칙 — 문장 짝맞춤:
입력은 [1] [2] … 로 번호가 매겨진 영어 문장들이다. 같은 번호를 붙여
각 문장의 번역을 한 줄씩 내놓아라. 번호를 합치거나 건너뛰지 마라.
한 영어 문장이 한국어로 길어지면 그대로 길게 쓰되, 번호는 하나로 유지한다."""


ALIGN_SYSTEM = translate_system() + _ALIGN_EXTRA


def align_system(field: str | None = None) -> str:
    return translate_system(field) + _ALIGN_EXTRA


def align_prompt(sents: list[str], title: str = "", author: str = "",
                 field: str | None = None) -> str:
    src = f"[출처] {author + ', ' if author else ''}{title}\n\n" if title else ""
    gl = glossary_block(" ".join(sents), field)
    body = "\n".join(f"[{i}] {s}" for i, s in enumerate(sents, 1))
    return (f"{src}아래 {len(sents)}개 문장을 각각 한국어로 번역하라. "
            f"출력은 [번호] 번역문 형태로 {len(sents)}줄이어야 한다."
            f"{gl}\n\n{body}")


_NUMLINE = re.compile(r'^\s*\[(\d+)\]\s*(.*)$')


def parse_aligned(out: str, n: int) -> list[str] | None:
    """[번호] 형식 응답을 순서대로 되돌린다. 개수가 안 맞으면 None."""
    got: dict[int, list[str]] = {}
    cur = None
    for line in out.splitlines():
        m = _NUMLINE.match(line)
        if m:
            cur = int(m.group(1))
            got[cur] = [m.group(2).strip()]
        elif cur is not None and line.strip():
            got[cur].append(line.strip())
    if len(got) != n or set(got) != set(range(1, n + 1)):
        return None
    return [" ".join(got[i]).strip() for i in range(1, n + 1)]


# ---------------- 여러 문단을 한 번에 (호출 고정비 절약) ----------------
_BATCH_EXTRA = """

추가 규칙 — 번호 유지:
입력은 [문단.문장] 번호가 붙은 영어 문장들이다. 예: [3.2] 는 3번 문단의
2번째 문장이다. 같은 번호를 그대로 붙여 각 문장의 번역을 한 줄씩 내놓아라.
번호를 합치거나 건너뛰거나 새로 만들지 마라. 입력에 있는 모든 번호가
출력에도 정확히 한 번씩 나와야 한다."""

_PAIR_NUM = re.compile(r'^\s*\[(\d+)\.(\d+)\]\s*(.*)$')


BATCH_ALIGN_SYSTEM = translate_system() + _BATCH_EXTRA


def batch_align_system(field: str | None = None) -> str:
    return translate_system(field) + _BATCH_EXTRA


def batch_align_prompt(units: list[tuple[int, list[str]]],
                       title: str = "", author: str = "",
                       field: str | None = None) -> str:
    """units: [(문단번호, [문장, ...]), ...]"""
    src = f"[출처] {author + ', ' if author else ''}{title}\n\n" if title else ""
    lines, n = [], 0
    for k, sents in units:
        for j, sent in enumerate(sents, 1):
            lines.append(f"[{k}.{j}] {sent}")
            n += 1
    gl = glossary_block(" ".join(s for _, ss in units for s in ss), field)
    return (f"{src}아래 {n}개 문장을 각각 한국어로 번역하라. "
            f"출력은 [문단.문장] 번역문 형태로 정확히 {n}줄이어야 한다."
            f"{gl}\n\n" + "\n".join(lines))


def parse_batch_aligned(out: str) -> dict[int, dict[int, str]]:
    """응답을 {문단번호: {문장번호: 번역}} 으로 되돌린다."""
    res: dict[int, dict[int, str]] = {}
    cur = None
    for line in out.splitlines():
        m = _PAIR_NUM.match(line)
        if m:
            k, j = int(m.group(1)), int(m.group(2))
            cur = (k, j)
            res.setdefault(k, {})[j] = m.group(3).strip()
        elif cur and line.strip():
            res[cur[0]][cur[1]] += " " + line.strip()
    return res
