#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
WINAMPER 社区源巡检机器人
==========================
读取仓库根目录的 sources.json，对每个接口做一次真实可用性探测：
  · 用一首固定公开歌曲（网易云音乐《晴天》id=186016）请求播放地址
  · 拿到 URL 且能 HEAD/GET 到音频 → alive=true
  · 其余 → alive=false
输出：重写 sources.json（可用的排前面），失败条目不删除只标记，方便人工核查。

用法：python3 tools/probe_sources.py [sources.json]
GitHub Actions 每 6 小时调用一次，结果自动 commit。
"""
import json, socket, sys, time, urllib.request, urllib.error
from datetime import date

PATH = sys.argv[1] if len(sys.argv) > 1 else 'sources.json'
TEST_ID = '186016'                      # 周杰伦《晴天》（兜底 id）
TEST_KEYWORD = '晴天'                    # 动态搜索关键词（优先用搜索结果里的 id）
UA = 'winamper-probe/1.0 (github-actions; winamper-player)'
TIMEOUT = 12


def http_get(url, timeout=TIMEOUT):
    req = urllib.request.Request(url, headers={'User-Agent': UA, 'Accept': '*/*'})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read()


def probe(base, kind):
    """返回 True/False 表示该接口当前是否可用。
    探测分两步：先搜索拿一个真实存在的歌曲 id（固定 id 会因下架而误报），
    再用该 id 请求播放地址，最后校验返回的确实是音频。"""
    # 1) 搜索拿动态 id
    song_id = None
    try:
        kw = urllib.request.quote(TEST_KEYWORD)
        data = json.loads(http_get(base + '?types=search&source=netease&count=1&name=' + kw).decode('utf-8', 'ignore'))
        if isinstance(data, list) and data and data[0].get('id'):
            song_id = str(data[0]['id'])
    except Exception:
        pass
    if not song_id:
        song_id = TEST_ID                      # 搜索失败时退回固定 id
    # 2) 取播放地址
    tries = []
    if kind == 'gd':
        tries.append(base + '?types=url&source=netease&id=' + song_id + '&bitrate=128')
    tries.append(base + '?types=url&source=netease&id=' + song_id)
    if kind == 'meting':
        tries.append(base + '?server=netease&type=url&id=' + song_id)
    play_url = None
    for u in tries:
        try:
            data = json.loads(http_get(u).decode('utf-8', 'ignore'))
            if isinstance(data, dict) and data.get('url'):
                play_url = data['url']; break
            if isinstance(data, list) and data and data[0].get('url'):
                play_url = data[0]['url']; break
        except Exception:
            continue
    if not play_url:
        return False
    # 2) 校验拿到的真是音频（只拉头部 64KB）
    try:
        req = urllib.request.Request(play_url, headers={'User-Agent': UA, 'Range': 'bytes=0-65535'})
        with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
            head = r.read(65536)
        ctype = (r.headers.get('Content-Type') or '').lower()
        if not head:
            return False
        if 'audio' in ctype or 'mpeg' in ctype or 'octet-stream' in ctype:
            return True
        # Content-Type 不规范时看文件头特征（ID3 / ftyp / OggS / fLaC）
        return head[:3] == b'ID3' or head[4:8] == b'ftyp' or head[:4] == b'OggS' or head[:4] == b'fLaC'
    except Exception:
        return False


def main():
    socket.setdefaulttimeout(TIMEOUT)
    with open(PATH, encoding='utf-8') as f:
        data = json.load(f)
    srcs = data.get('sources', [])
    changed = 0
    for s in srcs:
        url = s.get('url', '')
        if not url:
            s['alive'] = False
            continue
        ok = probe(url, s.get('kind', 'auto'))
        was = s.get('alive')
        s['alive'] = ok
        if was is not ok:
            changed += 1
        print(('OK  ' if ok else 'DEAD') + '  ' + url)
        time.sleep(1.5)                   # 温和一点，别把人家接口打疼
    srcs.sort(key=lambda x: 0 if x.get('alive') else 1)
    data['sources'] = srcs
    data['updatedAt'] = date.today().isoformat()
    data['lastProbe'] = {'at': time.strftime('%Y-%m-%d %H:%M:%S'), 'changed': changed,
                         'alive': sum(1 for s in srcs if s.get('alive')), 'total': len(srcs)}
    with open(PATH, 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    print('\nsummary: %d/%d alive, %d changed -> %s' % (
        data['lastProbe']['alive'], len(srcs), changed, PATH))
    # 探测结果写进文件，供 Actions 判断是否需要 commit
    with open('probe_result.txt', 'w') as f:
        f.write('%d\n' % changed)


if __name__ == '__main__':
    main()
