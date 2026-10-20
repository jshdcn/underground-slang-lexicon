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
# 置信度口径：README.zh-CN.md「词条质量口径」、README.md「Quality standards」与 data/schema.json 用的是同一套说法，改一处要四处一起改
CONF_DESC = {'high': '词义有真实用例佐证，并经人工复核确认',
             'medium': '词义基本确定，但用法较多或边界不清，使用时要结合语境',
             'to-verify': '证据不足或尚未逐条复核'}
REL_SHOW = 14     # 关联超过这个数只列前若干个
LANGS = SCHEMA.get('languages', {})
LANG_ORDER = ['zh', 'en', 'pt', 'id', 'vi', 'tl', 'ru', 'th', 'hi']
LANG_SHORT = {'zh': '中文', 'en': '英文', 'pt': '葡萄牙语', 'id': '印尼语', 'vi': '越南语',
              'tl': '他加禄语', 'ru': '俄语', 'th': '泰语', 'hi': '印地语'}


# ============================ 脱敏门禁 ============================
# 口径：词表留，引文脱。平台名、绰号、字段名保留在 term / aka（那是要被匹配的字符串），
# 散文字段里按类型占位。实现上：扫描前先把该词条自己的 term 与 aka 从文本里摘掉，
# 于是「平台代称」的「飞机」、「闲鱼洗货」的平台名、「查询结果字段模板」的「命中N条」
# 都自然豁免，而别处冒出来的同类字符串仍会被拦住。
#
# 两级规则：
#   LEAK  —— 数据本体，任何对外字段都不许有
#   QUOTE —— 只管 example（从语料截来的引文），释义里正常提及这些概念是释义的职责
PROSE_FIELDS = ('definition', 'mechanism', 'ambiguity', 'example', 'example_note')

LEAK = [
    (r'(?<!\d)1[3-9]\d{9}(?!\d)', '疑似真实手机号'),
    (r'(?<!\d)\d{15,18}[\dXx]?(?!\d)', '疑似真实身份证号'),
    (r'(?<![\d-])\d{3}[-\s]\d{4}[-\s]\d{4}(?![\d-])', '疑似分段写法的电话号'),
    (r'\bT[A-Za-z0-9]{25,}|0x[0-9a-fA-F]{20,}', '疑似加密货币钱包地址'),
    (r'\b[0-9a-f]{12,}\b', '疑似数据批次哈希/订单号'),
    (r'命中约?\s*[\d,]+\s*条|\d+\s*天[^，。]{0,10}命中', '夹带语料命中量——语料规模不对外'),
    (r'习近平|李克强', '涉及真实公众人物姓名'),
    (r'https?://|www\.', '未脱敏的链接'),
    (r'@[A-Za-z0-9_]{4,}', '疑似账号 handle'),
]

QUOTE = [
    (r'\.(?:xlsx?|csv|txt|zip|rar|7z|db|sql)\b', '夹带数据文件名'),
    (r'\b[A-Za-z]{1,6}\d{5,}\b', '疑似卖家账号/联系 ID'),
    (r'公\s*众\s*号|订阅号', '夹带公众号名'),
    (r'有限公司|[\u4e00-\u9fff]{2,4}工作室', '夹带商号/工作室名', False),
    (r'(?:热线|客服|专线|座机)\s*[:：]?\s*\d{4,}', '夹带客服/热线号码'),
    (r'(?:快递|收货|收件|家庭)地址\s*[:：]\s*\S', '疑似真实收寄地址'),
    (r'加\s*[vV微]|[+＋➕]\s*[vV]|私聊\S|私我\S', '联系方式未脱敏'),
    # 具名平台与机构：留在 term/aka 可以，散文里要占位
    (r'京东|淘宝|天猫|拼多多|美团|饿了么|抖音|快手|小红书|支付宝|微信|闲鱼|得物|唯品会|携程|滴滴|'
     r'亚马逊|虾皮|沃尔玛|微博|推特|脸书|抖店|千牛', '散文里出现具名平台，应按类型占位'),
    # 具名银行：用有界的名称列表，不用 [\u4e00-\u9fff]{2,6}银行 这种开放式写法。
    # 开放式会把「银行」前面任意几个字都当成行名——「支持银行」「渠道银行」「公安银行」
    # 「出售银行卡」全是假阳性，而且一旦用到脱敏流水线上会把正常句子改坏。
    (r'(?:工商|农业|建设|交通|招商|兴业|浦发|民生|光大|中信|华夏|广发|平安|邮储|浙商|恒丰|渤海|中国|招行|农行|工行|建行|中行)银行|四大行|六大行'
     r"|(?:北京|上海|天津|重庆|哈尔滨|长春|沈阳|石家庄|太原|呼和浩特|济南|青岛|南京|杭州|宁波|合肥|福州|厦门|南昌|郑州|武汉|长沙|广州|深圳|南宁|海口|成都|贵阳|昆明|拉萨|西安|兰州|西宁|银川|乌鲁木齐|苏州|无锡|温州|佛山|东莞|盛京|江苏|浙江|广东|山东|河南|四川|湖北|湖南|河北|陕西|甘肃|吉林|辽宁|黑龙江|安徽|江西|福建|云南|贵州|广西)银行"
     r'|农村商业银行|城市商业银行',
     '散文里出现具名银行，应占位为〔某银行〕', False),
    # 行名简称：只在紧跟银行类或支付通道类语素时才算行名。
    # 「工商一码」「农业破额」「建设正扫码」在通道语境里就是行名；
    # 「工商层面」「农业语境」「招商与选品」是常用词，不能动。
    (r'(?:工商|农业|建设|交通|招商|兴业|浦发|民生|光大|中信|华夏|广发|平安|邮储|浙商|恒丰|渤海)'
     r'(?=[行银]|套卡|白户|对私|对公|额度|放款|流水|征信|信用卡|储蓄卡'
     r'|破额|一码|正扫|反扫|扫码|聚合|进算|商家码)',
     '散文里出现具名银行简称，应占位为〔某银行〕'),
    # 「Verified by Visa」是 3-D Secure 的协议正式名称，属技术术语不是品牌露出，放行。
    (r'银联|万事达|Mastercard|(?<!by )Visa(?![a-z])|运通|JCB',
     '散文里出现具名卡组织，应占位为〔某卡组织〕'),
]
# 三元组 (正则, 说明, 是否参与去空格探测)。
# 去空格探测是为了识别「公 众 号」这类拆字绕过，但对开放式的中文量词规则
# （[\u4e00-\u9fff]{2,6}银行）会把相邻的两个词拼成一个，造出假阳性
# ——「农业破额 银行聚合」拼成「农业破额银行」、「助贷 消金申请 银行申请」拼成
# 「消金申请银行」，都不是行名。这类规则只扫原文。
LEAK = [(re.compile(r[0]), r[1], r[2] if len(r) > 2 else True) for r in LEAK]
QUOTE = [(re.compile(r[0]), r[1], r[2] if len(r) > 2 else True) for r in QUOTE]
PLACEHOLDER = re.compile(r'〔[^〕]{1,14}〕|【[^】]{1,14}】')
DESPACE = re.compile(r'(?<=[\u4e00-\u9fff])[ \t]+(?=[\u4e00-\u9fff])')


NUM = re.compile(r'\d+')


def _headwords(e):
    out = []
    for sv in [e.get('term', '')] + (e.get('aka') or []):
        for pt in re.split(r'[=/、]', re.sub(r'[（(].*?[)）]', '', sv)):
            pt = pt.strip()
            if len(pt) >= 2:
                out.append(pt)
    return out


def _vocab(entries):
    """词典收录的全部词形。带数字或 N 占位的（aka 里的「命中N条」「车轨5+1」）
    额外转成正则，用来匹配文本里的具体数字。"""
    lit, tpl = set(), []
    for e in entries:
        for h in _headwords(e):
            lit.add(h)
            if re.search(r'[\dN]', h):
                pat = ''.join(r'\s*\d+\s*' if c in '0123456789N' else re.escape(c) for c in h)
                tpl.append(re.compile(pat))
    return sorted(lit, key=len, reverse=True), tpl


def _vocab_spans(text, lit, tpl):
    """词形在文本里占据的区间。命中落在区间内即为「词表留」，不算泄露。"""
    spans = []
    for o in lit:
        i = text.find(o)
        while i >= 0:
            spans.append((i, i + len(o)))
            i = text.find(o, i + 1)
    for rx in tpl:
        spans += [m.span() for m in rx.finditer(text)]
    return spans


def check_masking(entries):
    """返回 [(id, 字段, 规则说明, 命中片段)]

    豁免口径就是「词表留」：**词典里任何词条的 term / aka** 都是术语，不是泄露——
    「平台代称」的「飞机」、「查询结果字段模板」的「命中N条」、「地方银行」
    本身就是收录的词头，散文里引用它们是词典该做的事。

    按**区间**豁免而不是删字符串：命中完全落在某个词形区间内才豁免。
    删字符串会把判断所需的上下文一并删掉——「工商一码」里的「一码」是词头，
    删掉后只剩「工商」，就看不出这是行名了。
    """
    lit, tpl = _vocab(entries)
    hits = []
    for e in entries:
        for f in PROSE_FIELDS:
            v = e.get(f) or ''
            if not v:
                continue
            probe = PLACEHOLDER.sub('　', v)        # 占位符换成全角空格，保留分隔但不参与匹配
            spans = _vocab_spans(probe, lit, tpl)
            rules = LEAK + (QUOTE if f == 'example' else [])
            found = None
            for cand, despaced in ((probe, False), (DESPACE.sub('', probe), True)):
                sp = spans if not despaced else _vocab_spans(cand, lit, tpl)
                for pat, w, ds_ok in rules:
                    if despaced and not ds_ok:
                        continue
                    for m in pat.finditer(cand):
                        # ① 命中整个落在词形区间内 → 词表留
                        if any(a <= m.start() and m.end() <= b for a, b in sp):
                            continue
                        # ② 词形正好是命中的尾部 → 贪婪正则多吃了前缀，仍属词表留
                        #    （「所有地方银行」里「地方银行」是收录词头，「所有」是多吃的）
                        if any(b == m.end() and a > m.start() for a, b in sp):
                            continue
                        found = (w, m.group(0)[:30])
                        break
                    if found:
                        break
                if found:
                    break
            if found:
                hits.append((e.get('id'), f, found[0], found[1]))
    return hits


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

DEF_MIN = 15          # 释义字数下限


def validate(entries, warns=None):
    errs = []
    warns = warns if warns is not None else []
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
        rel = e.get('related') or []
        for r in rel:
            if r in known and e['id'] not in (by_id_all[r].get('related') or []):
                errs.append(f"关联不对称：{e['id']} → {r} 缺反向")
        if e['id'] in rel:
            errs.append(f"{e['id']}: related 含自身")
        if len(rel) != len(set(rel)):
            errs.append(f"{e['id']}: related 有重复项")

    # ④ 脱敏门禁 —— 这是本项目的红线，放行一次就等于把清理过的泄露又带回来
    for eid, f, why, frag in check_masking(entries):
        errs.append(f"{eid}: 字段 {f} 未脱敏（{why}）——命中「{frag}」")

    # ⑤ 质量下限
    for e in entries:
        d = (e.get('definition') or '').strip()
        if 0 < len(d) < DEF_MIN:
            # 提示而非拦截：15 是现有词条的实测下限，不是设计约束。
            # 「每个手机号配一个独立接码链接」14 字已经说清了，不该为了凑字数改写。
            warns.append(f"释义偏短（{len(d)} 字）|{e['id']} {e.get('term')}")
        if not (e.get('related') or []):
            warns.append(f"没有任何关联（孤点）|{e['id']} {e.get('term')}")
        if not (e.get('mechanism') or '').strip():
            warns.append(f"缺 mechanism|{e['id']} {e.get('term')}")
        if not (e.get('ambiguity') or '').strip():
            warns.append(f"缺 ambiguity|{e['id']} {e.get('term')}")
    return errs

# 构词法词族：词根 + 构词逻辑 + 已收词条 id。render 与 tools/build_viz.py 共用，
# 因此放在模块层；修改词族时两边自动同步。
FAMILIES = [
    ('料', '数据与凭证的统称，按来源、用途与新鲜度派生',
     ['liao', 'yuliao', 'maoliao', 'neimuliao', 'daikuanshuju', 'liaoshang', 'baozhenbubaolou',
      'duankouliao', 'gaojingzhiliao', 'pingguoimzhuanyongliao', 'wangzhaxinliao',
      'liaoziduiwei', 'rongdan']),
    ('鱼', '钓鱼产业的词根，从手法到产物到服务全覆盖',
     ['diaoyu', 'yuliao', 'daidiao', 'jiechi']),
    ('码', '收款码、券码与验证码三条线，按资质、时效与防护派生',
     ['juhema', 'mashang', 'huoma', 'shangma', 'bianma', 'majiaodan', 'kuozhanma',
      'qiujiehuakuai', 'nengliangma', 'qiyema', 'gerenma', '106tuozhanma', 'jiama',
      'yima', 'shouma', 'sanwangshouma', 'quanma', 'huima', 'huimalv', 'laimalv', 'laimalv2',
      'gaohuima', 'fama', 'duijiema', 'yuyinma', 'zhucema']),
    ('号', '账号与号码资源的分级体系，按权限、历史与注册方式派生',
     ['chengpinhao', 'baihao', 'laohuohao', 'xieyihao', 'zhibohao', 'yuanshengxiaodian',
      'lengmenhao', 'yanzhenghao', 'chenmohao', 'sanrenhao', 'smhao', 'naiyonghao',
      'jingyanghao', 'huiliulaohao', 'shoucihao', 'haiwaihao', 'beianhao', 'xinghao',
      'lengremenzhanghao', 'sihao', 'bufenghao', 'yuyinhao', 'haoduan',
      'shanghao', 'dinghao', 'diaohao', 'qihao', 'shaihao', 'heihao']),
    ('卡', '账户与卡资源，按等级、归属地与风控状态派生',
     ['yileika', 'shouka', 'zhanghutaojian', 'gangka', 'shiminka', 'jiesuanka', 'zhuanka',
      'kashang', 'dongka', 'shika', 'xuka', 'shikaxuka', 'ercika', 'cunzhenka', 'guitaika',
      'yuyinka', 'yaliaoka', 'bangka', 'kawang', 'yunzhuanka', 'kahuiu', 'kakou',
      'jingwaikakou', 'shangka', 'fangka', 'xiaka', 'jiaka', 'liuka']),
    ('车', '把机会、通道与合作关系都隐喻为"车"',
     ['haoche', 'shangche', 'chedui', 'laoche', 'zhaoche', 'fache', 'xiaoche', 'daiche',
      'mache', 'kache', 'makache', 'zhaomache', 'xianjinche', 'naxianche', 'daxiaoche',
      'uche']),
    ('粉', '流量线索的生产、分级与承接',
     ['gufen', 'fenzhi', 'dafen', 'jiefen', 'baofen', 'followers', 'shuijun',
      'shangfen', 'zhangfen', 'feijishangfen', 'qianfen', 'wanfen', 'daiqianfen']),
    ('盘', '业务来源与骗局形态',
     ['pankou', 'zhipan', 'zijinpan', 'shazhupan']),
    ('户', '账户按主体性质区分，直接对应风控强度',
     ['gonghu', 'sihu', 'heihu', 'jiabai', 'jinjian', 'lajihu', 'kaihu']),
    ('单', '任务与订单的流转环节',
     ['baodan', 'shangdan', 'chadan', 'zuodan', 'budan', 'huidan', 'zuodanqun',
      'chudan', 'yuantouchudan', 'jingjiapaidan', 'abdan', 'paodan', 'heidan']),
    ('商', '供给侧的角色分工',
     ['kashang', 'mashang', 'liaoshang', 'ushang', 'caishang', 'dingjihaoshang']),
    ('代X', '把每个需要真人出面的动作都外包出去',
     ['daix', 'daidiao', 'dailshiming', 'daifu', 'daihuan', 'daiweiquan',
      'daishou', 'daibaobei', 'daifapinglun', 'xianyudailiao']),
    ('保X／包X', '以"保／包+风险项"逐项列出愿意承担的后果，保与包同音混用',
     ['baox', 'shangya', 'mailei', 'zoudanbao', 'garansi', 'mianche',
      'baopi', 'baodeng', 'baoshoudeng', 'baozhaohui', 'baohuan', 'baofuzhu', 'baochirou']),
    ('无视X', '逐条点名被绕过的风控措施',
     ['wushi', 'feigui', 'poe', 'jiabai', 'difangyinhang', 'wushiip', 'abwushi']),
    ('群', '社群既是组织形式，也是被仿冒的"品牌"',
     ['gongqun', 'jiagongqun', 'zuodanqun', 'shangya', 'hutui', 'ruqunyanzheng',
      'gongqunbaobei', 'qunzukelong']),
    ('水', '同字两义：名词指可薅额度，动词短语指平台放宽；去水印里又指水印',
     ['xiaochengxushui', 'fangshui', 'qushuiyin', 'liushui', 'shualiushui', 'shuifang',
      'tingjiliushui']),
    ('口', '"口"指通道与入口，派生出多条互不相干的线',
     ['kouzi', 'pankou', 'jiekou', 'rukouzhiyin', 'dangkou', 'shoujikou', 'kouling',
      'xingkouying']),
    ('反X', '由一项已知信息倒查另一项，"A反B"是可售商品',
     ['fancha', 'mohu', 'gongwangbuqi', 'mingxia', 'fancuishou', 'doufan']),
]


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
    # 图例：栏目名与下方逐条渲染用的是同一组标签
    L.append('## 怎么读一个词条')
    L.append('')
    L.append('每个分类内按出现频次从高到低排列。每个词条依次是下面这些栏目，没有内容的栏目不显示。')
    L.append('')
    L.append('| 栏目 | 说明 |')
    L.append('| --- | --- |')
    for name, desc in [
            ('标题下的标签', '非中文词条的语种与流通生态'),
            ('**又作**', '同一个意思的其他写法：变体、谐音、缩写'),
            ('**释义**', '一句话说清这个词是什么意思'),
            ('**在链条中的作用**', '这个词在黑灰产链条里干什么、接上下游哪一环'),
            ('**示例**（脱敏）', '真实场景里的用法；平台名、联系方式、链接等已脱敏'),
            ('**无示例说明**', '取不到可发布的用例时，说明原因'),
            ('**歧义与误报**', '这个词在正常语境下的意思，也就是误报从哪来'),
            ('**关联**', f'相关词条，超过 {REL_SHOW} 个时只列前 {REL_SHOW} 个'),
            ('研判置信度', '；'.join(f'{CONF_LABEL[k]}：{CONF_DESC[k]}' for k in ('high', 'medium', 'to-verify'))),
            ('出现频次', '该词在监测语料中的出现频次档（常见 / 较常见 / 少见），用于排规则优先级，不代表风险高低'),
            ('收录版本', '首次收录的版本')]:
        L.append(f'| {name} | {desc} |')
    L.append('')
    L.append('各字段的完整定义见 [README · 字段说明](../README.zh-CN.md#字段说明)。')
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
    # 构词法词族索引（词根表定义在模块层 FAMILIES）
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
                if len(names) <= REL_SHOW:
                    L.append(f"**关联**：{'、'.join(names)}")
                else:
                    L.append(f"**关联**（{len(names)} 个）：{'、'.join(names[:REL_SHOW])} 等")
                L.append('')
            meta = [f"研判置信度：{CONF_LABEL[e['confidence']]}"]
            if e.get('frequency'):
                meta.append(f"出现频次：{e['frequency']}")
            if e.get('first_seen'):
                meta.append(f"收录版本：{e['first_seen']}")
            L.append('<sub>' + ' ｜ '.join(meta) + '</sub>')
            L.append('')
    return '\n'.join(L) + '\n'

# 两份 README：英文 README.md 是仓库首页，中文 README.zh-CN.md 与词典正文同语言，两份共用同一套同步键。
# 数字一律用 <!--key-->数字<!--/key--> 标记回填；成段内容（按语种分布、枢纽词）用同名块标记整段重写。
# 早先按中文原文措辞写死的正则同步，措辞一改就静默失效，所以一律改用标记。
READMES = (('README.md', 'en'), ('README.zh-CN.md', 'zh'))
BADGE_LABELS = {'en': ('entries', 'categories', 'languages', 'relations'),
                'zh': ('词条', '分类', '语种', '关联边')}
LANG_EN = {'zh': 'Chinese', 'en': 'English', 'pt': 'Brazilian Portuguese', 'id': 'Indonesian',
           'vi': 'Vietnamese', 'tl': 'Tagalog', 'ru': 'Russian', 'th': 'Thai', 'hi': 'Hindi'}
HUBS = 6          # README 列出的枢纽词个数


def sync_readme(entries):
    """把两份 README 里易漂移的数字与实际数据同步，避免每轮更新手改出错。"""
    by_id = {e['id']: e for e in entries}
    edges = set()
    for e in entries:
        for r in e.get('related') or []:
            edges.add(tuple(sorted((e['id'], r))))
    degree = Counter(x for edge in edges for x in edge)
    # 同度数按 id 排，保证每次构建结果一致（集合的遍历顺序随哈希种子变）
    hubs = [i for i, _ in sorted(degree.items(), key=lambda kv: (-kv[1], kv[0]))[:HUBS]]

    def has(e, k):
        return bool((e.get(k) or '').strip())

    def pct(part, whole):
        return round(100 * part / whole) if whole else 0

    n_amb = sum(1 for e in entries if has(e, 'ambiguity'))
    v1 = [e for e in entries if e.get('first_seen') == 'v0.1']
    v2 = [e for e in entries if e.get('first_seen') == 'v0.2']
    lang_count = Counter(e.get('lang', 'zh') for e in entries)
    cat_count = Counter(e['category'] for e in entries)
    seq = [lg for lg in LANG_ORDER if lang_count.get(lg)]

    badges = (len(entries), len(cat_count), len(lang_count), len(edges))
    # 百分号写在标记外面（<!--p_amb-->53<!--/p_amb-->%），标记里只放数字。
    numbers = {
        'n_all': len(entries),
        'n_amb': n_amb,
        'p_amb': pct(n_amb, len(entries)),
        'n_tv': sum(1 for e in entries if e.get('confidence') == 'to-verify'),
        'n_ex': sum(1 for e in entries if e.get('example')),
        'n_note': sum(1 for e in entries if e.get('example_note') and not e.get('example')),
        'n_v1': len(v1),
        'n_v2': len(v2),
        'n_tv_v2': sum(1 for e in v2 if e.get('confidence') == 'to-verify'),
        'p_amb_v1': pct(sum(has(e, 'ambiguity') for e in v1), len(v1)),
        'p_amb_v2': pct(sum(has(e, 'ambiguity') for e in v2), len(v2)),
        'p_mech_v1': pct(sum(has(e, 'mechanism') for e in v1), len(v1)),
        'n_mech_v2': sum(has(e, 'mechanism') for e in v2),
        'p_mech_v2': pct(sum(has(e, 'mechanism') for e in v2), len(v2)),
        'n_edge': len(edges),
        'n_cross': sum(1 for a, b in edges if by_id[a]['category'] != by_id[b]['category']),
    }
    numbers.update({'cat:' + c: cat_count.get(c, 0) for c in CATS})
    blocks = {
        'en': {'langs': ' · '.join('%s %d' % (LANG_EN.get(lg, lg), lang_count[lg]) for lg in seq),
               'hubs': ', '.join('**%s**' % by_id[i]['term'] for i in hubs)},
        'zh': {'langs': ' ｜ '.join('%s %d' % (LANG_SHORT.get(lg, lg), lang_count[lg]) for lg in seq),
               'hubs': '、'.join('**%s**' % by_id[i]['term'] for i in hubs)},
    }

    synced = []
    for fn, lg in READMES:
        path = os.path.join(ROOT, fn)
        if not os.path.exists(path):
            print('⚠ 找不到 %s，跳过同步' % fn)
            continue
        with open(path, encoding='utf-8') as f:
            s = f.read()
        orig = s
        for label, val in zip(BADGE_LABELS[lg], badges):
            s = re.sub(r'(badge/' + re.escape(label) + r'-)\d+(-)',
                       lambda m, v=val: m.group(1) + str(v) + m.group(2), s)
        for k, v in numbers.items():
            if '<!--%s-->' % k not in s:
                print('⚠ %s 里找不到同步标记 <!--%s-->，这个数字不会自动更新' % (fn, k))
            s = re.sub(r'(<!--%s-->)\d+(<!--/%s-->)' % (re.escape(k), re.escape(k)),
                       lambda m, v=v: m.group(1) + str(v) + m.group(2), s)
        for k, v in blocks[lg].items():
            if '<!--%s-->' % k not in s:
                print('⚠ %s 里找不到同步标记 <!--%s-->，这段内容不会自动更新' % (fn, k))
            s = re.sub(r'(<!--%s-->).*?(<!--/%s-->)' % (k, k),
                       lambda m, v=v: m.group(1) + v + m.group(2), s, flags=re.S)
        if s != orig:
            with open(path, 'w', encoding='utf-8') as f:
                f.write(s)
            synced.append(fn)
    if synced:
        print('已同步 README 中的统计数字：' + '、'.join(synced))


def main():
    entries = load()
    warns = []
    errs = validate(entries, warns)
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
    if warns:
        # 提示项不拦提交：mechanism / ambiguity 在 schema 里都是选填，
        # 但覆盖率下滑会让词典成色不齐，所以逐类报出来。
        buckets = {}
        for w in warns:
            k = w.split('|')[0]
            buckets.setdefault(re.sub(r'（\d+ 字）', '', k), []).append(w)
        print()
        print('提示（不拦提交）：')
        for k, v in sorted(buckets.items(), key=lambda x: -len(x[1])):
            print('  - %s：%d 条' % (k, len(v)))

if __name__ == '__main__':
    main()
