#!/usr/bin/env python3
"""
Generate a conversation-timeline HTML for one simulation from
reddit_simulation.db + personas_meta.json.

The DB stores OASIS simulated wall-clock times (created_at), so ordering by
created_at gives the sim timeline (start_hour + round hours). Output goes to
<sim>/timeline.html and is served by scripts/serve_downloads.py.

Usage:
    python scripts/make_conversation_timeline.py --simulation-id sim_masked_s1_n100
"""

import argparse
import html
import json
import os
import re
import sqlite3
import sys
from datetime import datetime

ROOT = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                    'backend', 'uploads', 'simulations')
SCRUB = re.compile(r'(旧個社|旧所属|大山|相模|さぶーる|春雪|見方|ハム|買収|合併|アンケート|調査|尺度)')
TIER_CLS = {'部長': 'tier-bucho', '課長': 'tier-kacho', '一般社員': 'tier-shain'}

CSS = """
body{font-family:-apple-system,'Hiragino Sans',sans-serif;margin:24px;background:#f5f5f7;color:#1d1d1f}
h1{font-size:20px} h2{font-size:16px;margin-top:28px}
.meta{color:#666;font-size:12px}
.controls{margin:12px 0;display:flex;gap:12px;flex-wrap:wrap}
select,input[type=text]{padding:6px;font-size:13px}
.timeline{border-left:2px solid #d2d2d7;margin-left:8px;padding-left:0}
.item{position:relative;margin:10px 0;padding:10px 14px;background:#fff;border-radius:10px;box-shadow:0 1px 2px rgba(0,0,0,.06)}
.item::before{content:'';position:absolute;left:-7px;top:16px;width:12px;height:12px;border-radius:50%;background:#c7c7cc}
.item.comment::before{background:#0a84ff}.item.post::before{background:#34c759}
.item.like::before{background:#ffcc00}.item.dislike::before{background:#ff3b30}
.item.replylike::before{background:#5ac8fa}
.ts{font-size:11px;color:#8e8e93;font-variant-numeric:tabular-nums}
.who{font-weight:600;font-size:13px}
.tier{font-size:11px;padding:1px 6px;border-radius:8px;color:#fff;margin-left:6px;vertical-align:1px}
.tier-bucho{background:#af52de}.tier-kacho{background:#ff9500}.tier-shain{background:#0a84ff}
.body{margin-top:6px;font-size:14px;line-height:1.55;white-space:pre-wrap}
.quote{border-left:3px solid #d2d2d7;margin:6px 0;padding:4px 10px;background:#f2f2f7;border-radius:4px}
.small{font-size:12px;color:#666}
.summary{background:#fff;border-radius:10px;padding:12px 16px;font-size:13px}
.summary td{padding:2px 10px 2px 0}
.filter{display:none}
.item.show{display:block}
h3{font-size:13px;color:#666;margin-top:20px}
.tag{font-size:11px;color:#666}
mark{background:#ffe58f;padding:0 2px}
"""

JS = """
const fSim=document.getElementById('fSim'),fTier=document.getElementById('fTier'),
fThread=document.getElementById('fThread'),fType=document.getElementById('fType'),
fQ=document.getElementById('fQ');
function apply(){
  const q=fQ.value.trim();
  document.querySelectorAll('.item').forEach(el=>{
    let ok=true;
    if(fSim.value!=='all'&&el.dataset.sim!==fSim.value)ok=false;
    if(ok&&fTier.value!=='all'&&el.dataset.tier!==fTier.value)ok=false;
    if(ok&&fThread.value!=='all'&&el.dataset.thread!=='fThread'.value&&el.dataset.thread!==fThread.value)ok=false;
    if(ok&&fThread.value!=='all'&&el.dataset.thread!==fThread.value)ok=false;
    if(ok&&fType.value!=='all'&&!el.classList.contains(fType.value))ok=false;
    if(ok&&q){el.innerHTML=el.innerHTML.replace(/<mark>/g,'').replace(/<\\/mark>/g,'');
      const txt=el.dataset.text||'';if(!txt.toLowerCase().includes(q.toLowerCase()))ok=false;}
    el.classList.toggle('show',ok);
  });
}
[fSim,fTier,fThread,fType].forEach(x=>x.addEventListener('change',apply));
fQ.addEventListener('input',apply);
document.getElementById('count').textContent=document.querySelectorAll('.item').length+' 件';
apply();
"""


def fmt_time(iso):
    if not iso:
        return ''
    t = iso.replace('T', ' ')
    return t[:16]


def build(sim_ids):
    events = []
    for sid in sim_ids:
        d = os.path.join(ROOT, sid)
        con = sqlite3.connect(os.path.join(d, 'reddit_simulation.db'))
        con.row_factory = sqlite3.Row
        m = json.load(open(os.path.join(d, 'personas_meta.json')))
        meta = {p['agent_id']: p for p in m['people']}

        def who(uid):
            p = meta.get(uid, {})
            name = p.get('name') or f"user{uid}"
            tier = p.get('tier', '')
            return name, tier

        for r in con.execute('select post_id,user_id,content,created_at from post order by created_at'):
            name, tier = who(r['user_id'])
            events.append(dict(sim=sid, ts=r['created_at'], kind='post', thread=str(r['post_id']),
                               target='新規投稿', name=name, tier=tier, text=r['content'] or ''))
        for r in con.execute('select comment_id,post_id,user_id,content,created_at from comment order by created_at'):
            name, tier = who(r['user_id'])
            events.append(dict(sim=sid, ts=r['created_at'], kind='comment', thread=str(r['post_id']),
                               target=f"投稿 #{r['post_id']} へ返信", name=name, tier=tier, text=r['content'] or ''))
        for r in con.execute('select user_id,post_id,created_at from like order by created_at'):
            name, tier = who(r['user_id'])
            events.append(dict(sim=sid, ts=r['created_at'], kind='like', thread=str(r['post_id']),
                               target=f"投稿 #{r['post_id']} にいいね", name=name, tier=tier, text=''))
        for r in con.execute('select user_id,comment_id,created_at from comment_like order by created_at'):
            name, tier = who(r['user_id'])
            row = con.execute('select post_id from comment where comment_id=?', (r['comment_id'],)).fetchone()
            thr = str(row['post_id']) if row else '?'
            events.append(dict(sim=sid, ts=r['created_at'], kind='replylike', thread=thr,
                               target=f"コメント #{r['comment_id']} にいいね", name=name, tier=tier, text=''))
        for r in con.execute('select user_id,post_id,created_at from dislike order by created_at'):
            name, tier = who(r['user_id'])
            events.append(dict(sim=sid, ts=r['created_at'], kind='dislike', thread=str(r['post_id']),
                               target=f"投稿 #{r['post_id']} にdislike", name=name, tier=tier, text=''))
        # post 内容を quote 表示用
        posts = {str(r['post_id']): (r['content'] or '') for r in con.execute('select post_id,content from post')}
        con.close()

    events.sort(key=lambda e: e['ts'] or '')

    # summary stats
    by_thread = {}
    for e in events:
        if e['kind'] == 'comment':
            by_thread.setdefault(e['thread'], [0, ''])[0] += 1
    for tid in list(by_thread):
        for e in events:
            if e['kind'] == 'post' and e['thread'] == tid:
                by_thread[tid][1] = (e['text'] or '')[:40]
                break

    rows = []
    for tid, (n, first) in sorted(by_thread.items(), key=lambda kv: -kv[1][0]):
        rows.append(f'<tr><td>スレッド #{html.escape(tid)}</td><td>{n} コメント</td>'
                    f'<td class="small">{html.escape(first)}…</td></tr>')

    items = []
    for e in events:
        cls = e['kind']
        tier = TIER_CLS.get(e['tier'], '')
        q = ''
        if e['kind'] in ('comment',) and e['thread'] in posts_lookup(sim_ids):
            pass
        text = html.escape(e['text'] or '')
        if e['kind'] == 'comment':
            # quote of parent post (thread-level; exact parent not stored in like rows)
            pt = posts_of(e['sim'], e['thread'])
            if pt:
                q = f'<div class="quote small">親投稿: {html.escape(pt[:80])}…</div>'
        items.append(
            f'<div class="item {cls} show" data-sim="{html.escape(e["sim"])}" data-tier="{html.escape(e["tier"])}" '
            f'data-thread="{html.escape(e["thread"])}" data-text="{html.escape(e["text"] or e["target"])}">'
            f'<span class="ts">{fmt_time(e["ts"])}</span> '
            f'<span class="who">{html.escape(e["name"])}</span>'
            f'<span class="tier {tier}">{html.escape(e["tier"])}</span>'
            f'<span class="tag"> → {html.escape(e["target"])}</span>'
            f'{q}<div class="body">{text}</div></div>')

    return events, by_thread, items


def posts_of(sim, thread):
    d = os.path.join(ROOT, sim, 'reddit_simulation.db')
    try:
        con = sqlite3.connect(d)
        row = con.execute('select content from post where post_id=?', (int(thread),)).fetchone()
        con.close()
        return (row[0] or '') if row else ''
    except Exception:
        return ''


def posts_lookup(sim_ids):
    return set()


def render(sim_ids, out_path):
    events, by_thread, items = build(sim_ids)
    con0 = sqlite3.connect(os.path.join(ROOT, sim_ids[0], 'reddit_simulation.db'))
    npost = con0.execute('select count(*) from post').fetchone()[0]
    ncom = con0.execute('select count(*) from comment').fetchone()[0]
    nlike = con0.execute('select count(*) from like').fetchone()[0]
    con0.close()
    sims_opts = ''.join(f'<option value="{s}">{s}</option>' for s in sim_ids)
    tier_opts = ''.join(f'<option value="{t}">{t}</option>' for t in ('部長', '課長', '一般社員'))
    thread_opts = ''.join(f'<option value="{t}">#{t}</option>' for t in sorted(by_thread))
    type_opts = ''.join(f'<option value="{k}">{lab}</option>' for k, lab in
                        (('post', '投稿'), ('comment', 'コメント'), ('like', 'いいね'),
                         ('replylike', 'コメントいいね'), ('dislike', 'dislike')))
    scrub_hits = sum(1 for e in events if SCRUB.search(e['text'] or ''))

    doc = f"""<!DOCTYPE html><html lang="ja"><head><meta charset="utf-8">
<title>会話タイムライン: {html.escape(sim_ids[0])}</title><style>{CSS}</style></head><body>
<h1>エージェント間 会話タイムライン</h1>
<p class="meta">シミュレーション: {html.escape(', '.join(sim_ids))} ／ 生成: {datetime.now().strftime('%Y-%m-%d %H:%M')}<br>
DBの created_at はシミュレーション上の時刻（start_hour + round 時間）でソート済み。
実測: 投稿{npost}・コメント{ncom}・いいね{nlike}・合計{len(events)}イベント ／
マスキング監査 SCRUB語ヒット: {scrub_hits}（0 が正常）</p>
<div class="controls">
<select id="fSim"><option value="all">全 sim</option>{sims_opts}</select>
<select id="fTier"><option value="all">全役職</option>{tier_opts}</select>
<select id="fThread"><option value="all">全スレッド</option>{thread_opts}</select>
<select id="fType"><option value="all">全行動</option>{type_opts}</select>
<input type="text" id="fQ" placeholder="内容検索">
</div>
<p class="small">表示 <span id="count"></span> 件</p>
<h2>スレッド別まとめ</h2>
<table class="summary">{"".join(f'<tr><td>スレッド #{k}</td><td>{v[0]} コメント</td><td class="small">{html.escape(v[1][:40])}…</td></tr>' for k, v in sorted(by_thread.items(), key=lambda kv: -kv[1][0]))}</table>
<h2>タイムライン（古い順）</h2>
<div class="timeline">{''.join(items)}</div>
<script>{JS}</script></body></html>"""
    with open(out_path, 'w', encoding='utf-8') as f:
        f.write(doc)
    print(f'wrote {out_path}: {len(events)} events, scrub hits {scrub_hits}')


def render_markdown(sim_ids, out_path):
    """時系列の会話をマークダウン化（読み上げ・音声変換用）。"""
    events, by_thread, _ = build(sim_ids)
    lines = [f"# 会話タイムライン（{' , '.join(sim_ids)}）", "",
             "各行: `シミュ時刻 | 発信者（役職） → 対象 | 本文`。",
             "本文のないいいね系は対象のみ。音声変換は行順に読み上げれば対話の流れを追える。", "",
             "## 時系列（古い順）", ""]
    for e in events:
        ts = fmt_time(e['ts'])
        who = f"{e['name']}（{e['tier']}）"
        tgt = e['target']
        if e['kind'] in ('post', 'comment'):
            body = (e['text'] or '').replace('\n', ' ')
            lines.append(f"{ts} | {who} → {tgt} | {body}")
        else:
            lines.append(f"{ts} | {who} → {tgt}")
        lines.append('')
    lines.append('## 登場人物・役職')
    lines.append('')
    for sid in sim_ids:
        m = json.load(open(os.path.join(ROOT, sid, 'personas_meta.json')))
        import collections
        tiers = collections.Counter(p['tier'] for p in m['people'])
        lines.append(f"- {sid}: " + ', '.join(f'{t} {n}人' for t, n in tiers.items()))
    lines.append('')
    lines.append('## マスキング監査')
    hits = sum(1 for e in events if SCRUB.search(e['text'] or ''))
    lines.append(f"- 会話本文 SCRUB語ヒット: {hits}（0 が正常・個人/企業は特定不能）")
    with open(out_path, 'w', encoding='utf-8') as f:
        f.write('\n'.join(lines) + '\n')
    print(f'wrote {out_path} ({len(events)} lines)')


def main():
    p = argparse.ArgumentParser()
    p.add_argument('--simulation-id', action='append', required=True)
    p.add_argument('--out')
    args = p.parse_args()
    for sid in args.simulation_id:
        if not os.path.isdir(os.path.join(ROOT, sid)):
            sys.exit(f'no sim dir: {sid}')
    out = args.out or os.path.join(ROOT, args.simulation_id[0], 'timeline.html')
    render(args.simulation_id, out)
    md = out[:-5] + '.md'
    render_markdown(args.simulation_id, md)


if __name__ == '__main__':
    main()