#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""抓取网易云 / 酷狗公开榜单，产出 charts.json（同源静态数据）。

为什么要有这个脚本：
  两个榜单接口都不发 Access-Control-Allow-Origin，网页版 fetch 必被拦；
  公共 CORS 代理（allorigins 等）已全部失效，这条路走不通。
  所以改成「构建期抓一次 → 存成同源 JSON → 网页直接读」：
    · 同源请求没有跨域问题，也不依赖任何第三方代理
    · 榜单本身就是日更，一天抓一次完全够
    · APK 里仍走原生 HttpPlugin 直连，更实时，不受影响

产物结构（前端 src/js/15-chart.js 直接消费，无需再解析原始接口）：
  {
    "updatedAt": "2026-11-07T09:00:00Z",
    "sources": {
      "netease": {"charts":[{"id","name","sub","songs":[{"name","art"}]}]},
      "kugou":   {"charts":[...]}
    }
  }

用法：
  python3 tools/fetch_charts.py [输出路径] [--max-charts N] [--max-songs N]
"""

import json
import os
import ssl
import sys
import time
import urllib.request
import urllib.parse
from datetime import datetime, timezone

UA = 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120 Safari/537.36'

# 这些榜单一来价值低、二来经常抓空，直接跳过，省请求也省体积
SKIP_WORDS = ('小天才', '音乐合伙人', '留名榜')

DEFAULT_MAX_CHARTS = 24      # 每个源最多保留多少个榜单
DEFAULT_MAX_SONGS = 100      # 每个榜单最多保留多少首

# 酷狗 https 证书与域名不匹配，只能用 http；
# 这里只在 CI / 本地构建时跑，产物是 https 同源 JSON，不影响网页的混合内容限制。
CTX = ssl.create_default_context()
CTX.check_hostname = False
CTX.verify_mode = ssl.CERT_NONE


def get_json(url, retries=2):
    last = None
    for i in range(retries + 1):
        try:
            req = urllib.request.Request(url, headers={
                'User-Agent': UA,
                'Referer': 'https://music.163.com/',
                'Accept': 'application/json, text/plain, */*',
            })
            with urllib.request.urlopen(req, timeout=20, context=CTX) as r:
                raw = r.read()
            return json.loads(raw.decode('utf-8', 'replace'))
        except Exception as e:                     # noqa: BLE001
            last = e
            if i < retries:
                time.sleep(1.2 * (i + 1))
    print('   ! 请求失败 %s → %s' % (url[:90], last), file=sys.stderr)
    return None


# ---------------- 网易云 ----------------
def netease_charts(max_charts, max_songs):
    j = get_json('https://music.163.com/api/toplist')
    if not j:
        return []
    raw = j.get('list') or []
    picked = []
    for x in raw:
        name = (x.get('name') or '').strip()
        cid = str(x.get('id') or '')
        if not cid or not name:
            continue
        if any(w in name for w in SKIP_WORDS):
            continue
        picked.append({'id': cid, 'name': name, 'sub': x.get('updateFrequency') or ''})
        if len(picked) >= max_charts:
            break

    out = []
    for c in picked:
        d = get_json('https://music.163.com/api/playlist/detail?id=' + urllib.parse.quote(c['id']))
        tracks = (((d or {}).get('result') or {}).get('tracks') or [])[:max_songs]
        songs = []
        for t in tracks:
            nm = (t.get('name') or '').strip()
            if not nm:
                continue
            ar = '/'.join([(a.get('name') or '').strip()
                           for a in (t.get('artists') or []) if a.get('name')])
            songs.append({'name': nm, 'art': ar})
        if not songs:
            print('   - 跳过空榜单: %s' % c['name'], file=sys.stderr)
            continue
        c = dict(c, songs=songs)
        out.append(c)
        print('   ✓ 网易云 %-12s %3d 首' % (c['name'], len(songs)))
        time.sleep(0.25)
    return out


# ---------------- 酷狗 ----------------
def kugou_charts(max_charts, max_songs):
    j = get_json('http://mobilecdn.kugou.com/api/v3/rank/list?format=json')
    if not j:
        return []
    raw = (((j.get('data') or {}).get('info')) or [])
    picked = []
    for x in raw:
        rid = str(x.get('rankid') or '')
        name = (x.get('rankname') or '').strip()
        if not rid or not name or any(w in name for w in SKIP_WORDS):
            continue
        picked.append({'id': rid, 'name': name, 'sub': ''})
        if len(picked) >= max_charts:
            break

    out = []
    for c in picked:
        u = ('http://mobilecdn.kugou.com/api/v3/rank/song?rankid=%s&page=1&pagesize=%d'
             % (urllib.parse.quote(c['id']), max_songs))
        d = get_json(u)
        info = (((d or {}).get('data') or {}).get('info')) or []
        songs = []
        for t in info:
            fn = t.get('filename') or ''
            nm = (t.get('songname') or (fn.split(' - ', 1)[1] if ' - ' in fn else fn)).strip()
            if not nm:
                continue
            ar = '/'.join([(a.get('author_name') or '').strip()
                           for a in (t.get('authors') or []) if a.get('author_name')])
            if not ar and ' - ' in fn:
                ar = fn.split(' - ', 1)[0].strip()
            songs.append({'name': nm, 'art': ar})
        if not songs:
            print('   - 跳过空榜单: %s' % c['name'], file=sys.stderr)
            continue
        c = dict(c, songs=songs)
        out.append(c)
        print('   ✓ 酷狗   %-12s %3d 首' % (c['name'], len(songs)))
        time.sleep(0.25)
    return out


def main():
    out_path = 'charts.json'
    max_charts, max_songs = DEFAULT_MAX_CHARTS, DEFAULT_MAX_SONGS
    args = sys.argv[1:]
    if args and not args[0].startswith('-'):
        out_path = args.pop(0)
    i = 0
    while i < len(args):
        if args[i] == '--max-charts':
            max_charts = int(args[i + 1]); i += 2
        elif args[i] == '--max-songs':
            max_songs = int(args[i + 1]); i += 2
        else:
            i += 1

    print('› 抓取榜单 …')
    ne = netease_charts(max_charts, max_songs)
    kg = kugou_charts(max_charts, max_songs)

    data = {
        'updatedAt': datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ'),
        'sources': {
            'netease': {'charts': ne},
            'kugou': {'charts': kg},
        },
    }
    # 两个源都空说明整条链路挂了，此时不要覆盖旧文件（宁可留昨天的数据）
    if not ne and not kg:
        print('✘ 两个源都没取到数据，保留既有 charts.json 不覆盖', file=sys.stderr)
        return 1

    body = json.dumps(data, ensure_ascii=False, separators=(',', ':'))
    with open(out_path, 'w', encoding='utf-8') as f:
        f.write(body)
    n_songs = sum(len(c['songs']) for s in data['sources'].values() for c in s['charts'])
    print('✔ 已写出 %s：%d 个榜单 / %d 首 / %.0f KB'
          % (out_path, len(ne) + len(kg), n_songs, len(body.encode('utf-8')) / 1024))
    return 0


if __name__ == '__main__':
    sys.exit(main())
