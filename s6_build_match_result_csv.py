#!/usr/bin/env python3
"""生成 FC match_result.csv 式 16 列详表（完全自推导，不依赖任何现成匹配结果）。

数据源（全部从 Demo 原始数据推导）:
  remake2nd_demo/py     日文反编译脚本 -> 台词/行号/说话人 (Cmd_text_00/06/13)
  remake2nd_demo_sc/py  简中反编译脚本 -> 中文翻译（同文件同行号对齐）
  add_struct(array2=[INT(5),...])     -> 附加结构行号（normalized_args 关联，参照 scena_voice_kuro_extractor）
  my_match_result_sc.csv (s4)         -> 匹配结果
  script_data_sc.json                 -> voice_id -> OldScriptId/OldCharacterId
  evo_structure_sc/additional_voice   -> OldVoiceText

用法: python s6_build_match_result_csv.py [sc]
输出: W/match_result_sc_detailed.csv
"""
import ast, json, csv, sys, os, re
from collections import defaultdict, deque, Counter
from synonyms import normalize

from paths import W, resolve, require
GAME = (sys.argv[1].lower() if len(sys.argv) > 1 else 'sc')
assert GAME == 'sc', '目前仅支持 sc'
# 反编译目录可用命令行覆盖: python s6... sc [jp_dir] [sc_dir]（供 run.py 调用）
_args = [a for a in sys.argv[1:] if not a.startswith('-') and a.lower() != 'sc']
JP_DIR = _args[0] if _args and len(_args) >= 1 else os.path.join(W, 'remake2nd_demo', 'py')
SC_DIR = _args[1] if _args and len(_args) >= 2 else os.path.join(W, 'remake2nd_demo_sc', 'py')
# 00/06=普通对话 13=带立绘对话(UNKNOWN_05_13 为反编译别名) 08=分支选项/系统文本
OK_CMDS = ('Cmd_text_00', 'Cmd_text_06', 'Cmd_text_13', 'UNKNOWN_05_13', 'Cmd_text_08')

# ---------- 提取（参照 scena_voice_kuro_extractor + s1 的文本规则） ----------
def node_value(node):
    if isinstance(node, ast.Constant):
        return node.value
    if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id in ('INT', 'FLOAT'):
        if node.args and isinstance(node.args[0], ast.Constant):
            return node.args[0].value
    return ast.unparse(node)

def norm_key(elts):
    """normalized_args: 与 Command 前缀(5,funcid) + 参数值 对齐的键"""
    return ','.join(str(node_value(e)) for e in elts
                    if not (isinstance(e, ast.Call) and getattr(e.func, 'id', '') == 'UNDEF'))

def parse_one(path):
    """解析单个 .py: {'commands': [...], 'addstruct': {key: line}}"""
    tree = ast.parse(open(path, encoding='utf-8').read())
    cmds, adds = [], {}
    for node in ast.walk(tree):
        if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Name)):
            continue
        if node.func.id == 'Command':
            if not node.args or not isinstance(node.args[0], ast.Constant): continue
            ct = node.args[0].value
            if ct not in OK_CMDS or len(node.args) < 2 or not isinstance(node.args[1], ast.List): continue
            elts = node.args[1].elts
            funcid = 13 if ct == 'UNKNOWN_05_13' else int(ct[-2:])
            spk = None; parts = []
            for e in elts:
                if isinstance(e, ast.Call) and getattr(e.func, 'id', '') == 'INT' and isinstance(e.args[0], ast.Constant):
                    if spk is None: spk = e.args[0].value
                elif isinstance(e, ast.Constant) and isinstance(e.value, str):
                    parts.append(e.value)
            text = re.sub(r'<[^>]*>', '', ''.join(parts))
            cmds.append({'line': node.lineno, 'funcid': funcid, 'spk': spk, 'text': text,
                         'key': f'5,{funcid},' + norm_key(elts)})
        elif node.func.id == 'add_struct':
            for kw in node.keywords:
                if kw.arg == 'array2' and isinstance(kw.value, ast.List) and kw.value.elts:
                    e0 = kw.value.elts[0]
                    if (isinstance(e0, ast.Call) and getattr(e0.func, 'id', '') == 'INT'
                            and e0.args and isinstance(e0.args[0], ast.Constant) and e0.args[0].value == 5):
                        adds.setdefault(norm_key(kw.value.elts), node.lineno)
                        break
    cmds.sort(key=lambda c: c['line'])
    return {'commands': cmds, 'addstruct': adds}

CACHE_PATH = os.path.join(W, 's6_extract_cache.json')

def extract_dir_cached(d, cache, tag):
    """带逐文件缓存(mtime+size)的提取；cache 为可变 dict，原位更新"""
    changed = 0
    for fn in sorted(os.listdir(d)):
        if not fn.endswith('.py'): continue
        st = os.stat(os.path.join(d, fn))
        sig = f'{st.st_mtime_ns}:{st.st_size}'
        entry = cache.get(tag, {}).get(fn)
        if entry and entry.get('_sig') == sig:
            continue
        cache.setdefault(tag, {})[fn] = {'_sig': sig, **parse_one(os.path.join(d, fn))}
        changed += 1
    return {fn: {'commands': e['commands'], 'addstruct': e['addstruct']}
            for fn, e in cache.get(tag, {}).items()}, changed

cache = {}
if os.path.exists(CACHE_PATH):
    try:
        cache = json.load(open(CACHE_PATH, encoding='utf-8'))
    except Exception:
        cache = {}
print('提取日文/简中台词(带缓存)...')
jp, ch1 = extract_dir_cached(JP_DIR, cache, 'jp')
sc, ch2 = extract_dir_cached(SC_DIR, cache, 'sc')
if ch1 or ch2 or not os.path.exists(CACHE_PATH):
    json.dump(cache, open(CACHE_PATH, 'w', encoding='utf-8'), ensure_ascii=False)
print(f'  缓存: jp重解析{ch1} sc重解析{ch2} / 各{len(jp)},{len(sc)}文件')

def cf(text):
    return normalize(text).replace('\u3046\u3099', 'う').replace('ヴ', 'う') if text else ''

# ---------- s4 匹配结果 ----------
mine = list(csv.DictReader(open(require('my_match_result_sc.csv'), encoding='utf-8')))
queues_exact, queues_norm = {}, {}
for r in mine:
    queues_exact.setdefault((r['Scene'], r['RemakeVoiceText']), deque()).append(r)
    queues_norm.setdefault((r['Scene'], cf(r['RemakeVoiceText'])), deque()).append(r)
consumed = 0
def take_my_row(scene, text):
    global consumed
    q = queues_exact.get((scene, text))
    if q: consumed += 1; return q.popleft()
    qn = queues_norm.get((scene, cf(text)))
    if qn: consumed += 1; return qn.popleft()
    return None

# ---------- 场景/函数编号解释（审查表直观说明） ----------
CITY = {'Rolent': '洛连特', 'Bose': '波尔斯', 'Ruan': '卢安', 'Zeiss': '蔡斯',
        'Grancel': '格兰赛尔', 'Manoria': '马诺里亚', 'event': '事件演出', 'Event': '事件演出',
        'map': '系统', 'map1': '系统'}
EVO_TYPE = {'T': '主线剧情场景', 'C': '城镇/街区场景', 'E': '事件/演出场景',
            'R': '街道/野外场景', 'A': '系统/特殊场景', 'S': '系统'}
FUNC_PREFIX = [('TK_', '选项/单次对话(编号_地名_说话人)'), ('QS', '测验/问答关卡(SELECT_=选项分支)'),
               ('EV_', '剧情演出'), ('ST_', '剧情流程'), ('TALK_', '对话'), ('LP_', '看板/路牌'),
               ('EVENT_', '事件流程控制'), ('BELF_', '求证/辩证事件'), ('BLACKJACK', '小游戏(21点)'),
               ('CHR_', '人物控制'), ('EMO_', '表情'), ('SOUND_', '音频'), ('CAM_', '镜头'),
               ('DOF_', '景深'), ('AV_', '音频/视角'), ('PTRT_', '注视控制')]

def find_sora():
    for cand in ('SoraVoiceScripts-zhenjian', 'sora-voice-matcher/SoraVoiceScripts'):
        p = os.path.join(W, cand, 'cn.sc', 'py')
        if os.path.isdir(p): return p
    return None

evo_meta = {}   # scene -> (中文注释地名, MapName)
_sora = find_sora()
if _sora:
    for fn in os.listdir(_sora):
        if not fn.endswith('.py'): continue
        cn, mp = '', ''
        raw = open(os.path.join(_sora, fn), 'rb').read()
        try:
            content = raw.decode('utf-8')
        except UnicodeDecodeError:
            content = raw.decode('gbk', errors='replace')
        for i, line in enumerate(content.split('\n')):
            if i > 40: break
            s = line.strip()
            if not cn and s.startswith('#') and 'Function' not in s and 'end' not in s.lower():
                c_ = s.lstrip('#').strip()
                if c_ and c_.lower() != 'other': cn = c_
            if 'MapName' in s and '=' in s:
                mp = s.split('=', 1)[1].strip().strip(',').strip('\'"')
            if cn and mp: break
        evo_meta[fn[:-3]] = (cn, mp)

def evo_scene_cn(escn):
    cn, mp = evo_meta.get(escn) or evo_meta.get(re.sub(r'_\d+$', '', escn), ('', ''))
    if mp in ('Other', 'other', 'a'): mp = ''
    return cn or CITY.get(mp, mp or '未标注')

# Remake场景 -> EVO场景（唯一锚点投票），用于反推地名
remake_st = json.load(open(require('remake_structure_sc.json'), encoding='utf-8'))
evo_occ = defaultdict(list); remake_occ = defaultdict(list)
for sc_, funcs_ in json.load(open(require('evo_structure_sc.json'), encoding='utf-8')).items():
    for fn_, f_ in funcs_.items():
        for lab_, blk_ in f_['blocks'].items():
            for t in blk_:
                if t.get('text'):
                    evo_occ[cf(t['text'])].append(sc_)
for sc_, funcs_ in remake_st.items():
    for fn_, f_ in funcs_.items():
        for lab_, blk_ in f_['blocks'].items():
            for t in blk_:
                remake_occ[cf(t.get('text') or '')].append(sc_)
scene_corr = {}
votes_by_scene = defaultdict(Counter)
for n_, rscenes in remake_occ.items():
    if len(rscenes) == 1 and len(evo_occ.get(n_, [])) == 1:
        votes_by_scene[rscenes[0]][evo_occ[n_][0]] += 1
for rsc, c in votes_by_scene.items():
    escn, n = c.most_common(1)[0]
    if n >= 3: scene_corr[rsc] = escn

def remake_note(scene, func):
    parts = []
    m = re.match(r'mp(\d)', scene)
    if m: parts.append('mp0xxx=序章/系统段' if m.group(1) == '0' else f'mp{m.group(1)}xxx≈第{m.group(1)}章段')
    if scene.endswith('_ev'): parts.append('_ev=影片/事件版')
    if scene.endswith('_extk'): parts.append('_extk=附加对话')
    corr = scene_corr.get(scene)
    if corr: parts.append(f'锚点对应EVO {corr}（{evo_scene_cn(corr)}）')
    for pre, mean in FUNC_PREFIX:
        if func.startswith(pre):
            parts.append(f'{pre}前缀={mean}')
            break
    return '；'.join(parts)

def evo_note(escn):
    if not escn: return ''
    letter = escn[0] if escn[0].isalpha() else '?'
    return f'{letter}={EVO_TYPE.get(letter, "其他")}；{evo_scene_cn(escn)}'

# ---------- Old* 数据 ----------
sd_by_vid = {}
_sd_p = resolve('script_data_sc.json')
for x in (json.load(open(_sd_p, encoding='utf-8')) if _sd_p else []):
    v = x.get('voice_id') or ''
    if v.endswith('V'):
        k = v[:-1]
        if k not in sd_by_vid or (sd_by_vid[k].get('script_id', -1) < 0 <= x.get('script_id', -1)):
            sd_by_vid[k] = x
vid_text = {}
vid_to_evoblk = {}
evo_key = defaultdict(list)     # (char, norm) -> [vid]  供审查表展示被拒候选
for sc_, funcs_ in json.load(open(require('evo_structure_sc.json'), encoding='utf-8')).items():
    for fn_, f_ in funcs_.items():
        for lab_, blk_ in f_['blocks'].items():
            for t in blk_:
                if t.get('voice_id'):
                    vid_text[t['voice_id']] = t['text']
                    vid_to_evoblk[t['voice_id']] = (sc_, fn_, lab_)
                    n = cf(t['text'])
                    if n: evo_key[(t['voice_id'][:3], n)].append(t['voice_id'])
for x in json.load(open(require('additional_voice_sc.json'), encoding='utf-8')):
    vid_text.setdefault(x['voice_id'][:-1], x['text'])

def evo_struct(vid):
    b = vid_to_evoblk.get(vid)
    return f'{b[0]}/{b[1]}/{b[2]}' if b else ''

norm_chars = defaultdict(set)   # norm -> {EVO角色码}
for (c_, n_) in evo_key:
    norm_chars[n_].add(c_)

# ---------- 组装 ----------
COLS = ['RemakeVoiceID', 'RemakeScenaScriptFilename', 'RemakeScenaScriptLineno',
        'RemakeScenaScriptAddStructLineno', 'RemakeScenaScriptTranslationLineno',
        'RemakeScenaScriptTranslationAddStructLineno', 'RemakeFunction', 'RemakeBlock',
        'OldScriptId', 'OldCharacterId', 'OldVoiceFilename', 'MatchType',
        'SpeakerCheck', 'RemakeVoiceCategory', 'RemakeVoiceTranslation',
        'RemakeVoiceText', 'OldVoiceText', 'EvoScene', 'EvoFunction', 'EvoBlock',
        'Annotation']
out = []
stat = Counter()
vid_seq = 0
review_m = {}   # (scene, line) -> 对应的 s4 行（审查表用）
for fn in sorted(jp):
    scene = fn[:-3]
    jp_cmds = jp[fn]['commands']
    sc_cmds = {c['line']: c for c in sc.get(fn, {'commands': []})['commands']}
    sc_adds = sc.get(fn, {'addstruct': {}})['addstruct']
    for c in jp_cmds:
        if not c['text']: continue
        vid_seq += 1
        as_line = jp[fn]['addstruct'].get(c['key'])
        scc = sc_cmds.get(c['line'])
        row = dict.fromkeys(COLS, '')
        row['RemakeVoiceID'] = vid_seq
        row['RemakeScenaScriptFilename'] = scene
        row['RemakeScenaScriptLineno'] = c['line']
        row['RemakeScenaScriptAddStructLineno'] = as_line or ''
        row['RemakeScenaScriptTranslationLineno'] = scc['line'] if scc else ''
        row['RemakeScenaScriptTranslationAddStructLineno'] = (sc_adds.get(scc['key']) if scc else None) or ''
        row['RemakeVoiceCategory'] = 'voice'
        row['RemakeVoiceText'] = c['text']
        row['RemakeVoiceTranslation'] = scc['text'] if scc else ''
        anno = []
        m = take_my_row(scene, c['text'])
        if m is not None:
            review_m[(scene, c['line'])] = m
            row['RemakeFunction'] = m['Function']   # 所属结构函数（TK_/EV_/QS_/ST_…）
            row['RemakeBlock'] = m['Block']         # 所属基本块（Loc_xxx/_entry）
            row['SpeakerCheck'] = m['SpeakerMatch'] if m['SpeakerMatch'] != '对应' else ''
        if m is None:
            row['MatchType'] = 'unmatched'
            anno.append('结构未覆盖')
        else:
            vid = m['MyVoiceId']
            if m['MatchType'] == '唯一' and vid:
                ent = sd_by_vid.get(vid)
                if ent and ent.get('script_id', -1) >= 0:
                    row['MatchType'] = 'matched'
                    row['OldScriptId'] = str(ent['script_id'])
                    row['OldCharacterId'] = ent.get('character_id', '')
                else:
                    row['MatchType'] = 'voiceonly'
                row['OldVoiceFilename'] = 'ch' + vid + 'V'
                row['OldVoiceText'] = vid_text.get(vid, '')
                anno.append(f"结构匹配:{m['MatchType']}({m['Source']}) {m['Block']}")
            elif m['MatchType'] == '多候选':
                cands = [x for x in m['Candidates'].split('|') if x]
                row['MatchType'] = 'multi'
                if cands:
                    row['OldVoiceFilename'] = 'ch' + cands[0] + 'V'
                    row['OldVoiceText'] = vid_text.get(cands[0], '')
                anno.append('结构匹配多候选:' + m['Candidates'])
            else:
                row['MatchType'] = 'unmatched'
                anno.append(f"结构匹配:{m['MatchType']} {m['Block']}")
        row['Annotation'] = '; '.join(anno)
        # EVO 侧结构（additional 语音无脚本结构，留空）
        if row['OldVoiceFilename']:
            b = vid_to_evoblk.get(row['OldVoiceFilename'][2:-1])
            if b:
                row['EvoScene'], row['EvoFunction'], row['EvoBlock'] = b
        stat[row['MatchType']] += 1
        out.append(row)

OUT = os.path.join(W, 'match_result_sc_detailed.csv')
with open(OUT, 'w', newline='', encoding='utf-8') as f:
    w = csv.DictWriter(f, fieldnames=COLS)
    w.writeheader(); w.writerows(out)

# ---------- 说话人校对清单（长表：一行一个候选，经 RemakeVoiceID 关联主表） ----------
REVIEW = os.path.join(W, f'speaker_review_{GAME}.csv')
REVIEW_COLS = ['RemakeVoiceID', 'RemakeScenaScriptFilename', 'RemakeScenaScriptLineno',
               'RemakeFunction', 'RemakeBlock', 'RemakeNote', 'ReviewReason', 'SpeakerChar',
               'RemakeVoiceText', 'RemakeVoiceTranslation',
               'CandRole', 'CandVoiceId', 'CandChar', 'CandVoiceText',
               'CandEvoScene', 'CandEvoFunction', 'CandEvoBlock', 'EvoNote', 'Verdict']

review_rows = []
for r in out:
    if not r['SpeakerCheck']:
        continue
    scene = r['RemakeScenaScriptFilename']
    line = r['RemakeScenaScriptLineno']
    m = review_m[(scene, line)]   # 对应 s4 行(SpeakerChar/Candidates/SpeakerMatch)
    reason = m['SpeakerMatch'].split('(')[0]
    cv = r['OldVoiceFilename'][2:-1] if r['OldVoiceFilename'] else ''
    # 候选集合：当前匹配(若有) + 被拒候选
    entries = []
    if cv:
        entries.append((cv, f'当前匹配({reason})'))
    if reason == '同文本异角色':
        n = cf(r['RemakeVoiceText'])
        others = sorted(norm_chars.get(n, set()) - ({m['SpeakerChar']} if m['SpeakerChar'] else set()))
        for c in others:
            for v in sorted(evo_key[(c, n)]):
                entries.append((v, '被拒候选·同文本异角色'))
    else:
        for v in m['Candidates'].split('|'):
            if v and v != cv:
                entries.append((v, '被拒候选·其他'))
    for v, role in entries:
        b = vid_to_evoblk.get(v)
        review_rows.append({
            'RemakeVoiceID': r['RemakeVoiceID'],
            'RemakeScenaScriptFilename': scene,
            'RemakeScenaScriptLineno': line,
            'RemakeFunction': r['RemakeFunction'],
            'RemakeBlock': r['RemakeBlock'],
            'RemakeNote': remake_note(scene, r['RemakeFunction']),
            'ReviewReason': reason,
            'SpeakerChar': m['SpeakerChar'],
            'RemakeVoiceText': r['RemakeVoiceText'],
            'RemakeVoiceTranslation': r['RemakeVoiceTranslation'],
            'CandRole': role,
            'CandVoiceId': v,
            'CandChar': v[:3],
            'CandVoiceText': (vid_text.get(v) or '').replace('\n', '\\n'),
            'CandEvoScene': b[0] if b else '', 'CandEvoFunction': b[1] if b else '', 'CandEvoBlock': b[2] if b else '',
            'EvoNote': evo_note(b[0]) if b else '',
            'Verdict': '',   # 审查者填写
        })

with open(REVIEW, 'w', newline='', encoding='utf-8') as f:
    w = csv.DictWriter(f, fieldnames=REVIEW_COLS)
    w.writeheader(); w.writerows(review_rows)

leftover = len(mine) - consumed
has_trans = sum(1 for r in out if r['RemakeVoiceTranslation'])
has_add = sum(1 for r in out if r['RemakeScenaScriptAddStructLineno'])
n_review = sum(1 for r in out if r['SpeakerCheck'])
print(f'总行: {len(out)}  (提取自 {len(jp)} 个脚本)')
print(f'MatchType: {dict(stat)}')
print(f'说话人待校对: {n_review} 台词行 -> {len(review_rows)} 候选行(长表) -> {os.path.basename(REVIEW)}')
print(f'中文翻译: {has_trans}/{len(out)} = {has_trans/len(out)*100:.1f}%   add_struct行号: {has_add}')
print(f's4行未消耗: {leftover} (应为0)')
print(f'输出: {OUT}')
