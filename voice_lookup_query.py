#!/usr/bin/env python3
"""说话人/语音统一查询接口（索引由 s7_build_voice_lookup.py 生成）。

用法:
  python voice_lookup_query.py mp2000_ev 62412              # 行查询(场景 + 日文py行号)
  python voice_lookup_query.py --entity "20700|女性の声"     # 实体查询(说话人ID|显示名)
  python voice_lookup_query.py --list 21000                  # 列出某说话人的所有实体
  python voice_lookup_query.py --shared                      # EVO多人共用前缀
Python:
  from voice_lookup_query import VoiceLookup
  lk = VoiceLookup()                # 默认 sc; VoiceLookup('fc')
  r = lk.lookup('mp2000_ev', 62412)
详细规范: docs/voice_lookup.md
"""
import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from paths import resolve

DEFAULT_GAME = 'sc'


class VoiceLookup:
    def __init__(self, game: str = DEFAULT_GAME):
        p = resolve(f'voice_lookup_index_{game}.json')
        if p is None:
            raise SystemExit(f'未找到 voice_lookup_index_{game}.json —— 先运行: '
                             f'python s7_build_voice_lookup.py --game {game} --py-dir <日文py> [...]')
        self.index = json.load(open(p, encoding='utf-8'))
        self.lines = self.index['lines']
        self.entities = self.index['entities']
        self.evo_shared = set(self.index.get('evo_shared_prefixes', []))

    def lookup(self, file: str, line: int) -> dict:
        rec = self.lines.get(file, {}).get(str(line))
        if rec is None:
            return {'file': file, 'line': line, 'status': 'NOT_FOUND',
                    'note': '该行无对话命令(或场景名错误)'}
        out = {k: rec.get(k) for k in (
            'file', 'line', 'cmd', 'source', 'status', 'speaker_id', 'name_jp',
            'display_name_jp', 'display_name_sc', 'voice_id', 'text_jp', 'text_sc',
            'speaker_note', 'candidates')}
        out = {k: v for k, v in out.items() if v is not None}
        if rec.get('entity_key'):
            out['segment'] = rec.get('evo_group')
            out['entity'] = self._entity_view(rec['entity_key'], file)
        if rec.get('evo_match'):
            em = dict(rec['evo_match'])
            em['prefix_shared'] = em.get('prefix') in self.evo_shared
            out['evo_match'] = em
        return out

    def _entity_view(self, ek: str, file: str) -> dict:
        e = self.entities.get(ek)
        if not e:
            return {'entity_key': ek}
        view = {k: e.get(k) for k in (
            'entity_key', 'speaker_id', 'name_jp', 'display_name_jp', 'display_name_sc',
            'n_lines', 'n_voiced', 'n_groups', 'vote_scope') if e.get(k) is not None}
        if e.get('evo'):
            ev = dict(e['evo'])
            ev['prefix_shared'] = ev['prefix'] in self.evo_shared
            view['evo'] = ev
        if e.get('evo_scene_dependent'):
            m = dict(e['evo_scene_dependent'])
            cur = m.get(file)
            view['evo_scene_dependent'] = m
            view['evo_in_this_scene'] = ({'prefix': cur, 'prefix_shared': cur in self.evo_shared}
                                         if cur else None)
        if e.get('has_multi_shared_group'):
            view['has_multi_shared_group'] = True
        return view

    def entity(self, entity_key: str) -> dict:
        e = self.entities.get(entity_key)
        if not e:
            return {'entity_key': entity_key, 'status': 'NOT_FOUND'}
        return self._entity_view(entity_key, e['files'][0] if e.get('files') else '')

    def list_entities(self, speaker_id=None):
        if speaker_id is None:
            return sorted(self.entities)
        return sorted(k for k in self.entities if k.split('|')[0] == str(speaker_id))

    @property
    def shared_prefixes(self):
        return sorted(self.evo_shared)


def main():
    ap = argparse.ArgumentParser(description='Remake脚本行->说话人/语音情况查询')
    ap.add_argument('file', nargs='?', help='场景名, 如 mp2000_ev')
    ap.add_argument('line', nargs='?', type=int, help='日文py行号')
    ap.add_argument('--entity', help='实体查询: "说话人ID|显示名(日文)"')
    ap.add_argument('--list', dest='list_spk', metavar='说话人ID', help='列出该说话人的实体')
    ap.add_argument('--shared', action='store_true', help='EVO多人共用前缀')
    ap.add_argument('--game', default=DEFAULT_GAME)
    a = ap.parse_args()
    lk = VoiceLookup(a.game)
    if a.shared:
        print(json.dumps(lk.shared_prefixes, ensure_ascii=False))
    elif a.entity:
        print(json.dumps(lk.entity(a.entity), ensure_ascii=False, indent=1))
    elif a.list_spk:
        print(json.dumps(lk.list_entities(int(a.list_spk)), ensure_ascii=False, indent=1))
    elif a.file and a.line is not None:
        print(json.dumps(lk.lookup(a.file, a.line), ensure_ascii=False, indent=1))
    else:
        ap.print_help()


if __name__ == '__main__':
    main()
