#!/usr/bin/env python3
"""黑灰产行话词典同步 / 合并工具。

上游（数据源，CC BY 4.0）：
  threathunterX/underground-slang-lexicon
   data/entries/NN-<分类>.json   9 个分类文件，结构化词条
   data/schema.json              字段规范

本地扩展：
  local-data/local-entries.json 本仓库独有的词条（上游未收录）

产出（供 anti-cheating-assistant 技能直接消费）：
  skill/underground-slang-lexicon.md   扁平不分章、拼音全局序
  skill/underground-slang-index.md     双视图索引
  skill/manifest.json                  构建元信息（上游 sha / 计数 / 时间）

子命令：
  report          打印上游与本地差异
  extract-local   从既有词典 md 中抽出本地词条 -> local-data/local-entries.json
  build           上游 + 本地 -> skill/ 下两个 md
  check           上游是否有更新（有则退出码 2）

用法：
  python3 tools/merge_local.py report
  python3 tools/merge_local.py extract-local --from <lexicon.md> --index <index.md>
  python3 tools/merge_local.py build --out skill/
"""

from __future__ import annotations

import argparse
import base64
import datetime as dt
import hashlib
import json
import os
import re
import sys
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path

REPO = "threathunterX/underground-slang-lexicon"
REF = "main"
ROOT = Path(__file__).resolve().parent.parent
CACHE = Path(os.environ.get("SLANG_CACHE_DIR") or ROOT / ".cache" / "upstream")
LOCAL_DATA = ROOT / "local-data" / "local-entries.json"

# 置信度三档，用词与上游 tools/build_dict.py 的 CONF_LABEL 完全一致（用户要求
# 「用上游的经典表述，不要生造新词」）：高 / 中 / 待核实。
# 上游 to-verify 在本产物里就叫「待核实」——本地扩展没有研判流程，它的置信度
# **一律视为待核实**，与上游 to-verify 同级。
CONF_CN = {"high": "高", "medium": "中", "to-verify": "待核实"}
LABELED_CONF = frozenset({"high", "medium", "low"})  # 上游「已标注」的三档
UNLABELED_CN = "待核实"
# 尾行字段名也照上游：`研判置信度：X`
CONF_FIELD = "研判置信度"

# 本技能的用途是**咨询**（客户见到不认识的词才来问，要的是解释），不是**情报**
# （词出现即风险，要的是命中即告警）。所以产物按**词典**体例编排，而不是按情报字段平铺。
#
# 体例**以本地词典为准**：`### 词` → `又作` → 义项 → `研判置信度：X`。
# 只有上游才有的字段一律不渲染：
#   mechanism     在链条中
#   example       用例           example_note  无示例原因
#   frequency     频次
#   lang/region   语种·地区（语种另用于分节，见 LANG_CN）
#   first_seen    收录版本        related       关联词表
# 上游的 `ambiguity` 讲的就是「这个词在正常语境下什么意思」——本质是义项，照收，
# 与黑灰产义同层级编号，不丢、也不单列成二等条目。
#
# **词条下方不放分类**：上游自己的 dict/黑话词典.md 里，category 是 `## 分类`
# 分节标题，不是词条下方的一行；本地 `scenes` 是另一套轴（行业场景），原文也没有
# 这个词条下方分类行。本产物是扁平检索（一词一块），词头下只放义项，分类信息
# 只在索引的「视图二」里按上游分类 / 本地场景汇总。
DIGIT_BUCKET = "0"
# 本地词典里「→ 见 “X”」这类纯跳转块，应表达为别名而非正文
STUB_RE = re.compile(r"→?\s*见\s*[“\"「]?\s*(.+?)\s*[”\"」]?\s*$")

# 语种。上游 entries 自带 `lang` 字段（zh/en/pt/id/vi/tl…），本地没有这个字段，
# 按词头字符判：汉字开头 → 中文，0-9 / A-Z 开头 → 英文。
# 名称与上游 tools/build_dict.py 的 LANG_SHORT 一致。
LANG_CN = {"zh": "中文", "en": "英文", "pt": "葡萄牙语", "id": "印尼语",
           "vi": "越南语", "tl": "他加禄语", "ru": "俄语", "th": "泰语", "hi": "印地语"}
LANG_ORDER = ["zh", "en", "pt", "id", "vi", "tl", "ru", "th", "hi"]


def infer_lang(term: str) -> str:
    """没有 lang 字段时按词头字符判语种（本地扩展走这条）。"""
    c = term[:1]
    return "zh" if "\u4e00" <= c <= "\u9fff" else "en"


# 标点统一：**一律成本地用的中文引号**。
#   上游散文里有 721 对「」、548 个半角 "（都是成对的）、4 个半角 '（两对）；
#   本地正文一处半角引号都没有，全用“”。同一部词典不能有几套引号。
# 只在**成对**（计数为偶数）时改，落单的说明是撇号之类的用法，不动。
def _pair_up(s: str, ch: str, open_ch: str, close_ch: str) -> str:
    if s.count(ch) % 2:
        return s
    out, opening = [], True
    for c in s:
        if c == ch:
            out.append(open_ch if opening else close_ch)
            opening = not opening
        else:
            out.append(c)
    return "".join(out)


def cn_quotes(s: str) -> str:
    s = (s or "").replace("「", "“").replace("」", "”")
    s = _pair_up(s, '"', "“", "”")
    s = _pair_up(s, "'", "‘", "’")
    return s



# ==========================================================================
# 拼音
# ==========================================================================

_PINYIN_MISSING_WARNED = False


def _pinyin(text: str) -> str:
    global _PINYIN_MISSING_WARNED
    try:
        from pypinyin import lazy_pinyin
    except ImportError:  # CI 里会 pip install，本地缺失时退化为不排序
        if not _PINYIN_MISSING_WARNED:
            _PINYIN_MISSING_WARNED = True
            print(
                "!! 未安装 pypinyin：汉字退回按 Unicode 码位排序，分节会退化成"
                "「## 一 / ## 三 / ## 云」这种按首字的几百个小节，与 CI 产物不一致。\n"
                "   请用装了 pypinyin 的解释器构建（CI 里是 pip install pypinyin）。",
                file=sys.stderr,
            )
        return text
    return "".join(lazy_pinyin(text, errors=lambda c: c)).lower()


def sort_key(term: str, lang: str = "zh") -> tuple:
    """返回 (语种序, 分组桶, 排序键, 原名)。

    语种不同**不混排**：`zh` 在最前，其余按上游 LANG_ORDER。中文内部按拼音首字母
    分 A–Z 桶（首字不是汉字或拼音不出来 → `## 符号与数字` 桶）；英文等非中文语种
    整个语种一节，内部按小写字符串排。
    """
    if not term:
        return (0, DIGIT_BUCKET, "", term)
    rank = LANG_ORDER.index(lang) if lang in LANG_ORDER else len(LANG_ORDER)
    if lang != "zh":
        return (rank, "", term.lower(), term)
    py = _pinyin(term)
    first = py[:1]
    bucket = first.upper() if first.isascii() and first.isalpha() else DIGIT_BUCKET
    return (rank, bucket, py, term)


# ==========================================================================
# 上游抓取
# ==========================================================================

def _api(url: str) -> dict:
    """GitHub API 请求。带 GITHUB_TOKEN 时走认证——CI 里匿名请求只有 60 次/小时，
    容易在反复重建时踩限流。"""
    last: Exception | None = None
    headers = {
        "User-Agent": "anti-cheating-assistant-sync",
        "Accept": "application/vnd.github+json",
    }
    token = os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN")
    if token:
        headers["Authorization"] = f"Bearer {token}"
    for _ in range(3):
        try:
            req = urllib.request.Request(url, headers=headers)
            with urllib.request.urlopen(req, timeout=45) as resp:
                return json.load(resp)
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
            last = exc
    raise RuntimeError(f"请求失败：{url}（{last}）")


def upstream_tree_sha() -> str:
    """上游 main 当前的 tree sha。`build` 写进 manifest 的就是这个值，
    定时巡检直接复用同一函数，保证比对的两边是同一个东西。"""
    return _api(f"https://api.github.com/repos/{REPO}/git/trees/{REF}?recursive=1")["sha"]


def fetch_upstream(verbose: bool = True) -> tuple[list[dict], str]:
    """返回 (全部词条, 上游 commit sha)。按 blob sha 缓存。"""
    CACHE.mkdir(parents=True, exist_ok=True)
    tree = _api(f"https://api.github.com/repos/{REPO}/git/trees/{REF}?recursive=1")
    blobs = {t["path"]: t["sha"] for t in tree["tree"]
             if t["type"] == "blob" and t["path"].startswith("data/entries/")}
    if not blobs:
        raise RuntimeError("上游未找到 data/entries/*.json，仓库结构可能已变更")

    entries: list[dict] = []
    for path, sha in sorted(blobs.items()):
        cached = CACHE / f"{sha}.json"
        if cached.exists():
            raw = cached.read_bytes()
        else:
            blob = _api(f"https://api.github.com/repos/{REPO}/git/blobs/{sha}")
            raw = base64.b64decode(blob["content"])
            cached.write_bytes(raw)
            if verbose:
                print(f"  ↓ {path.rsplit('/', 1)[-1]}（{len(raw)//1024}KB）", file=sys.stderr)
        for e in json.loads(raw):
            e["_src"] = path.rsplit("/", 1)[-1]
            entries.append(e)

    keep = {f"{s}.json" for s in blobs.values()}
    for f in CACHE.glob("*.json"):
        if f.name not in keep:
            f.unlink()
    return entries, tree["sha"]


# ==========================================================================
# 本地词典解析（用于首次抽取）
# ==========================================================================

def parse_lexicon(path: Path) -> dict[str, str]:
    """切出「### 词」块。

    块尾必须也在 `## 小节标题` 处截断：原词典每节最后一个词条的正文后面紧跟着
    下一节标题（`## A`、`## B`…），只按 `^### ` 切会把标题吞进上一条正文，
    产物里就会出现「正文末尾莫名冒出个 A / B」。上一版正是踩了这个坑。
    """
    text = path.read_text(encoding="utf-8")
    return {
        m.group(1).strip(): clean_body(m.group(2))
        for m in re.finditer(r"^### (.+?)\s*$(.*?)(?=^### |^## |\Z)", text, re.M | re.S)
    }


# 正文里不该出现的结构性残留：markdown 标题行（`## A`）、孤立的章节字母
_STRAY_HEADING = re.compile(r"(?m)^[ \t]*#{1,6}[ \t]*[^\s#]{0,12}[ \t]*$\n?")


def clean_body(body: str) -> str:
    """洗掉正文里的 markdown 标题残留，并压掉首尾空行。

    这些行来自原词典的分节标题，是抽取时的边界错误，不是释义内容。
    在读取侧再洗一遍，旧数据不清也不会漏到产物里。
    """
    return _STRAY_HEADING.sub("", body or "").strip()


def parse_index_scenes(path: Path) -> dict[str, list[str]]:
    text = path.read_text(encoding="utf-8")
    if "## 视图二" not in text:
        return {}
    scenes: dict[str, list[str]] = {}
    for m in re.finditer(r"^## (.+?)（(\d+)）\s*$(.*?)(?=^## |\Z)",
                         text.split("## 视图二", 1)[1], re.M | re.S):
        scene = m.group(1).strip()
        for item in m.group(3).replace("\n", "、").split("、"):
            item = item.strip()
            if not item:
                continue
            # 去掉「（多义）」「（别名→X）」这类后缀，取主词名
            name = item
            while True:
                stripped = re.sub(r"（(?:多义|别名→[^）]*)）$", "", name).strip()
                if stripped == name:
                    break
                name = stripped
            scenes.setdefault(name, []).append(scene)
    return scenes


def classify(term: str, body: str, conf_of: dict[str, str],
             up_akas: dict[str, str]) -> tuple[str, str | None, str | None]:
    """判定一条本地词条相对上游的归属。

    **只看置信度，不看措辞相似度**。相似度分不出同义/异义（本地白话与上游压缩句式的
    三字组重合中位数仅 0.02，同义对也会低到 0），所以规则改成确定性的：

      alias      正文是纯别名跳转（「→ 见 X」）
      override   上游有同名词条、且**已标注**（高/中）→ 上游完全覆盖，本地整条丢弃
      both       上游有同名词条、但**未标注**（to-verify）→ 两方义项并列进同一块
      local-only 上游没有这个词条 → 本地独立成条（上游只把本词当别名时也归这一类）

    每次构建实时重算，不依赖 JSON 里冻结的状态。
    """
    bare = re.sub(r"^【释义】\s*", "", body.strip())

    stub = STUB_RE.match(bare) if len(bare) <= 40 else None
    if stub:
        return "alias", stub.group(1).strip(), None

    if term in conf_of:
        if conf_of[term] in LABELED_CONF:
            return "override", None, None
        return "both", None, "上游同名词条未标注，两方义项并列"
    if term in up_akas:
        # 上游没这个词条、只把它挂在别的词下当别名：不算「相同词条」，本地独立保留
        return "local-only", None, f"上游把本词并入「{up_akas[term]}」"
    return "local-only", None, None


# ==========================================================================
# 提取本地扩展
# ==========================================================================

def extract_local(lexicon: Path, index: Path, out: Path) -> dict:
    """把既有词典里上游没有的词条抽成 local-entries.json。

    重合词条按置信度处理：上游已标注 → 本地让位（status=override）；
    上游未标注 → 两方义项并列（status=both）。body 始终保留以便追溯。
    """
    upstream, sha = fetch_upstream()
    conf_of = {e["term"]: (e.get("confidence") or "") for e in upstream}
    up_akas = {a: e["term"] for e in upstream for a in (e.get("aka") or [])}

    local = parse_lexicon(lexicon)
    scenes = parse_index_scenes(index)

    records = []
    for term, body in local.items():
        status, alias_of, note = classify(term, body, conf_of, up_akas)
        rec = {"term": term, "status": status,
               "scenes": scenes.get(term, []), "body": body}
        if alias_of:
            rec["alias_of"] = alias_of
        if note:
            rec["note"] = note
        records.append(rec)
    records.sort(key=lambda r: sort_key(r["term"]))

    doc = {
        "schema": "anti-cheating-assistant/local-extension@1",
        "note": "本地独有词条。status 是抽取时的快照，build() 每次按最新上游重算。"
                "上游同名词条已标注 → override（本地让位）；上游同名词条未标注 → "
                "both（两方义项并列）；纯别名跳转块 → alias + alias_of。均不删除以便追溯。",
        "extracted_at": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
        "source_doc": lexicon.name,
        "entries": records,
    }
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(doc, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")

    count = lambda s: sum(1 for r in records if r["status"] == s)
    return {"total": len(records), "local_only": count("local-only"),
            "override": count("override"), "both": count("both"),
            "alias": count("alias"), "out": str(out)}


# ==========================================================================
# 渲染
# ==========================================================================

def _render_senses(senses: list[str]) -> list[str]:
    """义项统一渲染：单条直接成段，多条按 `1.` `2.` 编号。上游块与本地块共用。

    **编号用 Markdown 有序列表的规范写法**：`1.` + 一个半角空格 + 正文。
    """
    if not senses:
        return []
    if len(senses) == 1:
        return ["", senses[0]]
    return [""] + [f"{i}. {s}" for i, s in enumerate(senses, 1)]


# 本地 V1/V2 词典里，多义词的正文自带 `1.` `2.` 编号（11 条：gm料/点位/精聊/机票料/
# 料/买断/融车/三包/三黑料/收单/一道）。这里把各种写法的编号（`1.` `1、` `①`
# `（1）` `一、`）统一剥成同一形态，再按序号重新排——因为上游词条本身没有编号，
# 要插进去只能从 1 起排；**但排出来的形式必须和本地一样是 `1.`**。
_SENSE_MARK = re.compile(
    r"^\s*(?:[1-9]\d?\s*[.、)）]|[①②③④⑤⑥⑦⑧⑨⑩]|（[1-9]）|\([1-9]\)|[一二三四五六七]、)\s*")


def split_local_senses(body: str) -> tuple[str, list[str]]:
    """把本地正文拆成 (前言, [义项])。

    编号前的段落算前言（如 `一道` 的「又称“一道料”…」）；编号后的段落归属当前义项，
    直到下一个编号出现为止。
    """
    text = re.sub(r"^【释义】\s*", "", body.strip())
    paras = [p.strip() for p in re.split(r"\n\s*\n|\n", text) if p.strip()]
    lead: list[str] = []
    senses: list[str] = []
    for p in paras:
        if _SENSE_MARK.match(p):
            senses.append(_SENSE_MARK.sub("", p, count=1).strip())
        elif senses:
            # 续行直接贴到上一条义项末尾：中文句末自带「。」，再加空格会渲出「。 精聊…」
            # 这种半角空隙。只有两侧都是 ASCII 字母数字时才补一个空格，免得粘成一坨。
            prev = senses[-1]
            glue = " " if re.search(r"[0-9A-Za-z]$", prev) and re.match(r"[0-9A-Za-z]", p) else ""
            senses[-1] = f"{prev}{glue}{p}"
        else:
            lead.append(p)
    return "\n\n".join(lead), senses


# 本地正文里以「又称“A”“B”。」形式给出的异名，应上提为 `又作`（词条级），
# 而不是当前言散落在义项之前。只匹配「整段就是别名列表」的情形，避免误伤普通正文。
_AKA_LEAD = re.compile(r"^又称\s*((?:[“\"][^”\"]+[”\"]\s*)+)[。.]?$")


def lead_aliases(lead: str) -> list[str]:
    m = _AKA_LEAD.match((lead or "").strip())
    return re.findall(r"[“\"]([^”\"]+)[”\"]", m.group(1)) if m else []


# 前言以「又称“A”“B”，指……」开头的情形（`车手` 的「又称“职业取款人”，指在
# 电信网络诈骗犯罪中专门负责实施取款…」）。别名是前言级、也就是词条级，直接剥成
# `又作`，剩下「指……」当正文，读起来与上游条目同构。
_AKA_INLINE = re.compile(
    r"^又称\s*((?:[“\"][^”\"]+[”\"]\s*[、，,]?\s*)+)[，,、。.]?\s*(.*)$", re.S)


def split_lead_aka(lead: str) -> tuple[list[str], str]:
    """(别名列表, 剩余前言)。整段只有别名列表时，剩余前言为空串。"""
    s = (lead or "").strip()
    m = _AKA_LEAD.match(s)
    if m:
        return re.findall(r"[“\"]([^”\"]+)[”\"]", m.group(1)), ""
    m = _AKA_INLINE.match(s)
    if m:
        return re.findall(r"[“\"]([^”\"]+)[”\"]", m.group(1)), m.group(2).strip()
    return [], s


# 别名嵌在某一条义项正文里（「又称“股民料”，指股民数据」）——它只属于那一个义项，
# **不能**上提成词条级 `又作`（否则会被读成整个词条又叫股民料），但**必须**进索引：
# 用户拿着「股民料」来查，正文里没有 `### 股民料`，索引也不登记的话就彻底查不到。
_INLINE_AKA = re.compile(r"又称\s*((?:[“\"][^”\"]+[”\"]\s*)+)")


def sense_aliases(sense: str) -> list[str]:
    out: list[str] = []
    for m in _INLINE_AKA.finditer(sense or ""):
        out += re.findall(r"[“\"]([^”\"]+)[”\"]", m.group(1))
    return out


def render_upstream_block(e: dict, extra_senses: list[str] | None = None) -> str:
    """上游词条块。体例以本地词典为准，只剩四样：

        ### 词  →  又作  →  义项  →  研判置信度：X

    **只有上游才有的字段一律不渲染**：在链条中（mechanism）、用例（example）、
    无示例原因（example_note）、频次（frequency）、语种/地区（lang/region）、
    收录版本（first_seen）、关联词表（related）。上游自己的 `dict/黑话词典.md` 把
    category 当 `## 分节标题` 用，本地 `scenes` 是另一套轴，**词条下方都不放分类**。

    `extra_senses` 是「上游同名但未标注」时并进来的本地义项（见 classify 的 both）。
    """
    lines = [f"### {cn_quotes(e['term'])}"]

    # `又作` 是**词条级**（词形变体），不是义项级，所以放在义项之前、紧贴词头：
    #   ① 上游 schema 的定义就是「同义词、变体、谐音写法、英文缩写」——同一个意思的其他写法；
    #   ② 上游自己渲染的 dict 里，`又作` 也排在「释义」之前；
    #   ③ 数据模型没有义项归属字段，aka 是 entry 顶层数组，且全局约束「一个别名只能挂一个词条」；
    #   ④ 本产物索引是「别名→词条」，没有「别名→第2义项」的寻址能力，整块返回，结构上只能词条级。
    # 放在义项之后会被误读成「最后一个义项的别名」，故上移。
    if e.get("aka"):
        lines += ["", "又作：" + "、".join(cn_quotes(a) for a in e["aka"])]

    # 词典体例：**所有义项同一层级**，有几个就编几个号，不预设只有「黑灰产义 + 另义」两条。
    # 来源：1. definition 黑灰产义　2. ambiguity 该词在正常语境下的意思　3. 并入的本地区分义项
    senses: list[str] = []
    if e.get("definition"):
        senses.append(cn_quotes(e["definition"]))
    if e.get("ambiguity"):
        senses.append(cn_quotes(e["ambiguity"]))
    if extra_senses:
        senses += [cn_quotes(s) for s in extra_senses]

    lines += _render_senses(senses)

    conf = e.get("confidence") or ""
    lines += ["", f"{CONF_FIELD}：{CONF_CN.get(conf, UNLABELED_CN)}"]
    return "\n".join(lines) + "\n"


def render_local_block(rec: dict) -> str:
    """本地独有词条。与上游块共用同一套骨架：又作 → 义项 → 尾部元信息。

    本地正文里自带 `1.` `2.` 编号的多义词（11 条）在这里被拆成义项，按 `1.` `2.` 重排，
    避免同一部词典里出现两套编号体例。
    """
    lead, senses = split_local_senses(rec["body"])
    alias, lead = split_lead_aka(lead)

    out = [f"### {cn_quotes(rec['term'])}"]
    if alias:
        out += ["", "又作：" + "、".join(cn_quotes(a) for a in alias)]
    if lead:
        out += ["", cn_quotes(lead)]
    out += _render_senses([cn_quotes(s) for s in senses])

    m = re.search(r"上游把本词并入[“\"「](.+?)[”\"」]", rec.get("note") or "")
    if m and m.group(1):
        out += ["", f"相关义项：上游把本词并入“{m.group(1)}”，那边释义侧重不同；此处按本地口径独立成条。"]

    # 本地扩展没有研判流程，置信度**一律视为待核实**（与上游 to-verify 同档）
    out += ["", f"{CONF_FIELD}：{UNLABELED_CN}"]
    return "\n".join(out) + "\n"


def build(out_dir: Path) -> dict:
    """合成产物。上游**全量收录**（含未标注档），本地按置信度归位。"""
    upstream, up_sha = fetch_upstream()
    conf_of = {e["term"]: (e.get("confidence") or "") for e in upstream}
    up_akas = {a: e["term"] for e in upstream for a in (e.get("aka") or [])}

    local_doc = json.loads(LOCAL_DATA.read_text(encoding="utf-8"))
    # 旧抽取结果里混进了原词典的分节标题（`## A`…），读取侧统一洗掉
    for _r in local_doc["entries"]:
        _r["body"] = clean_body(_r["body"])

    # 归属每次构建实时重算，不依赖 JSON 里冻结的 status
    local_aliases: dict[str, str] = {}   # 纯别名跳转块
    local_recs: list[dict] = []          # 独立成条的本地词条
    paired: dict[str, dict] = {}         # 上游同名但未标注 → 义项并列
    overridden: set[str] = set()         # 上游同名且已标注 → 本地整条丢弃
    for r in local_doc["entries"]:
        status, alias_of, note = classify(r["term"], r["body"], conf_of, up_akas)
        if status == "alias":
            local_aliases[r["term"]] = alias_of
        elif status == "override":
            overridden.add(r["term"])
        elif status == "both":
            paired[r["term"]] = {"body": r["body"], "scenes": r.get("scenes", [])}
        else:
            rec = {"term": r["term"], "scenes": r.get("scenes", []), "body": r["body"]}
            if note:
                rec["note"] = note
            local_recs.append(rec)

    # ---- 合并渲染（扁平检索：一词一块，先按语种分块，中文内部按拼音 A–Z 分节） ----
    blocks: list[tuple[tuple, str]] = []
    for e in upstream:
        extra: list[str] | None = None
        p = paired.get(e["term"])
        if p:
            # 上游未标注 + 本地有同名 → 两边义项并列，编号连续
            lead, ss = split_local_senses(p["body"])
            _, lead = split_lead_aka(lead)
            extra = list(ss)
            if lead:
                extra.insert(0, lead)
        lang = e.get("lang") or "zh"
        blocks.append((sort_key(e["term"], lang), render_upstream_block(e, extra)))
    # 本地正文里的异名「又称“A”“B”」，两种去向：
    #   ① 落在前言（`一道` 的「又称“一道料”“一道资金”。」、`车手` 的
    #      「又称“职业取款人”，指…」）→ 词条级，索引直指该词条；
    #   ② 嵌在某条义项里（`gm料` 义2 的「又称“股民料”」）→ 留在义项里，不上提，
    #      索引目标标到义项（「gm料 义2」），免得被读成整个词条又叫股民料。
    # 两种都**必须进索引**——用户拿着「股民料」来查，正文没有 `### 股民料`，
    # 索引也不登记就彻底查不到。别名跳转块（原文「→ 见 X」）并进同一张表统一去重。
    # 被上游「完全覆盖」的词条不进索引：覆盖就是覆盖，连别名一起作废。
    local_alias_registry: dict[str, list[str]] = {}

    def _reg(alias: str, target: str) -> None:
        lst = local_alias_registry.setdefault(alias, [])
        if target not in lst:
            lst.append(target)

    for r in local_doc["entries"]:
        if r["term"] in local_aliases or r["term"] in overridden:
            continue  # 别名跳转块下面单独处理；被覆盖的词条连别名也不留
        lead, senses = split_local_senses(r["body"])
        for a in lead_aliases(lead) + sense_aliases(lead):
            _reg(a, r["term"])
        for i, s in enumerate(senses, 1):
            for a in sense_aliases(s):
                _reg(a, f"{r['term']} 义{i}" if len(senses) > 1 else r["term"])

    for a, target in local_aliases.items():
        _reg(a, target)

    for r in local_recs:
        lang = infer_lang(r["term"])
        blocks.append((sort_key(r["term"], lang), render_local_block(r)))
    blocks.sort(key=lambda x: x[0])

    total = len(blocks)
    stamp = dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%d")
    lang_count: dict[str, int] = {}
    for key, _ in blocks:
        lg = LANG_ORDER[key[0]] if key[0] < len(LANG_ORDER) else "其他"
        lang_count[lg] = lang_count.get(lg, 0) + 1
    lang_line = "、".join(
        f"{LANG_CN.get(lg, lg)} {n}"
        for lg, n in sorted(lang_count.items(),
                            key=lambda kv: (LANG_ORDER.index(kv[0]) if kv[0] in LANG_ORDER else 99)))
    lexicon_lines = [
        "# 黑话词典（合成版）",
        "",
        f"本文件由 `tools/merge_local.py` 自动生成，**请勿手工编辑**（{stamp}）。",
        "",
        f"- 上游数据：{REPO} @ `{up_sha[:10]}`（CC BY 4.0，威胁猎人 Threat Hunter 黑灰产研究团队）。"
        f"**本文件是衍生作品**：上游文件不在本仓寄存，每次构建实时取自上游 API；"
        f"本仓只放本地扩展、合并脚本与产出物。",
        f"- 本地扩展：`local-data/local-entries.json`（本仓库独有词条）",
        f"- 词条合计：**{total}**（上游 {len(upstream)} + 本地独立 {len(local_recs)}）"
        f"；另有 {len(paired)} 个同名未标注的词条，两方义项并进上游块",
        f"- 按语种：{lang_line}。**不同语种不混排**——中文按拼音分 `## A`–`## Z` 小节，"
        f"英文、印尼语、他加禄语、葡萄牙语、越南语各自单独成节，节内按字母序。"
        f"上游词条的语种取上游 `lang` 字段；本地词条没有该字段，按词头字符判"
        f"（汉字开头 → 中文，0-9 / A-Z 开头 → 英文）。",
        f"- **词典体例**（以本地词典为准）：`### 词` → `又作`（**词条级**："
        f"同一个意思的其他写法）→ **义项**（单条直接成段，多条按 `1.` `2.` 编号，同一层级）"
        f"→ 尾行 `研判置信度：高 / 中 / 待核实`。",
        f"- **词条下方不放分类**：上游自己的 `dict/黑话词典.md` 把分类用作 `## 分节标题`，"
        f"本地 `scenes` 是另一套轴，两种原文都没有“词条下方一行分类”这种写法。分类与场景的"
        f"分组只在索引文件里汇总。",
        f"- 只有上游才有的字段一律不渲染：`在链条中`、`用例`、`无示例原因`、`频次`、"
        f"`语种/地区`、`收录版本`、`关联词表`。",
        f"- **同名词条的处理只看置信度**：上游已标注（高/中）→ 上游完全覆盖，本地整条丢弃；"
        f"上游未标注 → 两方义项并列。本地扩展的置信度一律视为“待核实”。",
        f"- 上游 `ambiguity` 讲的就是该词在正常语境下的义项，故与黑灰产义并列编号。",
        f"- 引号统一用中文引号：上游散文里成对出现的直角引号，渲染时一律改写成中文引号。",
    ]
    lexicon_lines += [
        "",
        "一词多义不拆词，义项同层级编号；上游与本地词条共用同一套骨架，"
        "只以置信度区分（`高` / `中` / `待核实`）。",
    ]
    # 分节：语种变化 → `## 语种名`；同语种内桶变化（仅中文有 A–Z 桶）→ `## 桶名`
    cur_lang: int | None = None
    cur_bucket = ""
    for key, blk in blocks:
        lang_idx, bucket = key[0], key[1]
        if lang_idx != cur_lang:
            cur_lang, cur_bucket = lang_idx, ""
            lg = LANG_ORDER[lang_idx] if lang_idx < len(LANG_ORDER) else "其他"
            lexicon_lines += ["", f"## {LANG_CN.get(lg, lg)}（{lang_count.get(lg, 0)} 条）", ""]
        if bucket != cur_bucket:
            cur_bucket = bucket
            label = "符号与数字" if bucket == DIGIT_BUCKET else bucket
            lexicon_lines += ["", f"## {label}", ""]
        lexicon_lines += [blk]

    # ---- 索引 ----
    idx_lines = [
        "# 黑灰产行话词典词条索引",
        "",
        f"词典全文见 [underground-slang-lexicon.md](underground-slang-lexicon.md)"
        f"（{total} 词条，{lang_line}；一词一块，按语种分节，中文节内按拼音 A–Z）。"
        f"本文件由脚本生成，请勿手工编辑。",
        "",
        "**使用纪律（控制 token 成本，必须遵守）：**",
        "- **永远不要整读词典**。先查本索引判断词条是否存在；不存在，直接结束。",
        "- 存在时用 Grep 以词条名在词典中定位，只读命中块（一词一块，`### 词条名` 开头，到下一个 `### ` 为止）。",
        "- 索引未收录的别名、缩写拿不准时，直接对词典全文 Grep；"
        "“别名→X”表示该词是别名，主释义收录在 X 条名下；"
        "写成“别名→X 义2”的，表示该叫法**只指第 2 个义项**（原文在义项内写了“又称”），"
        "不是整个词条的同义词。",
        "- 块内**义项按 `1.` `2.` 编号**，同一层级。第二个义项通常是该词在**正常语境**下的意思。"
        "用户往往是见到某个词才来问，先判断他那句话落在哪个义项上，必要时都摆出来。",
        "- 尾行 `研判置信度：高/中` 是上游研判结果，可据此决定采信强度；"
        "尾行 `研判置信度：待核实` 指上游自己也未核定、或属本地扩展，引用时按未复核对待。",
        "- **不同语种不混排**：中文按拼音 A–Z，英文 / 印尼语 / 他加禄语 / 葡萄牙语 / 越南语"
        "各自单独一节。查不到时先想想是不是别的语种。",
        "",
    ]

    # 视图一：按语种全列。中文按拼音 A–Z 分桶，其余语种整个语种一段。
    local_only_terms = {r["term"] for r in local_recs}
    up_terms = set(conf_of)
    # (语种, 桶) → 条目
    by_bucket: dict[tuple[str, str], list[str]] = {}
    alias_bucket: dict[tuple[str, str], list[str]] = {}

    def bucket_of(term: str, lang: str | None = None) -> tuple[str, str]:
        lg = lang or infer_lang(term)
        k = sort_key(term, lg)
        return lg, k[1]

    for e in upstream:
        lg = e.get("lang") or "zh"
        by_bucket.setdefault(bucket_of(e["term"], lg), []).append(e["term"])
    for e in upstream:
        for a in e.get("aka") or []:
            if a in local_only_terms:  # 本地已独立成条，不再重复登记为别名
                continue
            alias_bucket.setdefault(bucket_of(a), []).append(f"{a}（别名→{e['term']}）")

    # 本地别名跳转块：目标先落到本地独立条，再落到上游主词
    def resolve(t: str) -> str:
        if t in local_only_terms:
            return t
        if t in up_terms:
            return t
        if t in up_akas:
            return up_akas[t]
        return t

    for a, targets in local_alias_registry.items():
        if a in up_terms or a in local_only_terms:
            continue  # 别名本身已是词条，不再当别名登记
        alts: list[str] = []
        for t in targets:
            base = re.sub(r"\s*义\d{1,2}$", "", t).strip()
            suffix = t[len(base):]
            dest = resolve(base)
            # 目标被改判到别的词时，本地那套义项编号已失效，不能带过去
            alt = dest if dest != base else f"{dest}{suffix}"
            if alt not in alts:
                alts.append(alt)
        alias_bucket.setdefault(bucket_of(a), []).append(
            f"{a}（别名→{' / '.join(alts)}）")
    for r in local_recs:
        by_bucket.setdefault(bucket_of(r["term"], infer_lang(r["term"])), []).append(r["term"])

    idx_lines.append(f"## 视图一：按语种全列（{total} 词条 + "
                     f"{sum(len(v) for v in alias_bucket.values())} 条别名）")
    idx_lines.append("")
    block_names = {k[3] for k, _ in blocks}

    def alive(item: str) -> bool:
        """别名必须指向真实存在的块，否则丢弃（防上游删词后留悬空引用）。

        目标可能带义项后缀（「gm料 义2」）或多个目标（「A 义1 / B 义2」），
        判定存在性时把后缀剥掉，逐个核对。
        """
        m = re.match(r"^(.+?)（别名→(.+?)）$", item)
        if not m:
            return True
        for t in m.group(2).split(" / "):
            t = re.sub(r"\s*义\d{1,2}$", "", t).strip()
            if t not in block_names:
                return False
        return True

    def sort_item(s: str, lg: str) -> tuple:
        return sort_key(re.sub(r"（别名→.*?）$", "", s), lg)

    ordered_langs = [lg for lg in LANG_ORDER if lg in lang_count] + \
                    [lg for lg in lang_count if lg not in LANG_ORDER]
    for lg in ordered_langs:
        n_lang = sum(len(v) for (l, _), v in by_bucket.items() if l == lg) + \
                 sum(len(v) for (l, _), v in alias_bucket.items() if l == lg)
        idx_lines.append(f"**{LANG_CN.get(lg, lg)}**（{n_lang} 条，"
                         f"{'拼音 A–Z 分桶' if lg == 'zh' else '按字母序'}）")
        idx_lines.append("")
        keys = sorted({b for (l, b) in list(by_bucket) + list(alias_bucket) if l == lg},
                      key=lambda b: (b == DIGIT_BUCKET, b))
        for b in keys:
            items = sorted(by_bucket.get((lg, b), []) + alias_bucket.get((lg, b), []),
                           key=lambda s: sort_item(s, lg))
            items = [it for it in items if alive(it)]
            if not items:
                continue
            label = "符号与数字" if b == DIGIT_BUCKET else b
            idx_lines.append(f"**{label}（{len(items)}）**：{'、'.join(items)}")
            idx_lines.append("")

    # 视图二
    up_groups: dict[str, list[str]] = {}
    for e in upstream:
        up_groups.setdefault(e.get("category", "未分类"), []).append(e["term"])
    loc_groups: dict[str, list[str]] = {}
    for r in local_recs:
        for s in (r["scenes"] or ["未归类"]):
            loc_groups.setdefault(s, []).append(r["term"])
    # 并入上游块的本地区分义项，同样按场景归组（义项穷尽）
    for term, info in paired.items():
        for s in (info["scenes"] or ["未归类"]):
            loc_groups.setdefault(s, []).append(term)

    idx_lines.append(f"## 视图二：归属分组（上游 {len(up_groups)} 分类 + 本地 {len(loc_groups)} 场景）")
    idx_lines.append("")
    idx_lines.append("上游分类按手法职能划分（取自上游 `category` 字段，上游自己的词典也按它分节），"
                     "本地场景按行业划分（取自本地 `scenes` 字段），两套轴互不映射。"
                     "词条正文下方不放这些信息，要按分类找词才来这里。")
    idx_lines.append("")
    for c, items in sorted(up_groups.items(), key=lambda kv: -len(kv[1])):
        idx_lines.append(f"## 上游·{c}（{len(items)}）")
        idx_lines.append("")
        idx_lines.append("、".join(sorted(items, key=lambda s: sort_key(s, "zh"))))
        idx_lines.append("")
    for c, items in sorted(loc_groups.items(), key=lambda kv: -len(kv[1])):
        idx_lines.append(f"## 本地·{c}（{len(items)}）")
        idx_lines.append("")
        idx_lines.append("、".join(sorted(items, key=lambda s: sort_key(s, "zh"))))
        idx_lines.append("")

    out_dir.mkdir(parents=True, exist_ok=True)
    lex_path = out_dir / "underground-slang-lexicon.md"
    idx_path = out_dir / "underground-slang-index.md"
    lex_path.write_text("\n".join(lexicon_lines) + "\n", encoding="utf-8")
    idx_path.write_text("\n".join(idx_lines) + "\n", encoding="utf-8")

    manifest = {
        "generated_at": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
        "upstream": {"repo": REPO, "ref": REF, "sha": up_sha},
        "policy": {
            "rendered_fields": ["term", "aka", "definition", "ambiguity", "confidence"],
            "dropped_fields": ["category", "mechanism", "example", "example_note",
                               "frequency", "lang", "region", "first_seen", "related"],
            "confidence_labels": CONF_CN,
            "same_term_rule": "上游同名词条已标注（高/中）→ 上游完全覆盖本地；"
                              "上游同名词条未标注 → 两方义项并列；本地置信度一律视为待核实",
            "language_split": "上游取 lang 字段；本地按词头字符判（汉字→中文，0-9/A-Z→英文）。"
                              "不同语种分节，不混排",
            "note": "产物面向咨询用途，体例以本地词典为准：### 词 → 又作 → 义项(1. 2.) → "
                    "研判置信度：X。上游字段只保留词/别名/释义/正常义/置信度，"
                    "其余（分类、在链条中、用例、频次、语种地区、收录版本、关联词表）"
                    "一律不渲染；分类与场景只在索引视图二汇总。上游含未标注档全部收录。"
                    "引号统一为中文引号。",
        },
        "counts": {
            "upstream_terms": len(upstream),
            "local_terms": len(local_recs),
            "merged_local_senses": len(paired),
            "overridden_local": len(overridden),
            "total": total,
            "aliases": sum(len(v) for v in alias_bucket.values()),
            "by_language": lang_count,
        },
        "files": {
            p.name: {"bytes": p.stat().st_size,
                     "sha256": hashlib.sha256(p.read_bytes()).hexdigest()}
            for p in (lex_path, idx_path)
        },
    }
    (out_dir / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return manifest


# ==========================================================================
# 报告
# ==========================================================================

def report() -> None:
    upstream, sha = fetch_upstream()
    conf_of = {e["term"]: (e.get("confidence") or "") for e in upstream}
    up_akas = {a: e["term"] for e in upstream for a in (e.get("aka") or [])}

    raw: list[dict] = []
    if LOCAL_DATA.exists():
        raw = json.loads(LOCAL_DATA.read_text(encoding="utf-8"))["entries"]

    st: dict[str, int] = {}
    for r in raw:
        status, _, _ = classify(r["term"], clean_body(r["body"]), conf_of, up_akas)
        st[status] = st.get(status, 0) + 1

    cats: dict[str, int] = {}
    for e in upstream:
        c = e.get("category", "未分类")
        cats[c] = cats.get(c, 0) + 1

    print(f"\n上游 {REPO} @ {sha[:10]}")
    print(f"  词条 {len(upstream)}　别名 {len(up_akas)}　分类 {len(cats)}")

    langs: dict[str, int] = {}
    for e in upstream:
        lg = e.get("lang") or "zh"
        langs[lg] = langs.get(lg, 0) + 1
    print("  语种：" + "　".join(
        f"{LANG_CN.get(lg, lg)} {n}"
        for lg, n in sorted(langs.items(),
                            key=lambda kv: (LANG_ORDER.index(kv[0]) if kv[0] in LANG_ORDER else 99))))

    conf: dict[str, int] = {}
    for e in upstream:
        k = e.get("confidence") or ""
        conf[k] = conf.get(k, 0) + 1
    print("  研判置信度：" + "　".join(
        f"{CONF_CN.get(k, k or '（未填）')} {n}" for k, n in
        sorted(conf.items(), key=lambda kv: (kv[0] != "high", -kv[1]))))
    print(f"  → 全部收录（含待核实 {sum(n for k, n in conf.items() if k not in LABELED_CONF)} 条）")

    label = {"alias": "纯别名跳转（进索引，不占正文）",
             "override": "上游同名已标注 → 本地整条丢弃",
             "both": "上游同名未标注 → 两方义项并列",
             "local-only": "上游没有 → 本地独立成条"}
    print(f"\n本地扩展 {len(raw)} 条记录：")
    for k in ("local-only", "both", "override", "alias"):
        if st.get(k):
            print(f"  {st[k]:>4}  {label[k]}")
    print(f"\n合并后正文 {len(upstream) + st.get('local-only', 0)} 条\n")
    for c, n in sorted(cats.items(), key=lambda kv: -kv[1]):
        print(f"  {n:>4}  {c}")
    print()


def check() -> int:
    _, sha = fetch_upstream(verbose=False)
    state = CACHE / "last-tree-sha"
    prev = state.read_text().strip() if state.exists() else ""
    if prev == sha:
        print("上游无变化")
        return 0
    print(f"上游有更新：{prev[:10] or '(首次)'} → {sha[:10]}")
    return 2


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="黑灰产行话词典同步 / 合并")
    sub = ap.add_subparsers(dest="cmd", required=True)

    sub.add_parser("report", help="打印上游分布")
    p_ex = sub.add_parser("extract-local", help="从既有词典抽取本地独有词条")
    p_ex.add_argument("--from", dest="src", required=True, type=Path)
    p_ex.add_argument("--index", required=True, type=Path)
    p_ex.add_argument("--out", default=LOCAL_DATA, type=Path)

    p_build = sub.add_parser("build", help="生成 skill/ 下两个 md")
    p_build.add_argument("--out", default=ROOT / "skill", type=Path)

    sub.add_parser("check", help="上游是否有更新")

    args = ap.parse_args(argv)
    if args.cmd == "report":
        report()
    elif args.cmd == "extract-local":
        info = extract_local(args.src, args.index, args.out)
        print(f"已抽取 {info['total']} 条 → {info['out']}")
        print(f"  本地独有 {info['local_only']}　上游已标注覆盖 {info['override']}　"
              f"上游未标注并列 {info['both']}　别名跳转 {info['alias']}")
    elif args.cmd == "build":
        m = build(args.out)
        c = m["counts"]
        print(f"已生成 → {args.out}")
        print(f"  上游 {c['upstream_terms']} + 本地独立 {c['local_terms']} "
              f"= {c['total']} 条（另有 {c['merged_local_senses']} 个同名未标注的词条义项并列，"
              f"{c['overridden_local']} 条本地被上游覆盖）")
        for name, meta in m["files"].items():
            print(f"  {name}: {meta['bytes']//1024}KB  sha256 {meta['sha256'][:12]}")
    else:
        return check()
    return 0


if __name__ == "__main__":
    sys.exit(main())
