#!/usr/bin/env python3
"""从 data/entries/*.json 生成人读版词典（dict/黑话词典.md）与扁平 CSV。

用法：python3 tools/build_dict.py
零第三方依赖。
"""
import json, csv, os, re, sys
from collections import OrderedDict, Counter

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ENTRY_DIR = os.path.join(ROOT, 'data', 'entries')
SCHEMA = json.load(open(os.path.join(ROOT, 'data', 'schema.json'), encoding='utf-8'))
CATS = SCHEMA['categories']
CONF_LABEL = {'high': '高', 'medium': '中', 'to-verify': '待核实'}
LANGS = SCHEMA.get('languages', {})
LANG_ORDER = ['zh', 'en', 'id', 'vi', 'tl', 'ru', 'th', 'hi']
LANG_SHORT = {'zh': '中文', 'en': '英文', 'id': '印尼语', 'vi': '越南语',
              'tl': '他加禄语', 'ru': '俄语', 'th': '泰语', 'hi': '印地语'}

def load():
    entries = []
    for fn in sorted(os.listdir(ENTRY_DIR)):
        if not fn.endswith('.json'):
            continue
        data = json.load(open(os.path.join(ENTRY_DIR, fn), encoding='utf-8'))
        if not isinstance(data, list):
            sys.exit(f'{fn}: 顶层必须是数组')
        for e in data:
            e['_file'] = fn
            entries.append(e)
    return entries

def validate(entries):
    errs = []
    by_id_all = {e['id']: e for e in entries}
    ids = Counter(e.get('id') for e in entries)
    for i, n in ids.items():
        if n > 1:
            errs.append(f'id 重复: {i} ×{n}')
    known = set(ids)
    for e in entries:
        for f in ('id', 'term', 'category', 'definition', 'confidence'):
            if not e.get(f):
                errs.append(f"{e.get('id') or e.get('term')}: 缺字段 {f}")
        if e.get('category') not in CATS:
            errs.append(f"{e.get('id')}: 未知分类 {e.get('category')}")
        if e.get('lang') and e['lang'] not in LANGS:
            errs.append(f"{e.get('id')}: 未知语种 {e['lang']}")
        if e.get('frequency') and e['frequency'] not in ('常见', '较常见', '少见'):
            errs.append(f"{e.get('id')}: frequency 取值非法 {e['frequency']}")
        if 'stats' in e:
            errs.append(f"{e.get('id')}: 不得保留 stats 字段——语料规模与内部标注不对外")
        if e.get('confidence') not in CONF_LABEL:
            errs.append(f"{e.get('id')}: confidence 取值非法 {e.get('confidence')}")
        for r in e.get('related') or []:
            if r not in known:
                errs.append(f"{e.get('id')}: related 指向不存在的词条 {r}")

    # 以下三项是增量维护中容易累积的缺陷，纳入构建校验以防复发
    def _norm(x):
        return re.sub(r'[（(].*?[)）]', '', x).strip()

    def _variants(e):
        out = []
        for a in e.get('aka') or []:
            for p in re.split(r'[=/、]', _norm(a)):
                p = p.strip()
                if p:
                    out.append(p)
        return out

    term2id = {}
    for e in entries:
        t = _norm(e['term'])
        if t in term2id:
            errs.append(f"term 重复：{t}（{term2id[t]} 与 {e['id']}）")
        term2id[t] = e['id']

    # ① 别名唯一性 + ② 别名不得与另一词条的 term 同形
    aka_owner = {}
    for e in entries:
        for v in _variants(e):
            if v in term2id and term2id[v] != e['id']:
                errs.append(f"{e['id']}: 别名「{v}」与词条 {term2id[v]} 的 term 同形——应改为互相关联")
            if v in aka_owner and aka_owner[v] != e['id']:
                errs.append(f"别名「{v}」同时挂在 {aka_owner[v]} 与 {e['id']} 上——须指定唯一归属")
            aka_owner[v] = e['id']
        if '=' in ''.join(e.get('aka') or []):
            errs.append(f"{e['id']}: 别名含“=”映射写法——别名只放可检索的变体形式，对照关系写进 mechanism")

    # ③ 关联对称性
    for e in entries:
        for r in e.get('related') or []:
            if r in known and e['id'] not in (by_id_all[r].get('related') or []):
                errs.append(f"关联不对称：{e['id']} → {r} 缺反向")
    return errs

def render(entries):
    by_id = {e['id']: e for e in entries}
    groups = OrderedDict((c, []) for c in CATS)
    for e in entries:
        groups[e['category']].append(e)
    FREQ_RANK = {'常见': 0, '较常见': 1, '少见': 2}
    for c in groups:
        groups[c].sort(key=lambda x: (FREQ_RANK.get(x.get('frequency'), 3), x['term']))

    L = []
    L.append('# 互联网黑灰产黑话词典')
    L.append('')
    lang_count = Counter(e.get('lang', 'zh') for e in entries)
    lang_line = '、'.join(f"{LANG_SHORT.get(lg, lg)} {lang_count[lg]}"
                          for lg in LANG_ORDER if lang_count.get(lg))
    L.append(f'共 {len(entries)} 条词条，{sum(1 for c in groups if groups[c])} 个分类。按语种：{lang_line}。')
    L.append('')
    L.append('本文件由 `tools/build_dict.py` 生成，请勿直接编辑；词条源文件在 `data/entries/`。')
    L.append('')
    L.append('> 本词典用于风险识别、内容审核与安全研究。词条只描述表达方式与手法机制，不提供实施步骤。示例均来自公开渠道监测语料并已脱敏。')
    L.append('')
    L.append('## 目录')
    L.append('')
    for c in CATS:
        if groups[c]:
            L.append(f'- [{c}](#{c.replace(" ", "-")})（{len(groups[c])} 条）')
    L.append('')
    # 非中文词条索引
    nz = [e for e in entries if e.get('lang', 'zh') != 'zh']
    if nz:
        L.append('## 非中文词条索引')
        L.append('')
        L.append('黑话的产业链结构与语种无关，因此非中文词条按同一套分类收录，这里另给一份按语种的索引。')
        L.append('')
        for lg in LANG_ORDER:
            items = [e for e in nz if e.get('lang') == lg]
            if not items:
                continue
            items.sort(key=lambda x: x['term'].lower())
            L.append(f"**{LANG_SHORT.get(lg, lg)}**（{len(items)} 条）：" +
                     '、'.join(f"[{e['term']}](#{e['term'].lower().replace(' ', '-')})" for e in items))
            L.append('')
    # 构词法词族索引
    FAMILIES = [
        ('料', '数据与凭证的统称，按来源、用途与新鲜度派生',
         ['liao', 'yuliao', 'maoliao', 'neimuliao', 'daikuanshuju', 'liaoshang', 'baozhenbubaolou']),
        ('鱼', '钓鱼产业的词根，从手法到产物到服务全覆盖',
         ['diaoyu', 'yuliao', 'daidiao', 'jiechi']),
        ('码', '收款码与券码两条线，按资质、时效与防护派生',
         ['juhema', 'mashang', 'huoma', 'shangma', 'bianma', 'majiaodan', 'kuozhanma', 'qiujiehuakuai']),
        ('号', '账号商品的分级体系，按权限、历史与注册方式派生',
         ['chengpinhao', 'baihao', 'laohuohao', 'xieyihao', 'zhibohao', 'yuanshengxiaodian',
          'lengmenhao', 'yanzhenghao', 'chenmohao']),
        ('卡', '账户与卡资源，按等级、归属地与风控状态派生',
         ['yileika', 'shouka', 'zhanghutaojian', 'gangka', 'shiminka', 'jiesuanka', 'zhuanka',
          'kashang', 'dongka']),
        ('车', '把机会、通道与合作关系都隐喻为"车"',
         ['haoche', 'shangche', 'chedui', 'laoche', 'zhaoche', 'fache', 'xiaoche', 'daiche']),
        ('粉', '流量线索的生产、分级与承接',
         ['gufen', 'fenzhi', 'dafen', 'jiefen', 'baofen', 'followers', 'shuijun']),
        ('盘', '业务来源与骗局形态',
         ['pankou', 'zhipan', 'zijinpan', 'shazhupan']),
        ('户', '账户按主体性质区分，直接对应风控强度',
         ['gonghu', 'sihu', 'heihu', 'jiabai', 'jinjian']),
        ('单', '任务与订单的流转环节',
         ['baodan', 'shangdan', 'chadan', 'zuodan', 'budan', 'huidan', 'zuodanqun']),
        ('商', '供给侧的角色分工',
         ['kashang', 'mashang', 'liaoshang', 'ushang']),
        ('代X', '把每个需要真人出面的动作都外包出去',
         ['daix', 'daidiao', 'dailshiming', 'daifu', 'daihuan', 'daiweiquan']),
        ('保X', '以"保+风险项"逐项列出愿意承担的后果',
         ['baox', 'shangya', 'mailei', 'zoudanbao', 'garansi', 'mianche']),
        ('无视X', '逐条点名被绕过的风控措施',
         ['wushi', 'feigui', 'poe', 'jiabai', 'difangyinhang']),
        ('群', '社群既是组织形式，也是被仿冒的"品牌"',
         ['gongqun', 'jiagongqun', 'zuodanqun', 'shangya', 'hutui', 'ruqunyanzheng']),
        ('水', '同字两义：名词指可薅额度，动词短语指平台放宽；去水印里又指水印',
         ['xiaochengxushui', 'fangshui', 'qushuiyin', 'liushui', 'shualiushui', 'shuifang']),
        ('口', '"口"指通道与入口，派生出多条互不相干的线',
         ['kouzi', 'pankou', 'jiekou', 'rukouzhiyin', 'dangkou', 'shoujikou', 'kouling']),
        ('反X', '由一项已知信息倒查另一项，"A反B"是可售商品',
         ['fancha', 'mohu', 'gongwangbuqi', 'mingxia', 'fancuishou']),
    ]
    rows = []
    for stem, note, ids in FAMILIES:
        terms = [by_id[i]['term'] for i in ids if i in by_id]
        if len(terms) >= 3:
            rows.append((stem, note, terms))
    if rows:
        L.append('## 构词法词族')
        L.append('')
        L.append('黑话的能产性集中在少数语素上：掌握词根比记单词更有用，因为新词会沿着同样的构词法出现。下表把已收词条按词根归类。')
        L.append('')
        L.append('| 词根 | 构词逻辑 | 已收词条 |')
        L.append('| --- | --- | --- |')
        for stem, note, terms in rows:
            L.append(f"| **{stem}** | {note} | {'、'.join(terms)} |")
        L.append('')
    for c in CATS:
        if not groups[c]:
            continue
        L.append(f'## {c}')
        L.append('')
        for e in groups[c]:
            L.append(f"### {e['term']}")
            L.append('')
            tags = []
            if e.get('lang', 'zh') != 'zh':
                tags.append(f"`{LANG_SHORT.get(e['lang'], e['lang'])}`")
            if e.get('region'):
                tags.append(f"`{e['region']}`")
            if tags:
                L.append(' '.join(tags))
                L.append('')
            if e.get('aka'):
                L.append(f"**又作**：{'、'.join(e['aka'])}")
                L.append('')
            L.append(f"**释义**：{e['definition']}")
            L.append('')
            if e.get('mechanism'):
                L.append(f"**在链条中的作用**：{e['mechanism']}")
                L.append('')
            if e.get('example'):
                L.append(f"**示例**（脱敏）：`{e['example']}`")
                L.append('')
            elif e.get('example_note'):
                L.append(f"**无示例说明**：{e['example_note']}")
                L.append('')
            if e.get('ambiguity'):
                L.append(f"**歧义与误报**：{e['ambiguity']}")
                L.append('')
            if e.get('related'):
                names = [by_id[r]['term'] for r in e['related'] if r in by_id]
                if len(names) <= 14:
                    L.append(f"**关联**：{'、'.join(names)}")
                else:
                    L.append(f"**关联**（{len(names)} 个）：{'、'.join(names[:14])} 等")
                L.append('')
            meta = [f"研判置信度：{CONF_LABEL[e['confidence']]}"]
            if e.get('frequency'):
                meta.append(f"出现频次：{e['frequency']}")
            if e.get('first_seen'):
                meta.append(f"收录版本：{e['first_seen']}")
            L.append('<sub>' + ' ｜ '.join(meta) + '</sub>')
            L.append('')
    return '\n'.join(L) + '\n'

def sync_readme(entries):
    """把 README 里易漂移的数字与实际数据同步，避免每轮更新手改出错。"""
    path = os.path.join(ROOT, 'README.md')
    if not os.path.exists(path):
        return
    with open(path, encoding='utf-8') as f:
        s = f.read()
    orig = s

    edges = set()
    for e in entries:
        for r in e.get('related') or []:
            edges.add(tuple(sorted((e['id'], r))))
    n_entry = len(entries)
    n_cat = len({e['category'] for e in entries})
    n_lang = len({e.get('lang', 'zh') for e in entries})
    n_edge = len(edges)
    n_amb = sum(1 for e in entries if e.get('ambiguity'))
    lang_count = Counter(e.get('lang', 'zh') for e in entries)

    # 徽章
    for label, val in (('词条', n_entry), ('分类', n_cat), ('语种', n_lang), ('关联边', n_edge)):
        s = re.sub(r'(badge/' + label + r'-)\d+(-)', lambda m: m.group(1) + str(val) + m.group(2), s)

    # 歧义覆盖
    s = re.sub(r'\*\*最关键的差异是"误报说明"。\*\* \d+ 条词条中 \d+ 条标注了歧义（\d+%）',
               '**最关键的差异是"误报说明"。** %d 条词条中 %d 条标注了歧义（%d%%）'
               % (n_entry, n_amb, round(100 * n_amb / n_entry)), s)

    # 按语种一行
    names = {'zh': '中文', 'en': '英文', 'id': '印尼语', 'tl': '他加禄语', 'vi': '越南语',
             'ru': '俄语', 'th': '泰语', 'hi': '印地语'}
    seq = [lg for lg in ['zh', 'en', 'id', 'tl', 'vi', 'ru', 'th', 'hi'] if lang_count.get(lg)]
    s = re.sub(r'(（\[非中文词条索引\]\(dict/黑话词典\.md#非中文词条索引\)）：)[^。]+。',
               lambda m: m.group(1) + ' ｜ '.join('%s %d' % (names[lg], lang_count[lg]) for lg in seq) + '。', s)

    if s != orig:
        with open(path, 'w', encoding='utf-8') as f:
            f.write(s)
        print('已同步 README 中的统计数字')


def main():
    entries = load()
    errs = validate(entries)
    if errs:
        print('校验未通过：')
        for e in errs:
            print('  -', e)
        sys.exit(1)
    os.makedirs(os.path.join(ROOT, 'dict'), exist_ok=True)
    md = os.path.join(ROOT, 'dict', '黑话词典.md')
    with open(md, 'w', encoding='utf-8') as f:
        f.write(render(entries))
    csvp = os.path.join(ROOT, 'dict', 'terms.csv')
    with open(csvp, 'w', encoding='utf-8-sig', newline='') as f:
        w = csv.writer(f)
        w.writerow(['id', 'term', 'aka', 'lang', 'region', 'category', 'definition', 'ambiguity', 'related', 'confidence', 'frequency'])
        for e in sorted(entries, key=lambda x: (CATS.index(x['category']), x['id'])):
            w.writerow([e['id'], e['term'], '|'.join(e.get('aka') or []), e.get('lang', 'zh'),
                        e.get('region', ''), e['category'], e['definition'],
                        e.get('ambiguity', ''), '|'.join(e.get('related') or []), e['confidence'],
                        e.get('frequency', '')])
    sync_readme(entries)
    print(f'校验通过：{len(entries)} 条词条')
    print('已生成', os.path.relpath(md, ROOT), '与', os.path.relpath(csvp, ROOT))

if __name__ == '__main__':
    main()
