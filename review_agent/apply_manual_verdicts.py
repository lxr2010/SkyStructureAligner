#!/usr/bin/env python3
"""应用人工校对裁定（manual_verdicts_*.jsonl）到 *_detailed_corrected.csv，
产出新的 *_detailed_manual.csv（原表不动），并输出应用摘要。

用法（在 review_agent/ 下或任意目录）:
  python apply_manual_verdicts.py                                # 读最新 manual_verdicts_*.jsonl
  python apply_manual_verdicts.py --verdicts path/to/manual_verdicts_3.jsonl
  python apply_manual_verdicts.py --game fc                      # fc / sc（默认 sc）

应用规则（与网页端导出一致）:
  change  -> MatchType=manual(change)；OldVoiceFilename=ch<vid>V；
             OldCharacterId/OldVoiceText/EvoScene/EvoFunction/EvoBlock 从
             evo_structure(+additional_voice) 反查填充（查不到则置空并记入 skipped）
  novoice -> MatchType=manual(novoice)；Old* 匹配列清空
  confirm -> MatchType=manual(confirm)；匹配不动
  suspect -> 匹配不动（保留原 MatchType），仅 Annotation 追加
  全部    -> Annotation 追加「人工校对[status] voice=… note=…」

同时输出 manual_apply_summary.json（应用统计 + change 明细）。
"""
import argparse
import csv
import glob
import json
import os
import re
import sys
from collections import Counter

HERE = os.path.dirname(os.path.abspath(__file__))          # .../SkyStructureAligner/review_agent
ROOT = os.path.dirname(HERE)
os.environ.setdefault('SKYSA_HOME', os.path.join(ROOT, 'data'))
sys.path.insert(0, ROOT)
from paths import resolve


def norm_vid(voice):
    """ch0010470181V / ch0010470181 / 0010470181 -> 0010470181；v* Remake 名 -> ''（不适用）"""
    v = str(voice or '').strip()
    m = re.match(r'^(?:ch)?(\d{10})(?:V)?$', v, re.I)
    return m.group(1) if m else ''


def load_verdicts(path):
    out = {}
    with open(path, encoding='utf-8') as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            o = json.loads(line)
            rid = str(o.get('RemakeVoiceID', ''))
            if rid and o.get('status') in ('confirm', 'change', 'novoice', 'suspect'):
                out[rid] = o          # 同 ID 多条：后者覆盖（人工记录无重复导出）
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--game', default='sc', choices=['fc', 'sc'])
    ap.add_argument('--verdicts', default=None, help='manual_verdicts_*.jsonl 路径（默认取 data/ 下最新的）')
    args = ap.parse_args()

    suf = '' if args.game == 'fc' else f'_{args.game}'
    det_p = resolve(f'match_result{suf}_detailed_corrected.csv')
    if det_p is None:
        raise SystemExit(f'未找到 match_result{suf}_detailed_corrected.csv')

    if args.verdicts:
        vp = args.verdicts
    else:
        cands = sorted(glob.glob(os.path.join(os.path.dirname(det_p), 'manual_verdicts_*.jsonl')))
        if not cands:
            raise SystemExit('未指定 --verdicts，且 data/ 下没有 manual_verdicts_*.jsonl')
        vp = cands[-1]
        print(f'使用最新裁定文件: {vp}')

    verdicts = load_verdicts(vp)
    det = list(csv.DictReader(open(det_p, encoding='utf-8-sig')))

    # vid -> (scene, func, block, text, speaker)；additional 池兜底（无 speaker）
    vid_info = {}
    evo_p = resolve(f'evo_structure{suf}.json')
    if evo_p:
        for sc, funcs in json.load(open(evo_p, encoding='utf-8')).items():
            for fn, f in funcs.items():
                for lab, blk in f['blocks'].items():
                    for t in blk:
                        if t.get('voice_id'):
                            v = t['voice_id']
                            v = v[2:] if v.startswith('ch') else v
                            v = v[:-1] if v.endswith('V') else v
                            vid_info[v] = (sc, fn, lab, t['text'], t.get('speaker', ''))
    add_p = resolve(f'additional_voice_{args.game}.json')
    if add_p:
        for x in json.load(open(add_p, encoding='utf-8')):
            v = x['voice_id']
            v = v[2:] if v.startswith('ch') else v
            v = v[:-1] if v.endswith('V') else v
            if v:
                vid_info.setdefault(v, ('additional', '', '', x['text'], ''))

    row_by_id = {r['RemakeVoiceID']: r for r in det}
    applied = Counter()
    skipped = []
    changes = []

    for rid, v in verdicts.items():
        r = row_by_id.get(rid)
        if r is None:
            skipped.append({'RemakeVoiceID': rid, 'reason': '主表中无此ID'})
            continue
        st = v['status']
        note = v.get('note', '')
        anno_add = f"人工校对[{st}]" + (f" {v.get('voice','')}" if v.get('voice') else '') + (f" {note}" if note else '')

        if st == 'change':
            vid = norm_vid(v.get('voice'))
            info = vid_info.get(vid)
            r['OldVoiceFilename'] = ('ch' + vid + 'V') if vid else ''
            r['OldCharacterId'] = (info[4] if info and info[4] else '')
            if info:
                r['OldVoiceText'], r['EvoScene'], r['EvoFunction'], r['EvoBlock'] = info[3], info[0], info[1], info[2]
            else:
                r['OldVoiceText'] = r['EvoScene'] = r['EvoFunction'] = r['EvoBlock'] = ''
                skipped.append({'RemakeVoiceID': rid, 'reason': f'voice {vid} 不在EVO结构，文本/定位置空'})
            r['MatchType'] = 'manual(change)'
            changes.append({'RemakeVoiceID': rid, 'prev': v.get('prevVoice', ''), 'new': r['OldVoiceFilename'],
                            'text': r['RemakeVoiceText'], 'note': note})
        elif st == 'novoice':
            for k in ('OldScriptId', 'OldCharacterId', 'OldVoiceFilename', 'OldVoiceText',
                      'EvoScene', 'EvoFunction', 'EvoBlock'):
                r[k] = ''
            r['MatchType'] = 'manual(novoice)'
        elif st == 'confirm':
            r['MatchType'] = 'manual(confirm)'
        # suspect：匹配不动
        r['Annotation'] = (r.get('Annotation', '') + '; ' + anno_add).strip('; ')
        applied[st] += 1

    out_p = os.path.join(os.path.dirname(det_p), f'match_result{suf}_detailed_manual.csv')
    with open(out_p, 'w', encoding='utf-8', newline='') as f:
        w = csv.DictWriter(f, fieldnames=list(det[0].keys()))
        w.writeheader()
        w.writerows(det)

    summary = {'verdicts_file': os.path.abspath(vp), 'applied': dict(applied),
               'total_verdicts': len(verdicts), 'skipped': skipped, 'changes': changes,
               'output': os.path.abspath(out_p)}
    sum_p = os.path.join(os.path.dirname(det_p), 'manual_apply_summary.json')
    json.dump(summary, open(sum_p, 'w', encoding='utf-8'), ensure_ascii=False, indent=1)

    print(f"裁定 {len(verdicts)} 条："
          + '，'.join(f'{k}={v}' for k, v in applied.most_common())
          + (f'；跳过 {len(skipped)} 条' if skipped else ''))
    print(f'输出: {out_p}')
    print(f'摘要: {sum_p}')


if __name__ == '__main__':
    main()
