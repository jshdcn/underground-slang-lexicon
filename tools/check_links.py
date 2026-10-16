#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""检查仓库内全部 Markdown 的相对链接与锚点是否有效。

用法：python3 tools/check_links.py
零第三方依赖。有断链时以非零状态退出。
"""
import re, os, sys, glob, urllib.parse, collections

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LINK = re.compile(r'\[([^\]]*)\]\(([^)]+)\)')
HEAD = re.compile(r'^(#{1,6})\s+(.+?)\s*$', re.M)


def anchors(path):
    """GitHub 锚点：标题去 markdown 标记、小写、空格转连字符、去多数标点。"""
    out = set()
    try:
        s = open(path, encoding='utf-8').read()
    except OSError:
        return out
    for _, t in HEAD.findall(s):
        t = re.sub(r'`([^`]*)`', r'\1', t)
        t = re.sub(r'\*\*?([^*]*)\*\*?', r'\1', t)
        t = re.sub(r'\[([^\]]*)\]\([^)]*\)', r'\1', t)
        a = re.sub(r'[^\w一-鿿\- ]', '', t.strip().lower()).replace(' ', '-')
        out.add(a)
    return out


def main():
    os.chdir(ROOT)
    mds = glob.glob('**/*.md', recursive=True)
    cache, bad, total = {}, collections.defaultdict(list), 0
    for md in mds:
        s = open(md, encoding='utf-8').read()
        base = os.path.dirname(md)
        for text, href in LINK.findall(s):
            href = href.strip()
            if href.startswith(('http://', 'https://', 'mailto:')):
                continue
            total += 1
            path, _, frag = href.partition('#')
            path = urllib.parse.unquote(path)
            frag = urllib.parse.unquote(frag)
            if path:
                tgt = os.path.normpath(os.path.join(base, path))
                if not os.path.exists(tgt):
                    bad[md].append('文件不存在: %s  ←「%s」' % (href, text))
                    continue
            else:
                tgt = md
            if frag:
                if tgt not in cache:
                    cache[tgt] = anchors(tgt)
                f = re.sub(r'[^\w一-鿿\- ]', '', frag.lower()).replace(' ', '-')
                if f not in cache[tgt]:
                    bad[md].append('锚点不存在: %s  ←「%s」' % (href, text))
    print('检查 %d 个 Markdown 文件，%d 个内部链接' % (len(mds), total))
    if not bad:
        print('全部有效')
        return 0
    for md, items in bad.items():
        print('\n%s:' % md)
        for x in items:
            print('   -', x)
    return 1


if __name__ == '__main__':
    sys.exit(main())
