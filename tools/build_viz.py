#!/usr/bin/env python3
"""词条关系图谱 v2：径向扇区总览 + 类别下钻 + 构词法词族太阳图。

产出（均为构建生成物，勿手改）：
- docs/index.html  单文件交互页（Canvas 渲染：总览/下钻/词族三视图，数据内联）
- docs/graph.svg   静态预览图（README 内嵌）
- docs/graph.json  图数据（供外部工具复用）

设计要点：
- 布局为种子固定的确定性径向扇区：九分类各占一个扇区，扇区角按词条数分配，
  扇区内按度数分层排布；同一份词条数据在任何机器上构建结果一致。
- 边分骨干/全量两档：默认视图只画骨干边（两端度数和最高的前 600 条），
  全量边随数据下发，由前端滑杆控制。
- 跨类关联聚合为类间弧线，权重 = 跨类边条数。
- 词族数据来自 build_dict.FAMILIES（模块层单一来源）。
零第三方依赖。
"""
import json
import math
import os
import random
import re
import sys
from collections import Counter, defaultdict

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ENTRY_DIR = os.path.join(ROOT, 'data', 'entries')
DOCS = os.path.join(ROOT, 'docs')
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import build_dict  # noqa: E402  仅取模块层 FAMILIES / CATS

REPO_URL = 'https://github.com/threathunterX/underground-slang-lexicon'
DICT_MD = REPO_URL + '/blob/main/dict/%E9%BB%91%E8%AF%9D%E8%AF%8D%E5%85%B8.md'

W, H = 1600, 1000
CX, CY = W / 2, H / 2 + 6
R_IN, R_OUT = 168, 428
SEED = 20261017
BACKBONE_N = 600
TOP_LABELS = 8          # 每个扇区静态图标注的 Top 词条数

PALETTE = ['#FF9E64', '#F7768E', '#9ECE6A', '#7DCFFF', '#BB9AF7',
           '#FFD166', '#73DACA', '#C0CAF5', '#F0A6CA']
BG = '#0d1017'
INK = '#dce3ec'
DIM = '#8b96a5'
GOLD = '#ffd166'

FREQ_R = {'常见': 6.5, '较常见': 5.0, '少见': 3.8, '': 3.5}


def load_entries():
    entries = []
    for fn in sorted(os.listdir(ENTRY_DIR)):
        if fn.endswith('.json'):
            entries.extend(json.load(open(os.path.join(ENTRY_DIR, fn), encoding='utf-8')))
    return entries


def polar(cx, cy, r, ang):
    return cx + r * math.cos(ang), cy + r * math.sin(ang)


def gh_slug(text):
    """GitHub 标题锚点：小写、去掉标点与 emoji、空格换连字符（与 tools/check_links.py 同一口径）。"""
    return re.sub(r'[^\w一-鿿\- ]', '', text.strip().lower()).replace(' ', '-')


def dict_anchors(entries):
    """词典正文里每个词条标题的实际锚点，供图谱「在词典中查看」直达。

    不能直接拿词条名当锚点：GitHub 会转小写、去 emoji 与标点（「黑U」→ 黑u、「not VM made」→ not-vm-made），
    重名标题还会按出现顺序加 -1、-2（drop 与 DROP 是 drop / drop-1），所以按词典的标题顺序逐个算。
    """
    seen, out = {}, {}
    for level, text in re.findall(r'^(#{1,6})\s+(.+?)\s*$', build_dict.render(entries), re.M):
        base = slug = gh_slug(text)
        while slug in seen:
            seen[base] += 1
            slug = '%s-%d' % (base, seen[base])
        seen[slug] = 0
        if level == '###':
            out[text] = slug
    return out


def label_poly(lx, ly, w, rot_deg, h=30):
    """分类标签的实际占位：按切向旋转后的矩形四角（画布与 SVG 都按 rot 旋转绘制）。"""
    a = math.radians(rot_deg)
    ca, sa = math.cos(a), math.sin(a)
    return [(lx + dx * ca - dy * sa, ly + dx * sa + dy * ca)
            for dx, dy in ((-w / 2, -h / 2), (w / 2, -h / 2), (w / 2, h / 2), (-w / 2, h / 2))]


def polys_overlap(p, q):
    """凸多边形分离轴判定：只要某条边的法向上两者投影不相交，就不重叠。"""
    for poly in (p, q):
        for i in range(len(poly)):
            (x1, y1), (x2, y2) = poly[i], poly[(i + 1) % len(poly)]
            nx, ny = y2 - y1, x1 - x2
            pa = [nx * x + ny * y for x, y in p]
            pb = [nx * x + ny * y for x, y in q]
            if max(pa) < min(pb) or max(pb) < min(pa):
                return False
    return True


def build_graph(entries):
    cats = [c for c in build_dict.CATS if any(e['category'] == c for e in entries)]
    color = {c: PALETTE[i % len(PALETTE)] for i, c in enumerate(cats)}
    ci = {c: i for i, c in enumerate(cats)}
    anchors = dict_anchors(entries)

    deg = Counter()
    edge_set = set()
    for e in entries:
        for r in e.get('related') or []:
            edge_set.add(tuple(sorted((e['id'], r))))
            deg[e['id']] += 1
            deg[r] += 1
    by_id = {e['id']: e for e in entries}
    edges = [p for p in sorted(edge_set) if p[0] in by_id and p[1] in by_id]

    scored = sorted(edges, key=lambda p: -(deg[p[0]] + deg[p[1]]))
    backbone = set(scored[:BACKBONE_N])

    versions = Counter(e.get('first_seen') or '' for e in entries)
    version_list = [{'v': v, 'count': versions[v]} for v in sorted(versions) if v]

    # ---- 径向扇区布局 ----
    rnd = random.Random(SEED)
    n_cat = len(cats)
    weights = []
    for c in cats:
        n_c = sum(1 for e in entries if e['category'] == c)
        # 大扇区额外上浮 18% 角度，缓解最密分类的拥挤
        weights.append(n_c * 1.18 if n_c > 100 else max(n_c, 8))
    gap = 0.030
    total = sum(weights)
    span = 2 * math.pi - gap * n_cat
    sectors = {}
    ang = -math.pi / 2
    cat_nodes = defaultdict(list)
    for e in entries:
        cat_nodes[e['category']].append(e)
    for c in cats:
        w = span * weights[cats.index(c)] / total
        sectors[c] = (ang, ang + w)
        ang += w + gap

    nodes = []
    for c in cats:
        a0, a1 = sectors[c]
        mid = (a0 + a1) / 2
        anchor = polar(CX, CY, (R_IN + R_OUT) / 2, mid)
        members = sorted(cat_nodes[c], key=lambda e: -deg[e['id']])
        n = len(members)
        span = a1 - a0
        sectors[c] = (a0, a1, anchor)
        r_lo, r_hi = R_IN + 40, R_OUT - 16
        for k, e in enumerate(members):
            # 等角间隔 + 黄金比例半径序列：相邻节点在半径上天然错开，避免扎堆
            t = (k + 0.5) / max(n, 1)
            a = a0 + 0.06 * span + 0.88 * span * t + (rnd.random() - 0.5) * (span * 0.4 / max(n, 1))
            frac = (k * 0.6180339887 + rnd.random() * 0.08) % 1.0
            r = r_lo + (r_hi - r_lo) * (0.04 + 0.92 * frac)
            x, y = polar(CX, CY, r, a)
            nodes.append({
                'id': e['id'], 'term': e['term'], 'cat': c, 'ci': ci[c],
                'color': color[c], 'lang': e.get('lang', 'zh'),
                'region': e.get('region', ''), 'conf': e['confidence'],
                'freq': e.get('frequency', ''), 'first': e.get('first_seen', ''),
                # 别名全量下发：搜索要覆盖全部别名（此前截到前 6 个，谐音替字等词条的后续别名搜不到）；展示时再截断
                'deg': deg[e['id']], 'aka': e.get('aka') or [],
                'anchor': anchors.get(e['term'], gh_slug(e['term'])),
                'def': (e.get('definition') or '')[:160],
                'x': x, 'y': y,
            })

    # 扇区内松弛：把间距小于 11px 的节点对推开，并夹回本扇区范围（确定性）
    for c in cats:
        a0, a1, _ = sectors[c]
        pts = [n for n in nodes if n['cat'] == c]
        for _ in range(40):
            moved = False
            for i in range(len(pts)):
                for j in range(i + 1, len(pts)):
                    dx, dy = pts[i]['x'] - pts[j]['x'], pts[i]['y'] - pts[j]['y']
                    d = math.hypot(dx, dy)
                    if d < 11:
                        if d < 1e-6:
                            dx, dy, d = 0.5, 0.5, 0.707
                        push = (11 - d) / 2
                        ux, uy = dx / d, dy / d
                        pts[i]['x'] += ux * push; pts[i]['y'] += uy * push
                        pts[j]['x'] -= ux * push; pts[j]['y'] -= uy * push
                        moved = True
            for n_ in pts:
                dx, dy = n_['x'] - CX, n_['y'] - CY
                r = min(max(math.hypot(dx, dy), r_lo - 10), r_hi + 10)
                a = math.atan2(dy, dx)
                while a < a0:
                    a += 2 * math.pi
                a = min(max(a, a0 + 0.015), a1 - 0.015)
                n_['x'], n_['y'] = polar(CX, CY, r, a)
            if not moved:
                break
    for n in nodes:
        n['x'] = round(n['x'], 1)
        n['y'] = round(n['y'], 1)

    # ---- 类间聚合 ----
    cross = Counter()
    for a, b in edges:
        ca, cb = by_id[a]['category'], by_id[b]['category']
        if ca != cb:
            cross[tuple(sorted((ca, cb)))] += 1
    cat_meta = []
    placed_polys = []
    for i, c in enumerate(cats):
        a0, a1, anchor = sectors[c]
        span = a1 - a0
        # 标签锚点：外圈错层（相邻扇区一半近一半远），窄扇区再外推；
        # 与已放置标签碰撞时沿径向再外推重试（确定性）
        mid = (a0 + a1) / 2
        cjk = sum(1 for ch in c if ord(ch) > 0x2E7F)
        lab_w = (cjk + (len(c) - cjk) * 0.56) * 19 + 46
        # 切向标签，方向固定为"从左向右读"：顶部/底部水平、两侧竖排
        tdx, tdy = -math.sin(mid), math.cos(mid)
        if tdx < 0 or (abs(tdx) < 1e-9 and tdy > 0):
            tdx, tdy = -tdx, -tdy
        rot = math.degrees(math.atan2(tdy, tdx))
        # 窄扇区直接外移轮外并配引线，避免与相邻标签在同环挤靠
        outer = span < 0.2
        leader = None
        if outer:
            sx, sy = polar(CX, CY, R_OUT + 8, mid)
            leader = [round(sx, 1), round(sy, 1)]
        lx = ly = 0
        # 碰撞按旋转后的实际矩形判定：标签是切向斜置的，按水平包围盒判会漏掉斜标签之间的重叠
        for attempt in range(10):
            r_try = (R_OUT + 168 if outer else R_OUT + (64 if i % 2 == 0 else 102)) + 34 * attempt
            # 切向错位：同角度簇的标签沿弧向左右让位（偶数档向右、奇数档向左，幅度递增）
            t_shift = 26 * ((attempt + 1) // 2) * (1 if i % 2 == 0 else -1)
            ax, ay = polar(CX, CY, min(r_try, 548), mid)
            tx, ty = -math.sin(mid), math.cos(mid)
            lx, ly = ax + tx * t_shift, ay + ty * t_shift
            lx = min(max(lx, 46), W - 46)
            ly = min(max(ly, 44), H - 30)
            poly = label_poly(lx, ly, lab_w + 12, rot)
            if not any(polys_overlap(poly, q) for q in placed_polys):
                break
        placed_polys.append(poly)
        top = sorted(cat_nodes[c], key=lambda e: -deg[e['id']])[:TOP_LABELS]
        cat_meta.append({
            'name': c, 'color': color[c],
            'n': len(cat_nodes[c]),
            'top': [e['term'] for e in top],
            'a0': a0, 'a1': a1, 'anchor': [round(anchor[0], 1), round(anchor[1], 1)],
            'label': [round(lx, 1), round(ly, 1), round(rot, 1)],
            'leader': leader,
            'cross': {other: n for (x, y), n in cross.items()
                      for other in [y if x == c else x if y == c else None] if other},
        })

    # 跨类弧线：只保留权重最高的若干条，几何/透明度/线宽预计算
    arcs = []
    for (x, y), n in sorted(cross.items(), key=lambda kv: -kv[1])[:26]:
        if n < 6:
            continue
        cm_x = next(m for m in cat_meta if m['name'] == x)
        cm_y = next(m for m in cat_meta if m['name'] == y)
        x0, y0 = cm_x['anchor']
        x1, y1 = cm_y['anchor']
        mx, my = CX + 0.45 * ((x0 + x1) / 2 - CX), CY + 0.45 * ((y0 + y1) / 2 - CY)
        arcs.append({
            'x0': round(x0, 1), 'y0': round(y0, 1), 'x1': round(x1, 1), 'y1': round(y1, 1),
            'mx': round(mx, 1), 'my': round(my, 1),
            'w': round(min(1.2 + math.sqrt(n) * 0.38, 7), 1),
            'a': round(min(0.14 + n / 700, 0.5), 2), 'n': n,
        })

    # 顶部/底部夹断区标签：径向外推会被画布边缘夹断失效，改为水平槽位重排
    def lab_width(name):
        cjk = sum(1 for ch in name if ord(ch) > 0x2E7F)
        return (cjk + (len(name) - cjk) * 0.56) * 19 + 46

    def poly_of(m, x=None):
        return label_poly(m['label'][0] if x is None else x, m['label'][1], lab_width(m['name']) + 12, m['label'][2])

    for zone_pred in (lambda y: y < 60, lambda y: y > H - 36):
        row = [c for c in cat_meta if zone_pred(c['label'][1])]
        row.sort(key=lambda c: c['label'][0])
        others = [m for m in cat_meta if m not in row]
        prev_end = None
        for c in row:
            w = lab_width(c['name'])
            x = c['label'][0]
            if prev_end is not None:
                x = max(x, prev_end + w / 2 + 24)
            # 还要避开不在这一行、但斜置后伸进来的相邻标签（顶部窄扇区旁的斜标签曾因此互相压住）
            while x < W - 46 - w / 2 and any(polys_overlap(poly_of(c, x), poly_of(o)) for o in others):
                x += 8
            x = min(x, W - 46 - w / 2)
            c['label'][0] = round(x, 1)
            prev_end = x + w / 2

    families = []
    for stem, note, ids in build_dict.FAMILIES:
        members = [by_id[i] for i in ids if i in by_id]
        if len(members) >= 3:
            families.append({
                'stem': stem, 'note': note,
                'terms': [{'id': m['id'], 'term': m['term'], 'cat': m['category'],
                           'color': color[m['category']]} for m in members],
            })

    return {
        'meta': {
            'n_entries': len(entries), 'n_edges': len(edges), 'n_backbone': len(backbone),
            'latest': max((v['v'] for v in version_list), default=''),
            'versions': version_list,
            'categories': cat_meta,
            'arcs': arcs,
        },
        'nodes': nodes,
        'edges': [[a, b, 1 if (a, b) in backbone else 0] for a, b in edges],
        'families': families,
    }


def esc(s):
    return (str(s).replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;'))


def wedge_path(a0, a1, r0, r1):
    x0o, y0o = polar(CX, CY, r1, a0)
    x1o, y1o = polar(CX, CY, r1, a1)
    x1i, y1i = polar(CX, CY, r0, a1)
    x0i, y0i = polar(CX, CY, r0, a0)
    large = 1 if (a1 - a0) > math.pi else 0
    return (f'M {x0o:.1f} {y0o:.1f} A {r1:.0f} {r1:.0f} 0 {large} 1 {x1o:.1f} {y1o:.1f} '
            f'L {x1i:.1f} {y1i:.1f} A {r0:.0f} {r0:.0f} 0 {large} 0 {x0i:.1f} {y0i:.1f} Z')


def render_svg(g):
    by_id = {n['id']: n for n in g['nodes']}
    latest = g['meta']['latest']
    p = []
    p.append(f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {W} {H}" '
             f'font-family="-apple-system,PingFang SC,Microsoft YaHei,sans-serif">')
    p.append(f'<rect width="{W}" height="{H}" fill="{BG}"/>')

    # 扇区底与类名（锚点、错层与旋转角已在数据端预计算）
    for c in g['meta']['categories']:
        a0, a1 = c['a0'], c['a1']
        p.append(f'<path d="{wedge_path(a0, a1, R_IN, R_OUT)}" fill="{c["color"]}" '
                 f'fill-opacity="0.055" stroke="{c["color"]}" stroke-opacity="0.22" stroke-width="1"/>')
        lx, ly, rot = c['label']
        if c.get('leader'):
            ex, ey = c['leader']
            p.append(f'<line x1="{ex:.0f}" y1="{ey:.0f}" x2="{lx:.0f}" y2="{ly:.0f}" '
                     f'stroke="{c["color"]}" stroke-opacity="0.5" stroke-width="1.2" '
                     f'stroke-dasharray="3 3"/>')
        p.append(f'<text x="{lx:.0f}" y="{ly:.0f}" transform="rotate({rot:.1f} {lx:.0f} {ly:.0f})" '
                 f'fill="{c["color"]}" font-size="17" font-weight="600" text-anchor="middle" '
                 f'paint-order="stroke" stroke="{BG}" stroke-width="5">{esc(c["name"])} {c["n"]}</text>')

    # 骨干边（类内）
    for a, b, bb in g['edges']:
        if not bb:
            continue
        na, nb = by_id[a], by_id[b]
        if na['ci'] != nb['ci']:
            continue
        p.append(f'<line x1="{na["x"]}" y1="{na["y"]}" x2="{nb["x"]}" y2="{nb["y"]}" '
                 f'stroke="#2b3648" stroke-opacity="0.20" stroke-width="0.7"/>')

    # 类间弧线（Top26，几何与样式预计算）
    for arc in g['meta']['arcs']:
        p.append(f'<path d="M {arc["x0"]:.0f} {arc["y0"]:.0f} Q {arc["mx"]:.0f} {arc["my"]:.0f} '
                 f'{arc["x1"]:.0f} {arc["y1"]:.0f}" fill="none" '
                 f'stroke="#8899bb" stroke-opacity="{arc["a"]}" stroke-width="{arc["w"]}"/>')

    # 节点与每类 Top 标签
    top_ids = set()
    for c in g['meta']['categories']:
        top = sorted((n for n in g['nodes'] if n['cat'] == c['name']), key=lambda n: -n['deg'])[:TOP_LABELS]
        top_ids.update(n['id'] for n in top)
    for n in g['nodes']:
        r = FREQ_R.get(n['freq'], 3.5)
        ring = (f' stroke="{GOLD}" stroke-width="2.0"' if n['first'] == latest and n['first'] else '')
        p.append(f'<circle cx="{n["x"]}" cy="{n["y"]}" r="{r}" fill="{n["color"]}" '
                 f'fill-opacity="0.9"{ring}/>')
    placed = []

    def try_place(lx, ly, w, h):
        pad = 4
        for (px, py, pw, ph) in placed:
            if not (lx + w + pad < px or px + pw + pad < lx or ly + h + pad < py or py + ph + pad < ly):
                return False
        placed.append((lx, ly, w, h))
        return True

    for n in sorted(g['nodes'], key=lambda m: -m['deg']):
        if n['id'] not in top_ids:
            continue
        fs = 13 if n['deg'] >= 24 else 12
        w_cjk = sum(1 for ch in n['term'] if ord(ch) > 0x2E7F)
        w = (w_cjk + (len(n['term']) - w_cjk) * 0.56) * fs
        lx = n['x'] + FREQ_R.get(n['freq'], 3.5) + 3
        if not try_place(lx, n['y'] - fs * 0.8, w, fs * 1.15):
            continue
        p.append(f'<text x="{lx:.0f}" y="{n["y"] + 3.5:.0f}" fill="{INK}" font-size="{fs}" '
                 f'paint-order="stroke" stroke="{BG}" stroke-width="4">{esc(n["term"])}</text>')

    # 中心信息区
    p.append(f'<circle cx="{CX:.0f}" cy="{CY:.0f}" r="{R_IN - 26}" fill="#11151d" stroke="#232b3a"/>')
    p.append(f'<text x="{CX:.0f}" y="{CY - 54:.0f}" fill="{INK}" font-size="30" font-weight="700" '
             f'text-anchor="middle">黑灰产黑话图谱</text>')
    p.append(f'<text x="{CX:.0f}" y="{CY - 24:.0f}" fill="{DIM}" font-size="13" text-anchor="middle">'
             f'{g["meta"]["n_entries"]} 词条 · {g["meta"]["n_edges"]} 关联 · {len(g["meta"]["categories"])} 分类'
             f' · {len(g["families"])} 词根</text>')
    bx = CX - 92
    for i, v in enumerate(g['meta']['versions']):
        hgt = min(10 + v['count'] * 0.06, 64)
        p.append(f'<rect x="{bx + i * 46:.0f}" y="{CY + 44 - hgt:.0f}" width="26" height="{hgt:.0f}" '
                 f'rx="3" fill="{GOLD if v["v"] == latest else "#3a4557"}" '
                 f'fill-opacity="{1.0 if v["v"] == latest else 0.8}"/>')
        p.append(f'<text x="{bx + i * 46 + 13:.0f}" y="{CY + 58:.0f}" fill="{DIM}" font-size="11" '
                 f'text-anchor="middle">{esc(v["v"])}</text>')
        p.append(f'<text x="{bx + i * 46 + 13:.0f}" y="{CY + 71:.0f}" fill="{DIM}" font-size="11" '
                 f'text-anchor="middle">{v["count"]}</text>')

    p.append(f'<text x="28" y="{H - 66}" fill="{DIM}" font-size="12">'
             f'节点大小 = 出现频次档　金圈 = {esc(latest)} 新增　弧线粗细 = 跨类关联强度　'
             f'交互版支持搜索 / 分类下钻 / 词族视图</text>')
    p.append('</svg>')
    return '\n'.join(p)


HTML = r'''<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<link rel="icon" href="data:,">
<title>黑灰产黑话图谱 · underground-slang-lexicon</title>
<style>
:root{--bg:#0d1017;--panel:#141924;--line:#263041;--text:#dce3ec;--dim:#8b96a5;--gold:#ffd166}
*{box-sizing:border-box}
html,body{margin:0;height:100%;background:var(--bg);color:var(--text);
  font:14px/1.6 -apple-system,"PingFang SC","Microsoft YaHei",sans-serif;overflow:hidden}
#app{display:flex;flex-direction:column;height:100%}
#bar{display:flex;align-items:center;gap:12px;padding:10px 16px;border-bottom:1px solid var(--line);
  background:#10141c;flex-wrap:wrap}
#bar h1{font-size:15px;margin:0;font-weight:600}
#bar h1 a{color:var(--text);text-decoration:none}
.tabs{display:flex;gap:4px}
.tab{padding:5px 14px;border-radius:8px;border:1px solid var(--line);background:none;color:var(--dim);
  cursor:pointer;font-size:13px}
.tab.on{background:#1d2636;color:var(--text);font-weight:600;border-color:#3a4a63}
#search{flex:0 1 260px;padding:6px 10px;border-radius:8px;border:1px solid var(--line);
  background:#0b0e13;color:var(--text);font-size:13px;outline:none}
#search:focus{border-color:#7dcfff}
#stage{flex:1;position:relative;min-height:0}
canvas{display:block;width:100%;height:100%;cursor:default}
#hud{position:absolute;left:12px;bottom:10px;color:var(--dim);font-size:11px}
#hud a{color:var(--dim)}
#crumb{position:absolute;left:14px;top:10px;display:flex;gap:8px;align-items:center;
  font-size:13px;color:var(--dim)}
#crumb button{padding:3px 12px;border-radius:7px;border:1px solid var(--line);background:#10141ccc;
  color:var(--text);cursor:pointer;font-size:12px}
#crumb button:hover{border-color:#7dcfff}
#tip{position:absolute;display:none;max-width:280px;background:#161b26f2;border:1px solid var(--line);
  border-radius:9px;padding:9px 12px;font-size:12.5px;pointer-events:none;z-index:5}
#tip b{font-size:13.5px}
#tip .d{color:var(--dim);margin-top:3px}
#side{width:330px;background:var(--panel);border-left:1px solid var(--line);padding:18px;
  overflow-y:auto;display:none;position:relative}
#side h2{margin:2px 0 4px;font-size:19px}
#side .aka{color:var(--dim);font-size:12.5px;margin-bottom:10px}
#side .tagline{display:flex;gap:6px;flex-wrap:wrap;margin-bottom:12px}
.tag{font-size:11px;padding:2px 8px;border-radius:6px;border:1px solid var(--line);color:var(--dim)}
.tag.cat{color:#0d1017;font-weight:600}
#side h3{font-size:12px;color:var(--dim);margin:14px 0 3px;font-weight:600;letter-spacing:.05em}
#side p{margin:0;font-size:13.5px}
#side a{color:#7dcfff;text-decoration:none}
#side .rel{display:flex;flex-wrap:wrap;gap:5px}
#side .rel button{font-size:12px;padding:2px 9px;border-radius:6px;border:1px solid var(--line);
  background:none;color:var(--text);cursor:pointer}
#side .rel button:hover{border-color:#7dcfff}
#side .close{position:absolute;top:10px;right:12px;background:none;border:none;color:var(--dim);
  font-size:18px;cursor:pointer}
#layout{display:flex;flex:1;min-height:0}
#edgeCtl{display:flex;align-items:center;gap:8px;color:var(--dim);font-size:12px}
#edgeCtl input{accent-color:#7dcfff}
@media (max-width:760px){#side{width:78vw}}
</style>
</head>
<body>
<div id="app">
  <div id="bar">
    <h1><a href="__REPO__" target="_blank">黑灰产黑话图谱</a></h1>
    <div class="tabs">
      <button class="tab on" data-mode="overview">产业链总览</button>
      <button class="tab" data-mode="families">构词法词族</button>
    </div>
    <input id="search" placeholder="搜索词条 / 别名，回车定位">
    <div id="edgeCtl" style="display:none">
      <span>关联边</span><input type="range" id="edges" min="0" max="100" value="60">
      <span id="edgeVal"></span>
    </div>
    <span id="stats" style="color:var(--dim);font-size:12px"></span>
  </div>
  <div id="layout">
    <div id="stage">
      <div id="crumb" style="display:none"></div>
      <canvas id="cv"></canvas>
      <div id="tip"></div>
      <div id="hud">由 <a href="__REPO__/blob/main/tools/build_viz.py" target="_blank">tools/build_viz.py</a> 自动生成 ·
        词典全文见 <a href="__DICTMD__" target="_blank">dict/黑话词典.md</a></div>
    </div>
    <aside id="side"></aside>
  </div>
</div>
<script>
const G = __GRAPH__;
const byId = {}; G.nodes.forEach(n => byId[n.id] = n);
const catOf = {}; G.meta.categories.forEach(c => catOf[c.name] = c);
const latest = G.meta.latest;
const adj = {}; G.edges.forEach(([a, b]) => { (adj[a] = adj[a] || []).push(b); (adj[b] = adj[b] || []).push(a); });
const FR = {'常见':6.5,'较常见':5.0,'少见':3.8,'':3.5};

const cv = document.getElementById('cv'), ctx = cv.getContext('2d');
const stage = document.getElementById('stage'), tip = document.getElementById('tip');
const side = document.getElementById('side'), crumb = document.getElementById('crumb');
const edgeCtl = document.getElementById('edgeCtl'), edgeSlider = document.getElementById('edges');
const W = 1600, H = 1000, CX = W/2, CY = H/2 + 6;
let view = {mode: 'overview', cat: null, k: 1, ox: 0, oy: 0, nodes: null, sim: null};
let hoverNode = null, hoverFam = null, hoverSec = null;

function fit(){
  const r = stage.getBoundingClientRect(), dpr = window.devicePixelRatio || 1;
  cv.width = r.width * dpr; cv.height = r.height * dpr;
  ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
  const s = Math.min(r.width / W, r.height / H) * 0.98;
  view.k = s; view.ox = (r.width - W * s) / 2; view.oy = (r.height - H * s) / 2;
  draw();
}
window.addEventListener('resize', fit);
// 侧栏开合会改舞台宽度但不触发 window resize：不重新适配，画布缓冲还是旧宽度，图被横向挤扁
if (window.ResizeObserver) new ResizeObserver(() => fit()).observe(stage);

function toScreen(x, y){ return [view.ox + x * view.k, view.oy + y * view.k]; }
function toWorld(x, y){ return [(x - view.ox) / view.k, (y - view.oy) / view.k]; }

function draw(){
  const r = stage.getBoundingClientRect();
  ctx.clearRect(0, 0, r.width, r.height);
  ctx.save();
  ctx.translate(view.ox, view.oy); ctx.scale(view.k, view.k);
  if (view.mode === 'overview') drawOverview();
  else if (view.mode === 'drill') drawDrill();
  else drawFamilies();
  ctx.restore();
}

// ---------- 总览：径向扇区 ----------
function drawOverview(){
  const show = Math.round(G.meta.n_backbone * (edgeSlider.value / 100));
  const edges = G.edges.filter(e => e[2] === 1).slice(0, Math.max(show, 60));
  // 类间弧线（Top26，样式预计算）
  G.meta.arcs.forEach(a => {
    ctx.beginPath(); ctx.moveTo(a.x0, a.y0);
    ctx.quadraticCurveTo(a.mx, a.my, a.x1, a.y1);
    ctx.strokeStyle = 'rgba(150,165,200,' + a.a + ')';
    ctx.lineWidth = a.w; ctx.stroke();
  });
  // 扇区底
  G.meta.categories.forEach(c => {
    ctx.beginPath();
    ctx.arc(CX, CY, 428, c.a0, c.a1);
    ctx.arc(CX, CY, 168, c.a1, c.a0, true);
    ctx.closePath();
    ctx.fillStyle = c.color + (hoverSec === c.name ? '18' : '0e');
    ctx.fill();
    ctx.strokeStyle = c.color + '38'; ctx.lineWidth = 1; ctx.stroke();
    const [lx, ly, rot] = c.label;
    if (c.leader){
      ctx.beginPath(); ctx.moveTo(c.leader[0], c.leader[1]); ctx.lineTo(lx, ly);
      ctx.strokeStyle = c.color + '80'; ctx.lineWidth = 1.2; ctx.setLineDash([3, 3]);
      ctx.stroke(); ctx.setLineDash([]);
    }
    ctx.save(); ctx.translate(lx, ly); ctx.rotate(rot * Math.PI / 180);
    ctx.fillStyle = c.color; ctx.font = '600 17px -apple-system,PingFang SC,sans-serif';
    ctx.textAlign = 'center'; ctx.textBaseline = 'middle';
    ctx.lineWidth = 5; ctx.strokeStyle = '#0d1017';
    ctx.strokeText(c.name + ' ' + c.n, 0, 0); ctx.fillText(c.name + ' ' + c.n, 0, 0);
    ctx.restore();
  });
    // 骨干边
  ctx.strokeStyle = 'rgba(43,54,72,0.30)'; ctx.lineWidth = 0.7;
  edges.forEach(([a, b]) => {
    const na = byId[a], nb = byId[b];
    if (na.ci === nb.ci){ ctx.beginPath(); ctx.moveTo(na.x, na.y); ctx.lineTo(nb.x, nb.y); ctx.stroke(); }
  });
  // 节点
  G.nodes.forEach(n => {
    ctx.beginPath(); ctx.arc(n.x, n.y, FR[n.freq] || 3.5, 0, 6.284);
    ctx.fillStyle = n.color; ctx.globalAlpha = 0.92; ctx.fill(); ctx.globalAlpha = 1;
    if (n.first && n.first === latest){
      ctx.beginPath(); ctx.arc(n.x, n.y, (FR[n.freq] || 3.5) + 2.6, 0, 6.284);
      ctx.strokeStyle = '#ffd166'; ctx.lineWidth = 1.8; ctx.stroke();
    }
  });
  // 每类 Top 标签（贪心避让：放不下就不画）
  ctx.textAlign = 'left'; ctx.textBaseline = 'alphabetic';
  ctx.lineWidth = 4; ctx.strokeStyle = '#0d1017'; ctx.lineJoin = 'round';
  const placed = [];
  const fits = (x, y, w, h) => {
    for (const [px, py, pw, ph] of placed){
      if (!(x + w + 3 < px || px + pw + 3 < x || y + h + 3 < py || py + ph + 3 < y)) return false;
    }
    placed.push([x, y, w, h]); return true;
  };
  G.meta.categories.forEach(c => {
    const top = G.nodes.filter(n => n.cat === c.name).sort((a,b) => b.deg - a.deg).slice(0, 8);
    top.forEach(n => {
      const fs = n.deg >= 24 ? 13 : 12;
      const cjk = [...n.term].filter(ch => ch.codePointAt(0) > 0x2E7F).length;
      const w = (cjk + (n.term.length - cjk) * 0.56) * fs;
      if (!fits(n.x + 9, n.y - fs * 0.8, w, fs * 1.15)) return;
      ctx.font = (n.deg >= 24 ? '600 ' + fs + 'px' : fs + 'px') + ' -apple-system,PingFang SC,sans-serif';
      ctx.strokeText(n.term, n.x + 9, n.y + 4);
      ctx.fillStyle = '#e6edf5'; ctx.fillText(n.term, n.x + 9, n.y + 4);
    });
  });
  // 中心信息
  ctx.beginPath(); ctx.arc(CX, CY, 142, 0, 6.284); ctx.fillStyle = '#11151d'; ctx.fill();
  ctx.strokeStyle = '#232b3a'; ctx.stroke();
  ctx.textAlign = 'center'; ctx.fillStyle = '#dce3ec';
  ctx.font = '700 30px -apple-system,PingFang SC,sans-serif'; ctx.fillText('黑灰产黑话图谱', CX, CY - 52);
  ctx.fillStyle = '#8b96a5'; ctx.font = '13px -apple-system,PingFang SC,sans-serif';
  ctx.fillText(G.meta.n_entries + ' 词条 · ' + G.meta.n_edges + ' 关联', CX, CY - 22);
  const vs = G.meta.versions, bx = CX - (vs.length * 46) / 2 + 10;
  vs.forEach((v, i) => {
    const h = Math.min(10 + v.count * 0.06, 62);
    ctx.fillStyle = v.v === latest ? '#ffd166' : '#3a4557';
    roundRect(bx + i * 46, CY + 42 - h, 26, h, 3); ctx.fill();
    ctx.fillStyle = '#8b96a5'; ctx.font = '11px sans-serif';
    ctx.fillText(v.v, bx + i * 46 + 13, CY + 56);
    ctx.fillText(v.count, bx + i * 46 + 13, CY + 69);
  });
}
function roundRect(x, y, w, h, r){
  ctx.beginPath(); ctx.moveTo(x + r, y);
  ctx.arcTo(x + w, y, x + w, y + h, r); ctx.arcTo(x + w, y + h, x, y + h, r);
  ctx.arcTo(x, y + h, x, y, r); ctx.arcTo(x, y, x + w, y, r); ctx.closePath();
}

// ---------- 下钻：单类子图 ----------
function enterDrill(catName, flash){
  const members = G.nodes.filter(n => n.cat === catName);
  const memberIds = new Set(members.map(n => n.id));
  const ghosts = [];
  members.forEach(n => (adj[n.id] || []).forEach(r => {
    if (!memberIds.has(r) && ghosts.length < 120 && !ghosts.find(g => g.id === r)) ghosts.push(byId[r]);
  }));
  const nodes = members.map(n => ({...n, ghost: false})).concat(ghosts.map(n => ({...n, ghost: true})));
  const idx = {}; nodes.forEach((n, i) => idx[n.id] = i);
  const sub = [];
  G.edges.forEach(([a, b, bb]) => {
    if (a in idx && b in idx) sub.push([idx[a], idx[b], bb]);
  });
  // 初始位置：类内节点小圆环，幽灵按原坐标收缩
  // 幽灵节点：按其关联成员的平均方向锚定在外环，不参与力导向（消除边缘堆叠）
  const memPos = {};
  members.forEach((m, i) => {
    const a = 2 * Math.PI * i / Math.max(members.length, 1);
    memPos[m.id] = [CX + 300 * Math.cos(a) * (0.55 + 0.45 * ((i % 3) / 3)),
                    CY + 210 * Math.sin(a) * (0.55 + 0.45 * ((i % 3) / 3))];
  });
  nodes.forEach((n, i) => {
    if (!n.ghost){
      [n.x, n.y] = memPos[n.id];
    } else {
      let sx = 0, sy = 0, c = 0;
      (adj[n.id] || []).forEach(r => { if (memPos[r]) { sx += memPos[r][0]; sy += memPos[r][1]; c++; } });
      const ang = c ? Math.atan2(sy / c - CY, sx / c - CX) : 2 * Math.PI * i / Math.max(ghosts.length, 1);
      const rr = 596 + (i % 4) * 30;
      n.x = Math.max(96, Math.min(W - 96, CX + rr * Math.cos(ang)));
      n.y = Math.max(116, Math.min(H - 80, CY + rr * 0.66 * Math.sin(ang)));
      n.fixed = true;
    }
    n.vx = 0; n.vy = 0;
  });
  view.mode = 'drill'; view.cat = catName; view.nodes = nodes; view.sub = sub; view.flash = flash || null;
  crumb.style.display = 'flex';
  crumb.innerHTML = '<button id="backOv">← 总览</button><span>' + catName +
    '（' + members.length + ' 词条' + (ghosts.length ? ' + 外环 ' + ghosts.length + ' 个跨类关联' : '') + '）</span>';
  document.getElementById('backOv').onclick = e => { e.stopPropagation(); exitDrill(); };
  edgeCtl.style.display = 'flex';
  startSim();
  draw();
}
function exitDrill(){
  view.mode = 'overview'; view.cat = null; view.nodes = null;
  crumb.style.display = 'none'; edgeCtl.style.display = 'none';
  edgeSlider.value = 60; draw();
}
function startSim(){
  cancelAnimationFrame(view._tick);
  const nodes = view.nodes, sub = view.sub;
  const k = 58;
  let iter = 0;
  const simStep = () => {
    // 温度：随迭代衰减，限制单帧位移，防止力爆炸把节点甩到边界
    const t = Math.max(26 * (1 - iter / 300), 0.8);
    for (let it = 0; it < 2; it++){
      for (let i = 0; i < nodes.length; i++){
        for (let j = i + 1; j < nodes.length; j++){
          let dx = nodes[i].x - nodes[j].x, dy = nodes[i].y - nodes[j].y;
          let d2 = dx*dx + dy*dy || 0.01;
          if (d2 > 200*200) continue;
          const d = Math.sqrt(d2), f = Math.min(k*k/d, 18);
          const ux = dx/d, uy = dy/d;
          nodes[i].vx += ux*f; nodes[i].vy += uy*f;
          nodes[j].vx -= ux*f; nodes[j].vy -= uy*f;
        }
      }
      sub.forEach(([a, b]) => {
        const na = nodes[a], nb = nodes[b];
        if (view.drag === na || view.drag === nb) return;
        let dx = na.x - nb.x, dy = na.y - nb.y;
        const d = Math.sqrt(dx*dx + dy*dy) || 0.01;
        const f = (na.ghost || nb.ghost ? 0.0008 : 0.010) * (d - 84);
        const ux = dx/d, uy = dy/d;
        na.vx -= ux*f; na.vy -= uy*f; nb.vx += ux*f; nb.vy += uy*f;
      });
      nodes.forEach(n => {
        if (n.fixed || n === view.drag) { n.vx = 0; n.vy = 0; return; }
        const pull = n.ghost ? 0.0012 : (n.deg >= 20 ? 0.0032 : n.deg >= 8 ? 0.0018 : 0.0009);
        n.vx += (CX - n.x) * pull; n.vy += (CY - n.y) * pull;
        n.vx *= 0.60; n.vy *= 0.60;
        const m = Math.hypot(n.vx, n.vy);
        if (m > t){ n.vx *= t/m; n.vy *= t/m; }
        n.x = Math.max(60, Math.min(W - 60, n.x + n.vx));
        n.y = Math.max(86, Math.min(H - 64, n.y + n.vy));
        if (!n.ghost){
          // 成员约束在内椭圆：斥力把节点均匀压向边界，形成满铺而非外溢
          const ddx = n.x - CX, ddy = n.y - CY;
          const e = Math.hypot(ddx / 560, ddy / 336);
          if (e > 1){ n.x = CX + ddx / e; n.y = CY + ddy / e; }
        }
      });
      // 最小间距分离：防止标签糊团（温和推挤）
      for (let i = 0; i < nodes.length; i++){
        for (let j = i + 1; j < nodes.length; j++){
          const dx = nodes[i].x - nodes[j].x, dy = nodes[i].y - nodes[j].y;
          const d = Math.hypot(dx, dy);
          if (d < 13 && d > 1e-6){
            const push = Math.min((13 - d) / 2, 1.5), ux = dx / d, uy = dy / d;
            nodes[i].x += ux * push; nodes[i].y += uy * push;
            nodes[j].x -= ux * push; nodes[j].y -= uy * push;
          }
        }
      }
    }
    iter++;
  };
  // 同步预热：先在温度衰减下收敛再首帧渲染
  for (let w = 0; w < 240; w++) simStep();
  const step = () => { simStep(); draw(); if (iter < 360) view._tick = requestAnimationFrame(step); };
  view._tick = requestAnimationFrame(step);
}

function drawDrill(){
  const show = Math.round(view.sub.length * (edgeSlider.value / 100));
  view.sub.slice(0, Math.max(show, 40)).forEach(([a, b]) => {
    const na = view.nodes[a], nb = view.nodes[b];
    ctx.beginPath(); ctx.moveTo(na.x, na.y); ctx.lineTo(nb.x, nb.y);
    ctx.strokeStyle = na.ci === nb.ci ? 'rgba(43,54,72,0.42)' : 'rgba(60,72,96,0.22)';
    ctx.lineWidth = 0.8; ctx.stroke();
  });
  ctx.textAlign = 'left'; ctx.lineWidth = 4; ctx.strokeStyle = '#0d1017'; ctx.lineJoin = 'round';
  view.nodes.forEach(n => {
    const r = n.ghost ? (FR[n.freq] || 3.5) * 0.7 : (FR[n.freq] || 3.5);
    ctx.beginPath(); ctx.arc(n.x, n.y, r, 0, 6.284);
    ctx.globalAlpha = n.ghost ? 0.28 : 0.95; ctx.fillStyle = n.color; ctx.fill(); ctx.globalAlpha = 1;
    if (n.ghost){ ctx.beginPath(); ctx.arc(n.x, n.y, r + 1.5, 0, 6.284);
      ctx.strokeStyle = n.color + '55'; ctx.lineWidth = 1; ctx.stroke(); }
    if (n.first && n.first === latest && !n.ghost){
      ctx.beginPath(); ctx.arc(n.x, n.y, r + 2.6, 0, 6.284);
      ctx.strokeStyle = '#ffd166'; ctx.lineWidth = 1.8; ctx.stroke();
    }
    });
  // 标签贪心避让：按度数优先占位（含金圈枢纽优先），碰撞即放弃低优先标签
  const lab = [];
  view.nodes.forEach(n => { if (!n.ghost || n.deg >= 18) lab.push(n); });
  lab.sort((a, b) => (b.first === latest) - (a.first === latest) || b.deg - a.deg);
  const placedL = [];
  lab.forEach(n => {
    let near = 0;
    for (const m of view.nodes){
      if (m !== n && Math.abs(m.x - n.x) < 90 && Math.abs(m.y - n.y) < 34) near++;
    }
    if (near > 8 && (n.ghost || n.deg < 4)) return;
    if (near > 4 && n.deg < 3) return;
    const fs = near > 5 ? (n.ghost ? 10 : 11.5) : (n.deg >= 24 ? 13.5 : 12.5);
    const cjk = [...n.term].filter(ch => ch.codePointAt(0) > 0x2E7F).length;
    const tw = (cjk + (n.term.length - cjk) * 0.56) * fs;
    const rx = n.x + 8, ry = n.y - fs * 0.8;
    for (const [px, py, pw, ph] of placedL){
      if (!(rx + tw + 3 < px || px + pw + 3 < rx || ry + fs * 1.15 + 3 < py || py + ph + 3 < ry)) return;
    }
    placedL.push([rx, ry, tw, fs * 1.15]);
    ctx.font = (n.deg >= 24 && near <= 5 ? '600 ' + fs + 'px' : fs + 'px') +
      ' -apple-system,PingFang SC,sans-serif';
    ctx.globalAlpha = n.ghost ? 0.55 : 1;
    ctx.lineWidth = 4; ctx.strokeStyle = '#0d1017';
    ctx.strokeText(n.term, n.x + 8, n.y + 4);
    ctx.fillStyle = n.ghost ? '#9aa6b8' : '#dce3ec';
    ctx.fillText(n.term, n.x + 8, n.y + 4); ctx.globalAlpha = 1;
  });
  if (view.flash && view.nodes === view._flashSet) {}
  if (view.flash){
    const n = view.nodes.find(n => n.id === view.flash);
    if (n){ ctx.beginPath(); ctx.arc(n.x, n.y, 16, 0, 6.284);
      ctx.strokeStyle = '#7dcfff'; ctx.lineWidth = 2.2; ctx.stroke(); }
  }
}

// ---------- 词族太阳图 ----------
function famGeom(){
  const fams = G.families;
  const total = fams.reduce((s, f) => s + f.terms.length, 0);
  let a = -Math.PI / 2;
  const gap = 0.012;
  const span = 2 * Math.PI - gap * fams.length;
  return fams.map(f => {
    const w = span * f.terms.length / total;
    const a0 = a, a1 = a + w; a = a1 + gap;
    return Object.assign({}, f, {a0, a1});
  });
}
function drawFamilies(){
  const geo = famGeom();
  const r0 = 108, r1 = 158, r2 = 408;
  geo.forEach(f => {
    ctx.beginPath(); ctx.arc(CX, CY, r1, f.a0, f.a1); ctx.arc(CX, CY, r0, f.a1, f.a0, true);
    ctx.closePath(); ctx.fillStyle = hoverFam === f.stem ? '#2c3850' : '#232c3f'; ctx.fill();
    const mid = (f.a0 + f.a1) / 2;
    // 词根标签：切向 + 固定"从左向右读"，避免左半边倒置
    let tdx = -Math.sin(mid), tdy = Math.cos(mid);
    if (tdx < 0 || (Math.abs(tdx) < 1e-9 && tdy > 0)){ tdx = -tdx; tdy = -tdy; }
    ctx.save();
    ctx.translate(CX + (r0 + r1) / 2 * Math.cos(mid), CY + (r0 + r1) / 2 * Math.sin(mid));
    ctx.rotate(Math.atan2(tdy, tdx));
    ctx.fillStyle = '#dce3ec'; ctx.font = '600 21px -apple-system,PingFang SC,sans-serif';
    ctx.textAlign = 'center'; ctx.textBaseline = 'middle';
    ctx.lineWidth = 5; ctx.strokeStyle = '#141924';
    ctx.strokeText(f.stem, 0, 0); ctx.fillText(f.stem, 0, 0);
    ctx.restore();
    if (f.terms.length >= 8){
      ctx.save();
      ctx.translate(CX + (r2 + 22) * Math.cos(mid), CY + (r2 + 22) * Math.sin(mid));
      ctx.rotate(Math.atan2(tdy, tdx));
      ctx.fillStyle = '#8b96a5'; ctx.font = '12px -apple-system,PingFang SC,sans-serif';
      ctx.textAlign = 'center'; ctx.textBaseline = 'middle';
      ctx.fillText(f.stem + '·' + f.terms.length, 0, 0);
      ctx.restore();
    }
    const n = f.terms.length;
    f.terms.forEach((t, i) => {
      const ta0 = f.a0 + (f.a1 - f.a0) * i / n, ta1 = f.a0 + (f.a1 - f.a0) * (i + 1) / n;
      ctx.beginPath(); ctx.arc(CX, CY, r2, ta0, ta1); ctx.arc(CX, CY, r1, ta1, ta0, true);
      ctx.closePath();
      ctx.fillStyle = t.color; ctx.globalAlpha = (hoverFam && hoverFam !== f.stem) ? 0.18 : 0.78;
      ctx.fill(); ctx.globalAlpha = 1;
    });
  });
  ctx.beginPath(); ctx.arc(CX, CY, r0 - 12, 0, 6.284); ctx.fillStyle = '#11151d'; ctx.fill();
  ctx.strokeStyle = '#232b3a'; ctx.stroke();
  ctx.textAlign = 'center'; ctx.fillStyle = '#dce3ec';
  ctx.font = '600 24px -apple-system,PingFang SC,sans-serif'; ctx.fillText('词根', CX, CY - 10);
  ctx.fillStyle = '#8b96a5'; ctx.font = '13px sans-serif';
  ctx.fillText(G.families.length + ' 个词族', CX, CY + 16);
  // 词族注记（hover 时）
  if (hoverFamNote){
    ctx.font = '13px -apple-system,PingFang SC,sans-serif'; ctx.fillStyle = '#dce3ec';
    ctx.textAlign = 'center'; ctx.lineWidth = 5; ctx.strokeStyle = '#0d1017';
    const t = hoverFamNote;
    ctx.strokeText(t, CX, H - 40, 1100); ctx.fillText(t, CX, H - 40, 1100);
  }
  // 分类颜色图例（底部一行）
  ctx.font = '12px -apple-system,PingFang SC,sans-serif';
  ctx.textBaseline = 'middle'; ctx.textAlign = 'left';
  let lx0 = CX - 470;
  G.meta.categories.forEach(c => {
    ctx.beginPath(); ctx.arc(lx0, H - 26, 5, 0, 6.284);
    ctx.fillStyle = c.color; ctx.fill();
    ctx.fillStyle = '#8b96a5';
    ctx.fillText(c.name, lx0 + 10, H - 26);
    lx0 += ctx.measureText(c.name).width + 30;
  });
}
let hoverFamNote = null;

// ---------- 详情面板 ----------
function esc(s){ return String(s).replace(/[&<>"]/g, c =>
  ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c])); }
function showSide(id){
  const n = byId[id];
  const rel = (adj[id] || []).slice(0, 40);
  side.innerHTML = `
    <button class="close">✕</button>
    <h2>${esc(n.term)}</h2>
    ${n.aka.length ? `<div class="aka">又作：${n.aka.slice(0, 12).map(esc).join('、')}${n.aka.length > 12 ? '　等 ' + n.aka.length + ' 个' : ''}</div>` : ''}
    <div class="tagline">
      <span class="tag cat" style="background:${n.color}">${n.cat}</span>
      ${n.lang !== 'zh' ? `<span class="tag">${n.lang}</span>` : ''}
      ${n.region ? `<span class="tag">${esc(n.region)}</span>` : ''}
      <span class="tag">${{high:'高',medium:'中','to-verify':'待核实'}[n.conf] || n.conf}置信</span>
      ${n.freq ? `<span class="tag">${n.freq}</span>` : ''}
      ${n.first ? `<span class="tag">${n.first}</span>` : ''}
    </div>
    <h3>释义</h3><p>${esc(n.def)}</p>
    <h3>关联（${rel.length}）</h3>
    <div class="rel">${rel.map(r => `<button data-id="${r}">${esc(byId[r].term)}</button>`).join('') ||
      '<p style="color:var(--dim)">无</p>'}</div>
    <h3>词典条目</h3>
    <p><a href="__DICTMD__#${encodeURIComponent(n.anchor)}" target="_blank">在黑话词典.md 中查看 ↗</a></p>`;
  side.style.display = 'block';
  side.querySelectorAll('.rel button').forEach(b =>
    b.onclick = () => { showSide(b.dataset.id); locate(b.dataset.id); });
  side.querySelector('.close').onclick = () => side.style.display = 'none';
}
function locate(id){
  const n = byId[id];
  if (view.mode === 'overview' || view.mode === 'drill'){
    if (view.mode === 'overview' || view.cat !== n.cat) enterDrill(n.cat, id);
    else view.flash = id;
    draw();
  }
}

// ---------- 搜索 ----------
const search = document.getElementById('search');
search.addEventListener('keydown', e => {
  if (e.key !== 'Enter') return;
  const q = search.value.trim().toLowerCase();
  if (!q) return;
  const m = G.nodes.find(n => n.term.toLowerCase() === q) ||
            G.nodes.find(n => n.term.toLowerCase().includes(q) ||
              n.aka.some(a => a.toLowerCase().includes(q)));
  if (m){ showSide(m.id); locate(m.id); }
  else { search.style.borderColor = '#f7768e'; setTimeout(() => search.style.borderColor = '', 900); }
});

// ---------- 命中检测与交互 ----------
function nodeAt(wx, wy){
  const pool = view.mode === 'drill' ? view.nodes : G.nodes;
  let best = null, bd = 14;
  for (const n of pool){
    const d = Math.hypot(n.x - wx, n.y - wy);
    if (d < bd){ bd = d; best = n; }
  }
  return best;
}
function secAt(wx, wy){
  const dx = wx - CX, dy = wy - CY, r = Math.hypot(dx, dy);
  if (r < 150 || r > 470) return null;
  const a = Math.atan2(dy, dx);
  for (const c of G.meta.categories){
    let aa = a;
    while (aa < -Math.PI / 2) aa += 2 * Math.PI;
    if (aa >= c.a0 && aa <= c.a1) return c;
  }
  return null;
}
function famAt(wx, wy){
  const dx = wx - CX, dy = wy - CY, r = Math.hypot(dx, dy);
  if (r < 108 || r > 408) return null;
  const a = Math.atan2(dy, dx);
  for (const f of famGeom()){
    if (a >= f.a0 && a <= f.a1){
      const n = f.terms.length;
      const i = Math.floor((a - f.a0) / ((f.a1 - f.a0) / n));
      return {fam: f, term: f.terms[Math.min(i, n - 1)]};
    }
  }
  return null;
}
cv.addEventListener('mousemove', e => {
  const rc = cv.getBoundingClientRect();
  const [wx, wy] = toWorld(e.clientX - rc.left, e.clientY - rc.top);
  let html = '';
  if (view.mode === 'families'){
    const hit = famAt(wx, wy);
    hoverFam = hit ? hit.fam.stem : null;
    hoverFamNote = hit ? hit.fam.stem + '：' + hit.fam.note : null;
    cv.style.cursor = hit ? 'pointer' : 'default';
    if (hit) html = `<b>${esc(hit.term.term)}</b><div class="d">${esc(hit.term.cat)}</div>`;
  } else {
    const n = nodeAt(wx, wy);
    hoverNode = n; hoverSec = null;
    if (n){
      cv.style.cursor = 'pointer';
      html = `<b>${esc(n.term)}</b>` + (n.aka.length ? ` <span class="d">又作 ${esc(n.aka.slice(0,3).join('、'))}</span>` : '') +
        `<div class="d">${esc(n.def.slice(0, 72))}${n.def.length > 72 ? '…' : ''}</div>`;
    } else if (view.mode === 'overview'){
      const s = secAt(wx, wy);
      hoverSec = s ? s.name : null;
      cv.style.cursor = s ? 'pointer' : 'default';
      if (s) html = `<b style="color:${s.color}">${esc(s.name)}</b><div class="d">${s.n} 词条 · 点击进入分类子图</div>`;
    } else cv.style.cursor = 'default';
  }
  if (html){
    tip.innerHTML = html; tip.style.display = 'block';
    const tr = stage.getBoundingClientRect();
    tip.style.left = Math.min(e.clientX - tr.left + 16, tr.width - 300) + 'px';
    tip.style.top = Math.min(e.clientY - tr.top + 12, tr.height - 90) + 'px';
  } else tip.style.display = 'none';
  if (view.mode !== 'drill' || !view._tick) draw();
  if (view.mode === 'drill' && view.drag) { view.drag.x = wx; view.drag.y = wy; }
});
cv.addEventListener('mousedown', e => {
  if (view.mode !== 'drill') return;
  const rc = cv.getBoundingClientRect();
  const [wx, wy] = toWorld(e.clientX - rc.left, e.clientY - rc.top);
  const n = nodeAt(wx, wy);
  if (n && !n.ghost){ view.drag = n; view._tick = requestAnimationFrame(function step(){
    draw(); if (view.drag) view._tick = requestAnimationFrame(step);
  }); }
});
window.addEventListener('mouseup', () => {
  if (view.drag){ view.drag = null; startSim(); }
});
cv.addEventListener('click', e => {
  const rc = cv.getBoundingClientRect();
  const [wx, wy] = toWorld(e.clientX - rc.left, e.clientY - rc.top);
  if (view.mode === 'families'){
    const hit = famAt(wx, wy);
    if (hit) showSide(hit.term.id);
    return;
  }
  const n = nodeAt(wx, wy);
  if (n){ showSide(n.id); return; }
  if (view.mode === 'overview'){
    const s = secAt(wx, wy);
    if (s) enterDrill(s.name);
  }
});
window.addEventListener('keydown', e => { if (e.key === 'Escape' && view.mode === 'drill') exitDrill(); });

edgeSlider.addEventListener('input', () => {
  document.getElementById('edgeVal').textContent =
    Math.round((view.mode === 'drill' ? view.sub.length : G.meta.n_backbone) * edgeSlider.value / 100) + ' 条';
  draw();
});
document.querySelectorAll('.tab').forEach(t => t.onclick = () => {
  document.querySelectorAll('.tab').forEach(x => x.classList.remove('on'));
  t.classList.add('on');
  const m = t.dataset.mode;
  if (m === 'families'){ view.mode = 'families'; crumb.style.display = 'none'; edgeCtl.style.display = 'none'; }
  else exitDrill();
  draw();
});

document.getElementById('stats').textContent =
  G.meta.n_entries + ' 词条 · ' + G.meta.n_edges + ' 关联 · ' + G.meta.categories.length + ' 分类';
fit();
const idParam = new URLSearchParams(location.search).get('id');
if (idParam && byId[idParam]){ showSide(idParam); locate(idParam); }
</script>
</body>
</html>
'''


def main():
    entries = load_entries()
    g = build_graph(entries)

    bad = [n['id'] for n in g['nodes'] if not (0 <= n['x'] <= W and 0 <= n['y'] <= H)]
    if bad:
        raise SystemExit(f'布局越界: {bad[:5]}')

    os.makedirs(DOCS, exist_ok=True)
    with open(os.path.join(DOCS, 'graph.json'), 'w', encoding='utf-8') as f:
        json.dump(g, f, ensure_ascii=False, separators=(',', ':'))
    html = (HTML.replace('__GRAPH__', json.dumps(g, ensure_ascii=False, separators=(',', ':')))
                .replace('__REPO__', REPO_URL).replace('__DICTMD__', DICT_MD))
    with open(os.path.join(DOCS, 'index.html'), 'w', encoding='utf-8') as f:
        f.write(html)
    with open(os.path.join(DOCS, 'graph.svg'), 'w', encoding='utf-8') as f:
        f.write(render_svg(g))

    fam_terms = sum(len(f['terms']) for f in g['families'])
    print(f'图谱：{g["meta"]["n_entries"]} 节点 / {g["meta"]["n_edges"]} 边（骨干 {g["meta"]["n_backbone"]}）'
          f'/ 词族 {len(g["families"])} 个覆盖 {fam_terms} 词条')
    print('已生成 docs/index.html、docs/graph.svg、docs/graph.json')


if __name__ == '__main__':
    main()
