#!/usr/bin/env python3
"""逐词合并的状态迁移走查。

合并规则只有一条判据——**置信度**：
  上游同名且已标注（高/中）→ 上游完全覆盖，本地整条丢弃
  上游同名且未标注（to-verify）→ 两方义项并列
  上游没有这个词条       → 本地独立成条（本地置信度一律视为「待核实」）
  正文是「→ 见 X」        → 纯别名跳转，只进索引

16 个场景，每个都实际跑一次 build()，再看产物是否符合预期。

    python3 tests/test_scenarios.py
    python3 tests/test_scenarios.py -v     # 打印每个场景的关键产物

零第三方依赖（拼音排序缺失时会退化，不影响断言）。
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tools"))
import merge_local as M  # noqa: E402

TMP = Path("/tmp/slang-scenario-test")

LOCAL_JIA = (
    "本地口径：指甲类团伙在放款前预收的保证金，金额随放款额度浮动，"
    "通常按 10% 到 30% 收取，且不计入还款本金；退保时以各种名目扣减，"
    "实际退还比例极低。这一层含义与上游的「转账通道」口径完全不同。"
)
LOCAL_BING = (
    "又称“乙本地别名”。本地口径：指专门为跑分车队提供身份证件的中间人，"
    "按人头结算，一人一证，证照不落地，出证周期三到七天。"
)


def ent(term, definition, *, id=None, aka=None, category="测试分类",
        confidence="high", mechanism="", example="", frequency="常见", lang="zh",
        ambiguity=""):
    return {
        "id": id or f"id-{term}",
        "term": term,
        "aka": aka or [],
        "category": category,
        "definition": definition,
        "mechanism": mechanism,
        "example": example,
        "ambiguity": ambiguity,
        "related": [],
        "confidence": confidence,
        "frequency": frequency,
        "first_seen": "v0.1",
        "lang": lang,
        "_src": "test.json",
    }


def run_build(upstream: list[dict], local: list[dict], tag: str) -> tuple[dict, dict]:
    """跑一次 build，返回 (块字典, 别名映射)。"""
    out = TMP / tag
    if out.exists():
        import shutil
        shutil.rmtree(out)
    local_file = TMP / f"{tag}-local.json"
    TMP.mkdir(parents=True, exist_ok=True)
    local_file.write_text(
        json.dumps({"entries": local}, ensure_ascii=False), encoding="utf-8")

    M.fetch_upstream = lambda verbose=False: (upstream, "testsha0abc123456")
    M.LOCAL_DATA = local_file
    M.build(out)

    lex = (out / "underground-slang-lexicon.md").read_text(encoding="utf-8")
    idx = (out / "underground-slang-index.md").read_text(encoding="utf-8")
    blocks = {m.group(1): m.group(2).strip()
              for m in re.finditer(r"^### (.+?)\s*$(.*?)(?=^### |\Z)", lex, re.M | re.S)}
    aliases = {}
    for line in idx.split("## 视图二")[0].splitlines():
        m = re.match(r"^\*\*(.+?)（\d+）\*\*：(.+)$", line)
        if not m:
            continue
        for item in m.group(2).split("、"):
            mm = re.match(r"^(.+?)（别名→(.+?)）$", item.strip())
            if mm:
                aliases[mm.group(1)] = mm.group(2)
    return blocks, aliases


def tail(blk: str) -> str:
    ls = [l.rstrip() for l in blk.split("\n")]
    while ls and (not ls[-1].strip() or ls[-1].startswith("## ")):
        ls.pop()
    return ls[-1] if ls else ""


def base_upstream() -> list[dict]:
    return [
        ent("甲词", "上游口径：指甲类团伙用于跨行转账的中转账户，按笔计费。", id="jia"),
        ent("乙词", "上游口径：指票据贴现业务的民间叫法。", id="yi",
            aka=["乙别名"], confidence="medium"),
        ent("戊词", "上游独有：指用于核验身份的第三方接口。", id="wu"),
        ent("庚词", "上游未标注口径：指代收代付通道的俗称。", id="geng",
            confidence="to-verify"),
    ]


def base_local() -> list[dict]:
    return [
        {"term": "甲词", "scenes": ["信贷欺诈场景"], "body": f"【释义】\n\n{LOCAL_JIA}"},
        {"term": "乙词", "scenes": ["跑分洗钱场景"], "body": f"【释义】\n\n{LOCAL_BING}"},
        {"term": "丙词", "scenes": ["营销欺诈场景"],
         "body": "【释义】\n\n本地独有：指用虚拟号批量注册后养熟再出售的账号池。"},
        {"term": "丁词", "scenes": [], "body": "【释义】\n\n→ 见 “甲词”"},
    ]


SCENARIOS: list[dict] = []


def scenario(name, expect, check):
    SCENARIOS.append({"name": name, "expect": expect, "check": check})


# ---- 1~2 上游增删（与本地无交集） ----
def s1():
    up = base_upstream() + [ent("己词", "上游新增：指批量注册用的一次性邮箱。")]
    b, _ = run_build(up, base_local(), "s1")
    ok = "己词" in b and "账号池" in b.get("丙词", "")
    return ok, (f"块数 {len(b)}，己词在，丙词未受影响="
                f"{'是' if '账号池' in b.get('丙词', '') else '否'}")


scenario("1 上游新增独有词汇", "新增块出现，本地不受影响", s1)


def s2():
    up = [e for e in base_upstream() if e["term"] != "戊词"]
    b, a = run_build(up, base_local(), "s2")
    ok = "戊词" not in b
    return ok, f"块数 {len(b)}，戊词{'还在' if '戊词' in b else '已消失'}"


scenario("2 上游删除独有词汇", "块一并清掉", s2)


# ---- 3 上游已标注 → 完全覆盖 ----
def s3():
    b, a = run_build(base_upstream(), base_local(), "s3")
    jia, yi = b.get("甲词", ""), b.get("乙词", "")
    ok = ("上游口径" in jia and "本地口径" not in jia
          and "上游口径" in yi and "跑分车队" not in yi
          and "乙本地别名" not in a          # 被覆盖的本地词条，别名也作废
          and "乙别名" in a)                 # 上游自己的别名照常登记
    return ok, (f"甲词本地口径已丢={'是' if '本地口径' not in jia else '否'}，"
                f"乙本地别名未登记={'是' if '乙本地别名' not in a else '否'}")


scenario("3 上游同名且已标注", "上游完全覆盖，本地正文与别名一并丢弃", s3)


# ---- 4 上游未标注 → 义项并列 ----
def s4():
    loc = base_local() + [{
        "term": "庚词", "scenes": ["跑分洗钱场景"],
        "body": "【释义】\n\n本地口径：指第四方支付机构替商户做的代收代付。"}]
    b, _ = run_build(base_upstream(), loc, "s4")
    blk = b.get("庚词", "")
    ok = ("代收代付通道的俗称" in blk and "本地口径" in blk
          and "1. 上游未标注口径" in blk and "2. 本地口径" in blk
          and tail(blk) == "研判置信度：待核实")
    return ok, f"两义项并列={'是' if ok else '否'}，尾行={tail(blk)}"


scenario("4 上游同名但未标注", "两方义项按 1. 2. 并列，尾行未标注", s4)


# ---- 5 上游未标注、本地无对应：照样发布 ----
def s5():
    b, _ = run_build(base_upstream(), base_local(), "s5")
    blk = b.get("庚词", "")
    ok = "代收代付通道的俗称" in blk and tail(blk) == "研判置信度：待核实"
    return ok, f"庚词独立成条={'是' if blk else '否'}，尾行={tail(blk)}"


scenario("5 上游未标注且本地没有", "独立成条，尾行标注未标注", s5)


# ---- 6~7 置信度升降，归属随之切换 ----
def s6():
    loc = base_local() + [{"term": "庚词", "scenes": [], "body": "【释义】\n\n本地口径：代收付。"}]
    low = base_upstream()
    b1, _ = run_build(low, loc, "s6a")
    high = [dict(e) for e in base_upstream()]
    for e in high:
        if e["term"] == "庚词":
            e["confidence"] = "high"
    b2, _ = run_build(high, loc, "s6b")
    ok = ("本地口径" in b1.get("庚词", "")
          and "本地口径" not in b2.get("庚词", "")
          and tail(b2.get("庚词", "")) == "研判置信度：高")
    return ok, (f"未标注时并列，改高后覆盖本地="
                f"{'是' if '本地口径' not in b2.get('庚词', '') else '否'}")


scenario("6 上游由未标注改为已标注", "并立变覆盖，本地口径消失", s6)


def s7():
    loc = base_local() + [{"term": "庚词", "scenes": [], "body": "【释义】\n\n本地口径：代收付。"}]
    high = [dict(e) for e in base_upstream()]
    for e in high:
        if e["term"] == "庚词":
            e["confidence"] = "high"
    b1, _ = run_build(high, loc, "s7a")
    b2, _ = run_build(base_upstream(), loc, "s7b")
    ok = ("本地口径" not in b1.get("庚词", "") and "本地口径" in b2.get("庚词", ""))
    return ok, f"降为未标注后本地口径回来={'是' if ok else '否'}"


scenario("7 上游由已标注改为未标注", "覆盖变并立，本地义项回来", s7)


# ---- 8 上游删掉同名词条：本地复位 ----
def s8():
    up = [e for e in base_upstream() if e["term"] != "甲词"]
    b, a = run_build(up, base_local(), "s8")
    jia = b.get("甲词", "")
    ok = "本地口径" in jia and tail(jia) == "研判置信度：待核实" and a.get("丁词") == "甲词"
    return ok, f"本地块回来，丁词别名→{a.get('丁词')}"


scenario("8 上游删除同名词条", "本地恢复独立成条，别名仍可解析", s8)


# ---- 9~10 本地侧 ----
def s9():
    b, a = run_build(base_upstream(), base_local(), "s9")
    ok = "丁词" not in b and a.get("丁词") == "甲词"
    return ok, f"丁词不占正文={'是' if '丁词' not in b else '否'}，别名→{a.get('丁词')}"


scenario("9 本地纯别名跳转块", "只进索引，不占正文块", s9)


def s10():
    b, _ = run_build(base_upstream(), base_local(), "s10")
    blk = b.get("丙词", "")
    ok = "〔" not in blk and "营销欺诈场景" not in blk and tail(blk) == "研判置信度：待核实"
    return ok, f"词条下方无分类行，尾行={tail(blk)}"


scenario("10 本地独有词条", "独立成条，词条下方不放分类", s10)


# ---- 11~13 体例归一 ----
def s11():
    loc = base_local() + [{
        "term": "辛词", "scenes": ["信贷欺诈场景"],
        "body": "【释义】\n\n1.信贷场景中指放款前预收的保证金。\n\n"
                "2.跑分场景中指通道抽取的佣金比例。"}]
    b, _ = run_build(base_upstream(), loc, "s11")
    blk = b.get("辛词", "")
    ok = ("1. 信贷场景" in blk and "2. 跑分场景" in blk
          and "①" not in blk and "②" not in blk)
    return ok, f"义项编号沿用本地 1./2.={'是' if ok else '否'}"


scenario("11 本地多义词自带编号", "按本地体例 `1.` `2.` 编号，不换成 ①②", s11)


def s12():
    loc = base_local() + [{
        "term": "壬词", "scenes": ["数据泄露场景"],
        "body": "【释义】\n\n1.游戏相关的用户数据。\n\n2.又称“壬别名”，指股民的个人信息数据。"}]
    b, a = run_build(base_upstream(), loc, "s12")
    blk = b.get("壬词", "")
    ok = ("又作" not in blk and "2. 又称“壬别名”" in blk and a.get("壬别名") == "壬词 义2")
    return ok, f"索引 壬别名→{a.get('壬别名')}"


scenario("12 异名嵌在义项内", "不上提为又作，但登记到该义项", s12)


def s13():
    loc = base_local() + [{
        "term": "癸词", "scenes": ["电信诈骗场景"],
        "body": "【释义】\n\n又称“癸别名”，指专门负责取款转移赃款的中间人。"}]
    b, a = run_build(base_upstream(), loc, "s13")
    blk = b.get("癸词", "")
    ok = "又作：癸别名" in blk and a.get("癸别名") == "癸词"
    return ok, f"又作已渲染={'是' if '又作：癸别名' in blk else '否'}，索引→{a.get('癸别名')}"


scenario("13 异名落在前言", "剥成词条级又作，索引直指词条", s13)


def s14():
    loc = base_local() + [{
        "term": "子词", "scenes": ["数据泄露场景"],
        "body": "【释义】\n\n指用于批量注册的手机号段。\n\n## A"}]
    b, _ = run_build(base_upstream(), loc, "s14")
    blk = b.get("子词", "")
    ok = ("## A" not in blk
          and not any(re.fullmatch(r"[A-Z]", l.strip()) for l in blk.split("\n"))
          and tail(blk) == "研判置信度：待核实")
    return ok, f"正文残留的分节标题已清除={'是' if ok else '否'}"


scenario("14 正文夹带分节标题", "读取侧洗掉，块仍以尾行收束", s14)


# ---- 15~16 字段裁剪与并立编号 ----
def s15():
    up = [ent("丑词", "上游口径：指以租代购的变体。",
              mechanism="处在放款与回收之间", example="丑词 分期",
              frequency="常见")]
    b, _ = run_build(up, [], "s15")
    blk = b.get("丑词", "")
    ok = not any(k in blk for k in ("在链条中", "用例", "频次", "语种", "无示例原因"))
    return ok, f"只有上游才有的字段已裁掉={'是' if ok else '否'}；块内：{blk!r}"


scenario("15 上游专有字段", "在链条中/用例/频次等一律不渲染", s15)


def s16():
    loc = [{"term": "寅词", "scenes": ["营销欺诈场景"],
            "body": "【释义】\n\n1.本地义一：刷量。\n\n2.本地义二：虚假评价。"}]
    up = [ent("寅词", "上游义一：指刷单。",
              confidence="to-verify")] + [
        ent("寅词二", "上游义二的对照词。", confidence="to-verify")]
    # 上游只有 definition 一条义项 + 本地两条 → 1. 2. 3. 连续
    b, _ = run_build(up, loc, "s16")
    blk = b.get("寅词", "")
    ok = all(x in blk for x in ("1. 上游义一", "2. 本地义一", "3. 本地义二"))
    return ok, f"义项编号连续={'是' if ok else '否'}"


scenario("16 并立时义项编号", "上游义项在前，本地义项接着编号不重号", s16)


# ---- 17 语种分节：不同语言不混排 ----
def s17():
    up = [ent("甲词", "中文词。", id="jia"),
          ent("escrow", "担保方。", id="esc", lang="en"),
          ent("japri", "印尼语的搭档。", id="jap", lang="id"),
          ent("GC", "他加禄语的保证交易。", id="gc", lang="tl")]
    loc = [{"term": "gm料", "scenes": ["数据泄露场景"],
            "body": "【释义】\n\n游戏数据。"}]
    out = TMP / "s17"
    b, _ = run_build(up, loc, "s17")
    lex = (out / "underground-slang-lexicon.md").read_text(encoding="utf-8")
    secs = [m.group(1) for m in re.finditer(r"(?m)^## (.+)$", lex)]
    ok = all(x in secs for x in ("中文（1 条）", "英文（2 条）", "印尼语（1 条）", "他加禄语（1 条）"))
    # 英文节里不能混进中文词条
    en = re.split(r"(?m)^## ", lex.split("## 英文", 1)[1])[0]
    ok = ok and "### 甲词" not in en and "### gm料" in en and "### escrow" in en
    return ok, "分节：" + "、".join(s for s in secs if not re.fullmatch(r"[A-Z0-9]", s))


scenario("17 语种分节", "中文/英文/印尼语/他加禄语各自成节，本地 0-9A-Z 开头算英文", s17)


# ---- 18 引号统一为中文引号 ----
def s18():
    up = [ent("甲词", "上游口径：「甲」指跨行转账的中转账户。", id="jia",
              ambiguity="「甲」在会计行业是科目代码。")]
    b, _ = run_build(up, [], "s18")
    blk = b.get("甲词", "")
    ok = "「" not in blk and "」" not in blk and "“甲”" in blk
    return ok, f"引号已归一={'是' if ok else '否'}"


scenario("18 引号统一", "上游散文里的「」渲染为“”", s18)


# ---- 19 序号格式：`1.` + 空格 ----
def s19():
    up = [ent("甲词", "第一义。", id="jia", ambiguity="第二义。")]
    b, _ = run_build(up, [], "s19")
    blk = b.get("甲词", "")
    ok = "1. 第一义。" in blk and "2. 第二义。" in blk
    return ok, f"序号形如 1. 后带空格={'是' if ok else '否'}"


scenario("19 序号格式", "`1.` + 半角空格", s19)


# ---- 20 上游块也不带分类行 ----
def s20():
    up = [ent("甲词", "上游口径。", id="jia", category="资金通道与洗钱")]
    b, _ = run_build(up, [], "s20")
    blk = b.get("甲词", "")
    ok = "〔" not in blk and "资金通道与洗钱" not in blk
    return ok, f"词条下方无分类行={'是' if ok else '否'}"


scenario("20 分类不渲染", "上游 category 只进索引视图二，不上词条正文", s20)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("-v", "--verbose", action="store_true")
    args = ap.parse_args(argv)

    width = max(len(s["name"]) for s in SCENARIOS) + 2
    print(f"\n{'场景'.ljust(width)}判定   实际表现")
    print("=" * 96)
    fail = 0
    for s in SCENARIOS:
        try:
            ok, detail = s["check"]()
        except Exception as exc:  # 崩溃也算不通过
            ok, detail = False, f"异常 {type(exc).__name__}: {exc}"
        mark = "通过" if ok else "不通过"
        if not ok:
            fail += 1
        print(f"{s['name'].ljust(width)}{mark}   {detail}")
        if args.verbose:
            print(f"{'':<{width}}预期：{s['expect']}")
    print("=" * 96)
    print(f"共 {len(SCENARIOS)} 个场景，不通过 {fail} 个\n")
    return 0 if fail == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
