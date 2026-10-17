#!/usr/bin/env python3
"""从 data/entries/*.json 构建词条关系图谱。

产出（均为构建生成物，勿手改）：
- docs/index.html  单文件交互式力导向图谱（数据内联，离线可用）
- docs/graph.svg   静态预览图（README 内嵌用）
- docs/graph.json  图数据（供外部工具复用）

布局为种子固定的确定性力导向（Fruchterman-Reingold + 网格分桶斥力），
同一份词条数据在任何机器上构建结果一致。零第三方依赖。
"""
import json
import math
import os
import random
from collections import Counter, defaultdict

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ENTRY_DIR = os.path.join(ROOT, 'data', 'entries')
DOCS = os.path.join(ROOT, 'docs')
REPO_URL = 'https://github.com/threathunterX/underground-slang-lexicon'
DICT_MD = REPO_URL + '/blob/main/dict/%E9%BB%91%E8%AF%9D%E8%AF%8D%E5%85%B8.md'

W, H = 1600, 1000          # 画布逻辑坐标
SEED = 20261017            # 固定种子：同数据同布局
ITERS = 220

PALETTE = [
    '#e4572e', '#17a398', '#4f6d7a', '#ffb302', '#7b6d8d',
    '#a13d63', '#3c9d9b', '#c97b84', '#5d8aa8',
]

FREQ_RANK = {'常见': 0, '较常见': 1, '少见': 2, None: 3}
FREQ_R = {'常见': 9.0, '较常见': 7.0, '少见': 5.5, None: 5.0}


def load_entries():
    entries = []
    for fn in sorted(os.listdir(ENTRY_DIR)):
        if fn.endswith('.json'):
            entries.extend(json.load(open(os.path.join(ENTRY_DIR, fn), encoding='utf-8')))
    return entries


def build_graph(entries):
    cats = []
    for e in entries:
        if e['category'] not in cats:
            cats.append(e['category'])
    color = {c: PALETTE[i % len(PALETTE)] for i, c in enumerate(cats)}

    deg = Counter()
    edges = set()
    for e in entries:
        for r in e.get('related') or []:
            edges.add(tuple(sorted((e['id'], r))))
            deg[e['id']] += 1
            deg[r] += 1
    versions = sorted({e.get('first_seen') for e in entries if e.get('first_seen')})

    nodes = []
    by_id = {e['id']: e for e in entries}
    for e in entries:
        nodes.append({
            'id': e['id'],
            'term': e['term'],
            'cat': e['category'],
            'color': color[e['category']],
            'lang': e.get('lang', 'zh'),
            'region': e.get('region', ''),
            'conf': e['confidence'],
            'freq': e.get('frequency', ''),
            'first': e.get('first_seen', ''),
            'deg': deg[e['id']],
            'aka': (e.get('aka') or [])[:6],
            'def': (e.get('definition') or '')[:160],
        })
    edges = [list(p) for p in sorted(edges) if p[0] in by_id and p[1] in by_id]
    return {
        'meta': {
            'n_entries': len(entries),
            'n_edges': len(edges),
            'categories': [{'name': c, 'color': color[c],
                            'n': sum(1 for e in entries if e['category'] == c)} for c in cats],
            'latest_version': versions[-1] if versions else '',
        },
        'nodes': nodes,
        'edges': edges,
    }


def layout(g):
    """种子固定的 Fruchterman-Reingold，网格分桶近似斥力。"""
    rnd = random.Random(SEED)
    idx = {n['id']: i for i, n in enumerate(g['nodes'])}
    n = len(g['nodes'])

    # 分类锚点：九分类在椭圆上各有锚位，分类内引力形成视觉分区
    cats = [c['name'] for c in g['meta']['categories']]
    anchor = {}
    for ci, c in enumerate(cats):
        ang = 2 * math.pi * (ci + 0.5) / max(len(cats), 1)
        anchor[c] = (W / 2 + 0.42 * W / 2 * math.cos(ang),
                     H / 2 + 0.42 * H / 2 * math.sin(ang))
    anchor_i = [anchor[nd['cat']] for nd in g['nodes']]

    x = [0.0] * n
    y = [0.0] * n
    for i, nd in enumerate(g['nodes']):
        ax, ay = anchor[nd['cat']]
        x[i] = ax + (rnd.random() - 0.5) * 140
        y[i] = ay + (rnd.random() - 0.5) * 140

    adj = defaultdict(list)
    for a, b in g['edges']:
        adj[idx[a]].append(idx[b])
        adj[idx[b]].append(idx[a])

    k = math.sqrt(W * H / max(n, 1)) * 0.55
    deg = [len(adj[i]) for i in range(n)]
    t0 = W * 0.05
    for it in range(ITERS):
        t = t0 * (1 - it / ITERS) ** 1.3 + 1.0
        dx = [0.0] * n
        dy = [0.0] * n
        cell = 2.6 * k
        grid = defaultdict(list)
        for i in range(n):
            grid[(int(x[i] / cell), int(y[i] / cell))].append(i)
        for i in range(n):
            gx, gy = int(x[i] / cell), int(y[i] / cell)
            for ox in (-1, 0, 1):
                for oy in (-1, 0, 1):
                    for j in grid.get((gx + ox, gy + oy), ()):
                        if j <= i:
                            continue
                        ddx = x[i] - x[j]
                        ddy = y[i] - y[j]
                        d2 = ddx * ddx + ddy * ddy
                        if d2 < 1e-6:
                            ddx, ddy, d2 = rnd.random() - 0.5, rnd.random() - 0.5, 0.01
                        d = math.sqrt(d2)
                        f = k * k / d
                        ux, uy = ddx / d, ddy / d
                        dx[i] += ux * f; dy[i] += uy * f
                        dx[j] -= ux * f; dy[j] -= uy * f
        for i in range(n):
            for j in adj[i]:
                if j <= i:
                    continue
                ddx = x[i] - x[j]
                ddy = y[i] - y[j]
                d = math.sqrt(ddx * ddx + ddy * ddy) or 0.01
                # 引力按两端度数归一，防止高关联枢纽把全图吸成一团
                f = d * d / k * 2.0 / (deg[i] + deg[j] + 2)
                ux, uy = ddx / d, ddy / d
                dx[i] -= ux * f; dy[i] -= uy * f
                dx[j] += ux * f; dy[j] += uy * f
        for i in range(n):
            ax, ay = anchor_i[i]
            pull = 0.03 if deg[i] >= 3 else 0.055
            dx[i] += (ax - x[i]) * pull
            dy[i] += (ay - y[i]) * pull
            m = math.hypot(dx[i], dy[i])
            if m > t:
                dx[i] *= t / m
                dy[i] *= t / m
            x[i] += dx[i]
            y[i] += dy[i]

    # 归一化铺满画布：力平衡只负责结构，比例由仿射缩放兜底
    mnx, mxx = min(x), max(x)
    mny, mxy = min(y), max(y)
    span_x = max(mxx - mnx, 1e-6)
    span_y = max(mxy - mny, 1e-6)
    margin = 70
    sx = (W - 2 * margin) / span_x
    sy = (H - 2 * margin) / span_y
    s = min(sx, sy)
    ox = (W - span_x * s) / 2
    oy = (H - span_y * s) / 2
    for i in range(n):
        x[i] = ox + (x[i] - mnx) * s
        y[i] = oy + (y[i] - mny) * s

    for i, nd in enumerate(g['nodes']):
        nd['x'] = round(x[i], 1)
        nd['y'] = round(y[i], 1)
    return g


HTML = r'''<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>黑灰产黑话图谱 · underground-slang-lexicon</title>
<style>
:root{
  --bg:#0e1116; --panel:#161b22; --line:#263041; --text:#dce3ec;
  --dim:#8b96a5; --accent:#4cc2ff; --new:#ffd166;
}
*{box-sizing:border-box}
html,body{margin:0;height:100%;background:var(--bg);color:var(--text);
  font:14px/1.6 -apple-system,"PingFang SC","Microsoft YaHei",sans-serif}
#app{display:flex;height:100%}
#stage{flex:1;position:relative;min-width:0}
svg{width:100%;height:100%;display:block;cursor:grab}
svg:active{cursor:grabbing}
#hud{position:absolute;top:14px;left:14px;background:rgba(22,27,34,.92);
  border:1px solid var(--line);border-radius:10px;padding:12px 14px;max-width:340px}
#hud h1{font-size:15px;margin:0 0 2px}
#hud h1 a{color:var(--accent);text-decoration:none}
#hud .sub{color:var(--dim);font-size:12px;margin-bottom:10px}
#search{width:100%;padding:7px 10px;border-radius:8px;border:1px solid var(--line);
  background:#0b0e13;color:var(--text);font-size:13px;outline:none;margin-bottom:10px}
#search:focus{border-color:var(--accent)}
.chips{display:flex;flex-wrap:wrap;gap:5px;margin-bottom:8px}
.chip{font-size:11.5px;padding:2.5px 9px;border-radius:999px;border:1px solid var(--line);
  color:var(--dim);cursor:pointer;user-select:none;background:transparent;transition:all .15s}
.chip.on{color:#0e1116;font-weight:600}
.legend-note{font-size:11px;color:var(--dim)}
.legend-note b{color:var(--new)}
#side{width:340px;background:var(--panel);border-left:1px solid var(--line);
  padding:18px;overflow-y:auto;display:none}
#side h2{margin:2px 0 4px;font-size:19px}
#side .aka{color:var(--dim);font-size:12.5px;margin-bottom:10px}
#side .tagline{display:flex;gap:6px;flex-wrap:wrap;margin-bottom:12px}
.tag{font-size:11px;padding:2px 8px;border-radius:6px;border:1px solid var(--line);color:var(--dim)}
.tag.cat{color:#0e1116;font-weight:600}
#side h3{font-size:12px;color:var(--dim);margin:14px 0 3px;font-weight:600;letter-spacing:.05em}
#side p{margin:0;font-size:13.5px}
#side a{color:var(--accent);text-decoration:none}
#side .rel{display:flex;flex-wrap:wrap;gap:5px}
#side .rel button{font-size:12px;padding:2px 9px;border-radius:6px;border:1px solid var(--line);
  background:transparent;color:var(--text);cursor:pointer}
#side .rel button:hover{border-color:var(--accent)}
#side .close{position:absolute;top:10px;right:12px;background:none;border:none;color:var(--dim);
  font-size:18px;cursor:pointer}
#side{position:relative}
#foot{position:absolute;bottom:12px;left:14px;color:var(--dim);font-size:11px}
#foot a{color:var(--dim)}
@media (max-width:760px){#side{width:78vw}#hud{max-width:62vw}}
</style>
</head>
<body>
<div id="app">
  <div id="stage">
    <div id="hud">
      <h1><a href="__REPO__" target="_blank">黑灰产黑话图谱</a></h1>
      <div class="sub" id="subtitle"></div>
      <input id="search" placeholder="搜索词条 / 别名…">
      <div class="chips" id="chips"></div>
      <div class="legend-note">节点大小＝关联度　<b>描边</b>＝最新版本新增　点击节点看详情</div>
    </div>
    <svg id="svg"></svg>
    <div id="foot">由 <a href="__REPO__/blob/main/tools/build_viz.py" target="_blank">tools/build_viz.py</a> 自动生成 · 词典全文见 <a href="__DICTMD__" target="_blank">dict/黑话词典.md</a></div>
  </div>
  <aside id="side"></aside>
</div>
<script>
const G = __GRAPH__;
const SVGNS = 'http://www.w3.org/2000/svg';
const svg = document.getElementById('svg');
const side = document.getElementById('side');
const byId = {}; G.nodes.forEach(n => byId[n.id] = n);
const latest = G.meta.latest_version;

document.getElementById('subtitle').textContent =
  G.meta.n_entries + ' 词条 · ' + G.meta.n_edges + ' 条关联 · ' + G.meta.categories.length + ' 个分类';

const VW = 1600, VH = 1000;
let view = {x: 0, y: 0, k: 1};
const root = document.createElementNS(SVGNS, 'g');
svg.appendChild(root);

function applyView(){
  root.setAttribute('transform', `translate(${view.x},${view.y}) scale(${view.k})`);
}
function fit(){
  const r = svg.getBoundingClientRect();
  const s = Math.min(r.width / VW, r.height / VH) * 0.96;
  view = {k: s, x: (r.width - VW * s) / 2, y: (r.height - VH * s) / 2};
  applyView();
}
window.addEventListener('resize', fit);

// ---- 边 ----
const edgeG = document.createElementNS(SVGNS, 'g');
edgeG.setAttribute('stroke', '#2b3648');
edgeG.setAttribute('stroke-opacity', '0.35');
root.appendChild(edgeG);
G.edges.forEach(([a, b]) => {
  const l = document.createElementNS(SVGNS, 'line');
  l.setAttribute('x1', byId[a].x); l.setAttribute('y1', byId[a].y);
  l.setAttribute('x2', byId[b].x); l.setAttribute('y2', byId[b].y);
  l.setAttribute('stroke-width', 0.6);
  edgeG.appendChild(l);
});

// ---- 节点 ----
const nodeG = document.createElementNS(SVGNS, 'g');
root.appendChild(nodeG);
const FR = {'常见': 9.0, '较常见': 7.0, '少见': 5.5, '': 5.0};
G.nodes.forEach(n => {
  const g = document.createElementNS(SVGNS, 'g');
  const r = FR[n.freq] || 5.5;
  const isNew = n.first && n.first === latest;
  g.append(seenCircle(n, r, isNew));
  const label = document.createElementNS(SVGNS, 'text');
  label.setAttribute('x', n.x + r + 3);
  label.setAttribute('y', n.y + 3.5);
  label.setAttribute('fill', n.deg >= 10 ? '#dce3ec' : '#939eae');
  label.setAttribute('font-size', n.deg >= 24 ? 13 : n.deg >= 10 ? 11.5 : 10);
  label.setAttribute('font-weight', n.deg >= 24 ? 600 : 400);
  label.textContent = n.term;
  label.__node = n;
  g.appendChild(label);
  g.addEventListener('click', ev => { ev.stopPropagation(); showSide(n.id); });
  g.style.cursor = 'pointer';
  nodeG.appendChild(g);
  n.__el = g;
});
function seenCircle(n, r, isNew){
  const c = document.createElementNS(SVGNS, 'circle');
  c.setAttribute('cx', n.x); c.setAttribute('cy', n.y);
  c.setAttribute('r', r);
  c.setAttribute('fill', n.color);
  c.setAttribute('fill-opacity', '0.92');
  if (isNew){
    c.setAttribute('stroke', '#ffd166');
    c.setAttribute('stroke-width', 2.2);
  }
  c.__node = n;
  return c;
}

// ---- 分类过滤 ----
const onCats = new Set(G.meta.categories.map(c => c.name));
const chips = document.getElementById('chips');
G.meta.categories.forEach(c => {
  const b = document.createElement('button');
  b.className = 'chip on';
  b.textContent = c.name + ' ' + c.n;
  b.style.borderColor = c.color;
  b.style.background = c.color;
  b.onclick = () => {
    if (onCats.has(c.name)) onCats.delete(c.name); else onCats.add(c.name);
    b.classList.toggle('on');
    b.style.background = b.classList.contains('on') ? c.color : 'transparent';
    applyFilter();
  };
  chips.appendChild(b);
});
function applyFilter(){
  G.nodes.forEach(n => {
    n.__el.style.display = onCats.has(n.cat) ? '' : 'none';
  });
  G.edges.forEach(([a, b], i) => {
    edgeG.children[i].style.display =
      (onCats.has(byId[a].cat) && onCats.has(byId[b].cat)) ? '' : 'none';
  });
}

// ---- 搜索 ----
const search = document.getElementById('search');
search.addEventListener('input', () => {
  const q = search.value.trim().toLowerCase();
  G.nodes.forEach(n => {
    const hit = !q || n.term.toLowerCase().includes(q) ||
      n.aka.some(a => a.toLowerCase().includes(q)) || n.id.includes(q);
    n.__el.style.opacity = hit ? 1 : 0.08;
  });
  if (q){
    const m = G.nodes.find(n => n.term.toLowerCase() === q) ||
              G.nodes.find(n => n.term.toLowerCase().includes(q));
    if (m) focusNode(m.id, false);
  }
});
svg.addEventListener('click', () => { side.style.display = 'none'; });

// ---- 详情面板 ----
function showSide(id){
  const n = byId[id];
  const rel = (G.edges.filter(([a, b]) => a === id || b === id)
    .map(([a, b]) => (a === id ? b : a)));
  const relBtns = rel.slice(0, 40).map(r =>
    `<button data-id="${r}">${byId[r].term}</button>`).join('');
  side.innerHTML = `
    <button class="close">✕</button>
    <h2>${esc(n.term)}</h2>
    ${n.aka.length ? `<div class="aka">又作：${n.aka.map(esc).join('、')}</div>` : ''}
    <div class="tagline">
      <span class="tag cat" style="background:${n.color}">${n.cat}</span>
      ${n.lang !== 'zh' ? `<span class="tag">${n.lang}</span>` : ''}
      ${n.region ? `<span class="tag">${esc(n.region)}</span>` : ''}
      <span class="tag">${{high:'高',medium:'中','to-verify':'待核实'}[n.conf] || n.conf}置信</span>
      ${n.freq ? `<span class="tag">${n.freq}</span>` : ''}
      ${n.first ? `<span class="tag">${n.first}</span>` : ''}
    </div>
    <h3>释义</h3><p>${esc(n.def)}</p>
    ${n.region ? '' : ''}
    <h3>关联（${rel.length}）</h3>
    <div class="rel">${relBtns || '<p style="color:var(--dim)">无</p>'}</div>
    <h3>词典条目</h3>
    <p><a href="__DICTMD__#${encodeURIComponent(n.term)}" target="_blank">在黑话词典.md 中查看 ↗</a></p>`;
  side.style.display = 'block';
  side.querySelectorAll('.rel button').forEach(b =>
    b.onclick = () => showSide(b.dataset.id));
  side.querySelector('.close').onclick = () => side.style.display = 'none';
  focusNode(id, true);
}
function focusNode(id, pan){
  const n = byId[id];
  const r = svg.getBoundingClientRect();
  const s = Math.max(view.k, 1.1);
  if (pan){
    view = {k: s,
            x: r.width / 2 - n.x * s,
            y: r.height / 2 - n.y * s};
    applyView();
  }
  highlight(id);
}
function highlight(id){
  G.nodes.forEach(n => n.__el.style.opacity = (n.id === id) ? 1 : 0.35);
  setTimeout(() => G.nodes.forEach(n => n.__el.style.opacity = 1), 900);
}
function esc(s){
  return String(s).replace(/[&<>"]/g, c =>
    ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]));
}

// ---- 缩放拖拽 ----
let drag = null;
svg.addEventListener('mousedown', e => { drag = {x: e.clientX, y: e.clientY, vx: view.x, vy: view.y}; });
window.addEventListener('mousemove', e => {
  if (drag){
    view.x = drag.vx + e.clientX - drag.x;
    view.y = drag.vy + e.clientY - drag.y;
    applyView();
  }
});
window.addEventListener('mouseup', () => drag = null);
svg.addEventListener('wheel', e => {
  e.preventDefault();
  const r = svg.getBoundingClientRect();
  const mx = e.clientX - r.left, my = e.clientY - r.top;
  const f = e.deltaY < 0 ? 1.12 : 1 / 1.12;
  const k = Math.min(Math.max(view.k * f, 0.15), 8);
  view.x = mx - (mx - view.x) * (k / view.k);
  view.y = my - (my - view.y) * k / view.k;
  view.k = k;
  applyView();
}, {passive: false});

fit();
const idParam = new URLSearchParams(location.search).get('id');
if (idParam && byId[idParam]) showSide(idParam);
</script>
</body>
</html>
'''


def render_svg(g):
    """静态预览 SVG：标签按度数优先、贪心避让放置，冲突即放弃。"""
    p = []
    p.append(f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {W} {H}" '
             f'font-family="-apple-system,PingFang SC,Microsoft YaHei,sans-serif">')
    p.append(f'<rect width="{W}" height="{H}" fill="#0e1116"/>')
    for a, b in g['edges']:
        na, nb = g['_pos'][a], g['_pos'][b]
        p.append(f'<line x1="{na[0]}" y1="{na[1]}" x2="{nb[0]}" y2="{nb[1]}" '
                 f'stroke="#2b3648" stroke-opacity="0.33" stroke-width="0.6"/>')
    for n in g['nodes']:
        x, y = g['_pos'][n['id']]
        r = FREQ_R.get(n['freq'], 5.0)
        extra = (f' stroke="#ffd166" stroke-width="2.2"' if n['first'] == g['meta']['latest_version'] else '')
        p.append(f'<circle cx="{x}" cy="{y}" r="{r}" fill="{n["color"]}" '
                 f'fill-opacity="0.92"{extra}/>')

    # 标签贪心避让：度数高的先占位，放不下就放弃该标签
    placed = []

    def try_place(lx, ly, w, h):
        pad = 3
        for (px, py, pw, ph) in placed:
            if not (lx + w + pad < px or px + pw + pad < lx or ly + h + pad < py or py + ph + pad < ly):
                return False
        placed.append((lx, ly, w, h))
        return True

    for n in sorted(g['nodes'], key=lambda m: -m['deg']):
        if n['deg'] < 5:
            continue
        x, y = g['_pos'][n['id']]
        fs = 15 if n['deg'] >= 24 else 13 if n['deg'] >= 10 else 12
        w_cjk = sum(1 for ch in n['term'] if ord(ch) > 0x2E7F)
        w = (w_cjk + (len(n['term']) - w_cjk) * 0.56) * fs
        lx = x + FREQ_R.get(n['freq'], 5.0) + 3
        if not try_place(lx, y - fs * 0.8, w, fs * 1.15):
            continue
        p.append(f'<text x="{lx}" y="{y + 3.5}" fill="{"#dce3ec" if n["deg"] >= 10 else "#939eae"}" '
                 f'font-size="{fs}">{_esc(n["term"])}</text>')
    leg_x = 24
    p.append(f'<text x="{leg_x}" y="{H - 76}" fill="#dce3ec" font-size="22" font-weight="600">'
             f'黑灰产黑话图谱</text>')
    p.append(f'<text x="{leg_x}" y="{H - 54}" fill="#8b96a5" font-size="13">'
             f'{g["meta"]["n_entries"]} 词条 · {g["meta"]["n_edges"]} 条关联 · 黄圈为 {g["meta"]["latest_version"]} 新增</text>')
    for i, c in enumerate(g['meta']['categories']):
        col = i % 3
        row = i // 3
        p.append(f'<circle cx="{leg_x + 6 + col * 190}" cy="{H - 30 + row * 20}" r="5" fill="{c["color"]}"/>')
        p.append(f'<text x="{leg_x + 16 + col * 190}" y="{H - 26 + row * 20}" fill="#8b96a5" '
                 f'font-size="12">{_esc(c["name"])} {c["n"]}</text>')
    p.append('</svg>')
    return '\n'.join(p)


def _esc(s):
    return (s.replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;'))


def main():
    entries = load_entries()
    g = build_graph(entries)
    layout(g)
    g['_pos'] = {n['id']: (n['x'], n['y']) for n in g['nodes']}

    bad = [n['id'] for n in g['nodes'] if not (0 <= n['x'] <= W and 0 <= n['y'] <= H)]
    if bad:
        raise SystemExit(f'布局越界: {bad[:5]}')

    os.makedirs(DOCS, exist_ok=True)
    slim = {'meta': g['meta'], 'nodes': g['nodes'], 'edges': g['edges']}
    with open(os.path.join(DOCS, 'graph.json'), 'w', encoding='utf-8') as f:
        json.dump(slim, f, ensure_ascii=False, separators=(',', ':'))

    html = (HTML.replace('__GRAPH__', json.dumps(slim, ensure_ascii=False, separators=(',', ':')))
                .replace('__REPO__', REPO_URL)
                .replace('__DICTMD__', DICT_MD))
    with open(os.path.join(DOCS, 'index.html'), 'w', encoding='utf-8') as f:
        f.write(html)

    with open(os.path.join(DOCS, 'graph.svg'), 'w', encoding='utf-8') as f:
        f.write(render_svg(g))

    iso = [n['id'] for n in g['nodes'] if n['deg'] == 0]
    print(f'图谱：{g["meta"]["n_entries"]} 节点 / {g["meta"]["n_edges"]} 边 / 孤立 {len(iso)}')
    print('已生成 docs/index.html、docs/graph.svg、docs/graph.json')


if __name__ == '__main__':
    main()
