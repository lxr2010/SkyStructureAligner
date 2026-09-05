#!/usr/bin/env python3
"""s7 一键生成 说话人/语音统一查询索引 voice_lookup_index_{game}.json。

从零开始（仅依赖：反编译 py 目录 + 可选游戏目录/表文件）：
  1. 下载 Release 数据资产(evo_structure/script_data/speaker_map/additional_voice)
  2. s1 重建 Remake 结构(带说话人不确定性/显示名时间线) -> s2 --prefix-stats(EVO前缀归属)
     -> derive_speaker_map -> s4 匹配 -> s6 详表   [--skip-pipeline 跳过, 复用已有结果]
  3. 从 table.pac 解 t_name.tbl(说话人ID->名; 无游戏文件则降级为无名模式)
  4. 解析 日文/简中 py: Cmd_text说话人 + chr_set_display_name时间线 + VAR调用点回溯
     + add_struct数据表对话(vals[0]==5家族, 说话人=vals[2]) 与内联镜像去重
  5. 场景×角色段两级EVO投票(标志性台词 + 全局char_id>=0x100):
     段内分裂=多人共用前缀; 跨场景不一致=scene_dependent(合法,保留per-scene映射)

用法:
  uv run python s7_build_voice_lookup.py --game sc --py-dir <日文py> [--py-dir-sc <简中py>]
      [--game-dir "<游戏目录>"] [--skip-download] [--skip-pipeline]
      # --game-dir 指向含 pac/steam/table.pac 的游戏安装目录; 也接受 --table-pac 直接给路径

产物(data/):
  voice_lookup_index_{game}.json   统一查询索引(voice_lookup_query.py / rt.py speaker 使用)
  speaker_names_t_name_{game}.json t_name 映射(中间件)
  evo_prefix_stats_{game}.json     EVO前缀归属统计(s2)
"""
import argparse, ast, collections, csv, glob, json, os, re, struct, subprocess, sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from paths import W, resolve, require
import evo_speaker_info as evo_speaker

PY = sys.executable


def run_step(cmd, desc):
    print(f'>>> [{desc}] {" ".join(cmd)}')
    r = subprocess.run(cmd, cwd=HERE)
    if r.returncode != 0:
        sys.exit(f'步骤失败: {desc}')


def ensure_speaker_data(game):
    p = os.path.join(HERE, 'data', f'evo_speaker_names_{game}.json')
    if not os.path.exists(p):
        run_step([PY, 'build_evo_bank_index_pure.py'], '重建EVO说话人知识库(pure版)')

def download_assets(game, skip=False):
    from run import ASSETS, BASE, download
    if skip:
        return
    for name in ASSETS.get(game, []):
        download(name)


# ---------- FPAC / t_name ----------
def fpac_extract(fpac_path, want_name):
    """从 FPAC 包中取出单个文件内容"""
    with open(fpac_path, 'rb') as f:
        if f.read(4) != b'FPAC':
            raise ValueError(f'{fpac_path} 不是 FPAC')
        count, _, _ = struct.unpack('<3I', f.read(12))
        entries_pos = f.tell()
        files = []
        for i in range(count):
            f.seek(entries_pos + i * 32)
            _h, noff, size, loc = struct.unpack('<4Q', f.read(32))
            f.seek(noff)
            name = b''
            while True:
                c = f.read(1)
                if c in (b'\0', b''):
                    break
                name += c
            files.append((name.decode(), loc, size))
    for name, loc, size in files:
        if name.endswith(want_name):
            with open(fpac_path, 'rb') as f:
                f.seek(loc)
                return f.read(size)
    return None


def parse_t_name(tbl_bytes):
    """t_name.tbl -> {id: {jp,name...}}(104字节/行=13×u64, [0]=ID [1]=名offset...; 65535变体行剔除)"""
    if not tbl_bytes or tbl_bytes[:4] != b'#TBL':
        return None
    count, = struct.unpack_from('<I', tbl_bytes, 4)
    off = 8
    start = length = ecount = None
    for _ in range(count):
        start, length, ecount = struct.unpack_from('<3I', tbl_bytes, off + 68)
        off += 80

    def s(o):
        e = tbl_bytes.index(b'\0', o)
        return tbl_bytes[o:e].decode('utf-8', 'replace')

    m = {}
    for e in range(ecount):
        row = struct.unpack_from('<13Q', tbl_bytes, start + e * length)
        cid = row[0]
        if cid == 65535:      # ~400行服装变体复用此ID, 与对话无关
            continue
        m[cid] = {'jp': s(row[1]), 'model': s(row[2]), 'face': s(row[3])}
    return m


def load_t_name(args, game):
    """优先级: --table-pac > --game-dir/pac/steam/table.pac > 旧索引复用 > 降级无名"""
    out_p = os.path.join(W, f'speaker_names_t_name_{game}.json')
    if os.path.exists(out_p) and not args.rebuild_names:
        return json.load(open(out_p, encoding='utf-8'))
    cand = []
    if args.table_pac:
        cand.append(args.table_pac)
    if args.game_dir:
        cand.append(os.path.join(args.game_dir, 'pac', 'steam', 'table.pac'))
    for pac in cand:
        if os.path.exists(pac):
            try:
                m = parse_t_name(fpac_extract(pac, 't_name.tbl'))
                if m:
                    json.dump(m, open(out_p, 'w', encoding='utf-8'), ensure_ascii=False)
                    print(f't_name: {len(m)} 个角色名 <- {pac}')
                    return m
            except Exception as e:
                print(f'警告: 解析 {pac} 失败: {e}')
    print('警告: 无 table.pac -> 说话人名称降级为空(仅保留ID); '
          '传 --game-dir "<游戏目录>" 可补全')
    return {}


# ---------- py 解析 ----------
DIALOG_CMDS = ('Cmd_text_00', 'Cmd_text_06', 'Cmd_text_08', 'Cmd_text_13', 'UNKNOWN_05_13')
FN_RE = re.compile(r'set_current_function\("([^"]+)"\)')


def _intval(n):
    if isinstance(n, ast.Call) and isinstance(n.func, ast.Name) and n.func.id == 'INT' \
            and n.args and isinstance(n.args[0], ast.Constant):
        return n.args[0].value
    return None


def parse_py(path):
    """-> dialog[(line,cmd,spk,voice,text)], disp_events[(line,spk,name)],
         var_lines[(line,cmd,varname)], assigns[(line,var,int|None)], call_sites,
         add_struct[(line,spk,voice,text)]"""
    src = open(path, encoding='utf-8').read()
    tree = ast.parse(src)
    dialog, disp, varl, assigns, calls, adds = [], [], [], [], collections.defaultdict(list), []
    for node in ast.walk(tree):
        if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Name)):
            continue
        nm = node.func.id
        if nm == 'Command' and node.args and isinstance(node.args[0], ast.Constant) \
                and node.args[0].value in DIALOG_CMDS:
            if len(node.args) < 2 or not isinstance(node.args[1], ast.List):
                continue
            elts = node.args[1].elts
            if not elts:
                continue
            spk = _intval(elts[0])
            voice = None
            if node.args[0].value in ('Cmd_text_06', 'UNKNOWN_05_06'):
                for el in elts[1:6]:
                    v = _intval(el)
                    if v is not None and v >= 10000:
                        voice = v
                        break
            buf = []
            for el in elts[1:]:
                v = _intval(el)
                if v is not None:
                    buf.append('\n' if v == 10 else '')
                elif isinstance(el, ast.Constant) and isinstance(el.value, str) \
                        and not el.value.startswith('<#'):
                    buf.append(el.value)
            dialog.append((node.lineno, node.args[0].value, spk, voice, ''.join(buf).strip()))
            if spk is None and isinstance(elts[0], ast.Call) and elts[0].func.id == 'LoadVar' \
                    and elts[0].args and isinstance(elts[0].args[0], ast.Constant):
                varl.append((node.lineno, node.args[0].value, elts[0].args[0].value))
        elif nm == 'CallFunction' and node.args and isinstance(node.args[0], ast.Constant):
            fname = node.args[0].value
            if fname == 'chr_set_display_name' and len(node.args) > 1 \
                    and isinstance(node.args[1], ast.List) and len(node.args[1].elts) >= 2:
                e0, e1 = node.args[1].elts[0], node.args[1].elts[1]
                if _intval(e0) is not None and isinstance(e1, ast.Constant) and isinstance(e1.value, str):
                    disp.append((node.lineno, _intval(e0), e1.value))
            elif len(node.args) > 1 and isinstance(node.args[1], ast.List):
                args = [_intval(x) for x in node.args[1].elts]
                calls[(fname, tuple(a is not None for a in args))].append(
                    {'file': None, 'line': node.lineno,
                     'args': args})   # file 由调用方回填
        elif nm == 'AssignVar' and node.args and isinstance(node.args[0], ast.Constant):
            val = _intval(node.args[1]) if len(node.args) > 1 else None
            if val is None and len(node.args) > 1 and isinstance(node.args[1], ast.Call) \
                    and node.args[1].func.id == 'UNDEF':
                val = None
            assigns.append((node.lineno, node.args[0].value, val))
        elif nm == 'add_struct':
            kw = {k.arg: k.value for k in node.keywords}
            if 'nb_sth1' not in kw or 'array2' not in kw or not isinstance(kw['array2'], ast.List):
                continue
            v = kw['nb_sth1']
            if (v.value if isinstance(v, ast.Constant) else None) != 0x3:
                continue
            vals = kw['array2'].elts[0::2]
            ints = [_intval(x) for x in vals]
            strs = [x.value for x in vals if isinstance(x, ast.Constant) and isinstance(x.value, str)]
            texts = [t for t in strs if t and not t.startswith('<#')]
            if len(ints) < 3 or not isinstance(ints[0], int) or not isinstance(ints[2], int) or not texts:
                continue
            if ints[0] != 5:      # 5=Cmd_text家族; (1,47)=动画资源名等非对话
                continue
            voice = next((t for t in ints[3:9] if isinstance(t, int) and 10000 <= t <= 59999), None)
            adds.append((node.lineno, ints[2], voice, ''.join(texts)))
    dialog.sort(); disp.sort()
    return dialog, disp, varl, assigns, calls, adds


def norm(t):
    return re.sub(r'\s+', '', t or '')


def main():
    ap = argparse.ArgumentParser(description='一键生成 voice_lookup_index_{game}.json')
    ap.add_argument('--game', default='sc', choices=['fc', 'sc'])
    ap.add_argument('--py-dir', required=True, help='Remake 日文反编译 py 目录(主坐标系)')
    ap.add_argument('--py-dir-sc', default=None, help='简中反编译 py 目录(显示名/文本中文)')
    ap.add_argument('--game-dir', default=None, help='游戏安装目录(含 pac/steam/table.pac)')
    ap.add_argument('--table-pac', default=None, help='直接给 table.pac 路径')
    ap.add_argument('--skip-download', action='store_true')
    ap.add_argument('--skip-pipeline', action='store_true', help='复用已有 my_match_result, 不重跑s1-s6')
    ap.add_argument('--rebuild-names', action='store_true')
    args = ap.parse_args()
    game, suf = args.game, ('' if args.game == 'fc' else f'_{args.game}')

    download_assets(game, args.skip_download)
    ensure_speaker_data(game)
    if not args.skip_pipeline:
        py = os.path.abspath(args.py_dir)
        run_step([PY, 's1_build_remake_structure.py', game, py], 's1')
        run_step([PY, 's2_build_evo_structure.py', game, '--prefix-stats'], 's2前缀统计')
        run_step([PY, 'derive_speaker_map.py', game], '说话人映射推导')
        run_step([PY, 's4_generate_match_result.py', game], 's4匹配')
        if args.py_dir_sc:
            run_step([PY, 's6_build_match_result_csv.py', game, py,
                      os.path.abspath(args.py_dir_sc)], 's6详表')
    mr_p = require(f'my_match_result{suf}.csv')
    sd_p = require(f'script_data_{game}.json')

    names = load_t_name(args, game)

    # ---- 解析两套 py ----
    files = sorted(os.path.basename(p)[:-3] for p in glob.glob(os.path.join(args.py_dir, '*.py')))
    jp, scd = {}, {}
    for i, f in enumerate(files):
        jp[f] = parse_py(os.path.join(args.py_dir, f + '.py'))
        p = os.path.join(args.py_dir_sc, f + '.py') if args.py_dir_sc else None
        scd[f] = parse_py(p) if p and os.path.exists(p) else None
        if i % 20 == 0:
            print(f'  解析 py {i + 1}/{len(files)}...')

    def nm(spk):
        return ((names.get(spk) if isinstance(spk, int) else None) or names.get(str(spk)) or {}).get('jp')

    def has_name(spk):
        return (isinstance(spk, int) and spk in names) or str(spk) in names

    def disp_join(f):
        """JP/SC显示名按 同spk第i次事件 对齐(行号可能因译文漂移, 事件序1:1)"""
        je = collections.defaultdict(list); se = collections.defaultdict(list)
        for ln, spk, name in jp[f][1]:
            je[spk].append(name)
        if scd.get(f):
            for ln, spk, name in scd[f][1]:
                se[spk].append(name)

        def resolve(spk, line):
            idx = -1
            for ln, s2, _ in jp[f][1]:
                if s2 == spk and ln < line:
                    idx += 1
            if idx < 0:
                return None, None
            dj = je[spk][idx] if idx < len(je[spk]) else None
            ds = se.get(spk, [None] * (idx + 1))[idx] if scd.get(f) and idx < len(se.get(spk, [])) else None
            return dj, ds
        return resolve

    def text_sc_join(f):
        """SC文本: (file,voice)优先, 其次(file,cmd,spk,序号)"""
        jmap = {}
        if not scd.get(f):
            return jmap
        by_voice = {}
        for ln, cmd, spk, v, t in scd[f][0]:
            if v:
                by_voice.setdefault(v, t)
        occ = collections.Counter(); socc = collections.Counter()
        skey = {}
        for ln, cmd, spk, v, t in scd[f][0]:
            k = (cmd, spk)
            socc[k] += 1
            skey[(k, socc[k])] = t
        for ln, cmd, spk, v, t in jp[f][0]:
            if v and v in by_voice:
                jmap[ln] = by_voice[v]
            else:
                k = (cmd, spk)
                occ[k] += 1
                if (k, occ[k]) in skey:
                    jmap[ln] = skey[(k, occ[k])]
        return jmap

    # ---- VAR 回溯(调用点实参 / 就近AssignVar): 预扫描函数块边界 + 全库调用点 ----
    fn_of = {}          # (file,line) -> 函数名
    all_calls = collections.defaultdict(list)   # fname -> [{file,line,args}]
    for f in files:
        cur = None
        for i, line in enumerate(open(os.path.join(args.py_dir, f + '.py'), encoding='utf-8'), 1):
            m = FN_RE.search(line)
            if m:
                cur = m.group(1)
                continue
            cm = re.search(r'CallFunction\("([^"]+)", \[(.*?)\]\)', line)
            if cm:
                a = [int(x) for x in re.findall(r'INT\((\d+)\)', cm.group(2))]
                all_calls[cm.group(1)].append({'file': f, 'line': i, 'args': a})
            if cur:
                fn_of[(f, i)] = cur

    var_res = {}   # (file,line) -> {'resolved':[id], 'how':str}
    for f in files:
        for line, cmd, varname in jp[f][2]:
            how = resolved = None
            if varname.startswith('PARAM_'):
                k = int(varname[6:])
                fn = fn_of.get((f, line))
                cands = [c for c in all_calls.get(fn, []) if len(c['args']) > k]
                vals = sorted({c['args'][k] for c in cands})
                if vals:
                    resolved, how = vals, f"回溯CallFunction('{fn}')第{k}参,{len(cands)}个调用点"
                else:
                    how = f"函数'{fn}'无静态调用点(引擎回调)"
            else:
                cands = [(ln, v) for ln, vr, v in jp[f][3] if vr == varname and ln < line]
                if cands and cands[-1][1] is not None:
                    resolved, how = [cands[-1][1]], f'就近AssignVar({varname})=INT({cands[-1][1]})'
                elif cands:
                    how = f'就近AssignVar({varname})=UNDEF占位,静态不可解'
                else:
                    how = '无前置AssignVar'
            var_res[(f, line)] = {'resolved': resolved, 'how': how}

    # ---- 行记录合成(内联 + add_struct去重补充) ----
    records = {}
    inline_voiced = set(); inline_txt = set()
    for f in files:
        dj = disp_join(f); tj = text_sc_join(f)
        for line, cmd, spk, voice, text in jp[f][0]:
            djn, dsn = dj(spk, line) if spk is not None else (None, None)
            if voice:
                inline_voiced.add((f, voice))
            inline_txt.add((f, spk, norm(text)[:24]))
    for f in files:
        for line, spk, voice, text in jp[f][5]:
            if voice and (f, voice) in inline_voiced:
                continue
            if not voice and (f, spk, norm(text)[:24]) in inline_txt:
                continue
            jp[f][0].append((line, 'add_struct', spk, voice, text))
        jp[f][0].sort()

    for f in files:
        dj = disp_join(f); tj = text_sc_join(f)
        for line, cmd, spk, voice, text in jp[f][0]:
            source = 'add_struct' if cmd == 'add_struct' else 'cmd'
            djn = dsn = None
            if spk is not None:
                djn, dsn = dj(spk, line)
            v = var_res.get((f, line)) if spk is None else None
            rec = {'file': f, 'line': line, 'cmd': cmd, 'source': source,
                   'speaker_id': spk, 'name_jp': nm(spk),
                   'display_name_jp': djn, 'display_name_sc': dsn,
                   'voice_id': voice, 'text_jp': (text or '')[:80],
                   'text_sc': (tj.get(line) or '')[:80] if tj else None}
            if isinstance(spk, int) and spk == 65535:
                rec['status'] = 'CONFIRMED' if voice else 'NO_VOICE'
                rec['speaker_note'] = '无名/系统'
            elif isinstance(spk, int):
                multi = (djn and ('＆' in djn or '&' in djn)) or (dsn and ('＆' in dsn or '&' in dsn))
                if multi:
                    rec['status'] = 'MULTI_SPEAKER'
                elif not has_name(spk) and not (djn or dsn):
                    rec['status'] = 'UNCERTAIN'; rec['speaker_note'] = '动态槽位且无显示名'
                else:
                    rec['status'] = 'CONFIRMED' if voice else 'NO_VOICE'
            elif spk is None and v is not None:
                if v['resolved'] and len(v['resolved']) == 1:
                    rid = v['resolved'][0]
                    d2j, d2s = dj(rid, line)
                    rec.update({'speaker_id': rid, 'name_jp': nm(rid),
                                'display_name_jp': d2j, 'display_name_sc': d2s,
                                'status': 'CONFIRMED' if voice else 'NO_VOICE',
                                'speaker_note': f"VAR回溯唯一解: {v['how']}"})
                elif v['resolved']:
                    rec['status'] = 'MULTI_OPTION'
                    rec['candidates'] = [{'speaker_id': x, 'name_jp': nm(x)} for x in v['resolved']]
                    rec['speaker_note'] = v['how']
                else:
                    rec['status'] = 'UNCERTAIN'; rec['speaker_note'] = v['how']
            else:
                rec['status'] = 'UNCERTAIN'; rec['speaker_note'] = '首参非INT(LoadVar未识别或空参)'
            if isinstance(rec.get('speaker_id'), int):
                rec['entity_key'] = f"{rec['speaker_id']}|{norm(rec.get('display_name_jp') or rec.get('display_name_sc'))}"
            records.setdefault(f, {})[str(line)] = rec

    # ---- 两级投票 ----
    voice_data = {x['voice_id']: x for x in json.load(open(sd_p, encoding='utf-8'))
                  if x.get('voice_id')}
    match = [r for r in csv.DictReader(open(mr_p, encoding='utf-8'))
             if r.get('MyVoiceId') and re.fullmatch(r'\d{10}', r['MyVoiceId'])]
    by_vf = {}
    for f in records:
        for ln, rec in records[f].items():
            if rec.get('voice_id'):
                by_vf[(f, rec['voice_id'])] = rec
    freq = collections.Counter()
    for f in records:
        for rec in records[f].values():
            t = norm(rec.get('text_jp') or rec.get('text_sc') or '')
            if len(t) >= 4:
                freq[t] += 1

    def signature(rec):
        t = norm(rec.get('text_jp') or rec.get('text_sc') or '')
        return len(t) >= 4 and freq[t] <= 3

    for f in records:      # 场景×角色段
        run_cnt = {}; open_ent = {}
        for ln in sorted(records[f], key=int):
            rec = records[f][ln]
            ek = rec.get('entity_key')
            if not ek:
                continue
            if open_ent.get(rec['speaker_id']) != ek:
                run_cnt[ek] = run_cnt.get(ek, 0) + 1
                open_ent[rec['speaker_id']] = ek
            rec['evo_group'] = f'{f}::{ek}::r{run_cnt[ek]}'

    grp_pre = collections.defaultdict(collections.Counter)
    grp_cid = collections.defaultdict(collections.Counter)
    for m in match:
        rv = m.get('RemakeVoiceId')
        if not (rv and rv.isdigit()):
            continue
        rec = by_vf.get((m['Scene'], int(rv)))
        if not rec or not rec.get('evo_group') or rec['status'] not in ('CONFIRMED', 'MULTI_SPEAKER'):
            continue
        if not signature(rec):
            continue
        ev = voice_data.get(m['MyVoiceId'] + 'V')
        if not ev:
            continue
        g = rec['evo_group']
        grp_pre[g][m['MyVoiceId'][:3]] += 1
        try:
            ci = int(ev.get('character_id') or '0', 16)
        except ValueError:
            ci = 0
        if ci >= 0x100:
            grp_cid[g][ci] += 1

    groups = {}; evo_shared = set()
    for g, c in grp_pre.items():
        total = sum(c.values()); top = c.most_common()
        p0, n0 = top[0]
        cidc = grp_cid.get(g)
        cid_top = cidc.most_common(1)[0] if cidc else None
        ent = {'votes': [{'prefix': p, 'count': n} for p, n in top[:5]],
               'n_voted_lines': total, 'file': g.split('::')[0], 'entity_key': g.split('::')[1]}
        if n0 / total >= 0.7:
            _jp, _cn = evo_speaker.bank_name(p0, args.game)
            ent['evo'] = {'prefix': p0,
                          'char_id': (f'0x{cid_top[0]:X}' if cid_top else None),
                          'confidence': round(n0 / total, 2),
                          'identity_jp': _jp, 'identity_cn': _cn}
        else:
            ent['evo_multi_shared'] = True
            evo_shared.add(p0)
        groups[g] = ent

    entity_info = collections.defaultdict(
        lambda: {'n_lines': 0, 'n_voiced': 0, 'files': collections.Counter()})
    for f in records:
        for rec in records[f].values():
            ek = rec.get('entity_key')
            if not ek:
                continue
            ei = entity_info[ek]
            ei['n_lines'] += 1
            ei['n_voiced'] += 1 if rec.get('voice_id') else 0
            ei['files'][f] += 1
            for k in ('name_jp', 'display_name_jp', 'display_name_sc'):
                if rec.get(k) and k not in ei:
                    ei[k] = rec[k]

    entities = {}
    for ek, ei in entity_info.items():
        my = {g: e for g, e in groups.items() if e['entity_key'] == ek}
        ent = {'speaker_id': int(ek.split('|')[0]),
               'name_jp': ei.get('name_jp'), 'display_name_jp': ei.get('display_name_jp'),
               'display_name_sc': ei.get('display_name_sc'),
               'n_lines': ei['n_lines'], 'n_voiced': ei['n_voiced'],
               'files': sorted(ei['files']), 'n_groups': len(my)}
        prefixes = {e['evo']['prefix'] for e in my.values() if e.get('evo')}
        cid_votes = collections.Counter(e['evo']['char_id'] for e in my.values()
                                        if e.get('evo') and e['evo'].get('char_id'))
        if len(prefixes) == 1 and prefixes:
            cid_total = sum(cid_votes.values())
            ct = cid_votes.most_common(1)[0] if cid_votes else None
            _p0 = next(iter(prefixes))
            _jp, _cn = evo_speaker.bank_name(_p0, args.game)
            ent['evo'] = {'prefix': _p0,
                          'char_id': (ct[0] if ct and ct[1] / cid_total >= 0.6 else None),
                          'char_id_consistent': len(cid_votes) <= 1,
                          'confidence': round(min(e['evo']['confidence'] for e in my.values()
                                                  if e.get('evo')), 2),
                          'identity_jp': _jp, 'identity_cn': _cn}
            ent['vote_scope'] = 'global_unanimous'
        elif len(prefixes) > 1:
            per_file = {}
            for g, e in sorted(my.items()):
                if e.get('evo'):
                    per_file.setdefault(e['file'], []).append(e['evo']['prefix'])
            ent['evo_scene_dependent'] = {f2: v[0] for f2, v in sorted(per_file.items())}
            ent['vote_scope'] = 'scene_dependent'
        else:
            ent['vote_scope'] = 'no_vote'
        if any(e.get('evo_multi_shared') for e in my.values()):
            ent['has_multi_shared_group'] = True
        entities[ek] = ent

    for m in match:      # 行级实际匹配附着
        rv = m.get('RemakeVoiceId')
        if not (rv and rv.isdigit()):
            continue
        rec = by_vf.get((m['Scene'], int(rv)))
        if rec:
            rec['evo_match'] = {'voice_file': m['MyVoiceId'] + 'V', 'prefix': m['MyVoiceId'][:3],
                                'char_id': (voice_data.get(m['MyVoiceId'] + 'V') or {}).get('character_id'),
                                'match_type': m.get('MatchType'), 'speaker_match': m.get('SpeakerMatch')}

    st = collections.Counter(rec['status'] for f in records for rec in records[f].values())
    index = {'meta': {
        'line_coordinate': '日文反编译(py-dir)行号, 与 match_result 详细表 RemakeScenaScriptLineno 一致',
        'status_defs': {
            'CONFIRMED': '固定说话人+带语音号', 'NO_VOICE': '固定说话人,无语音号(未配音行)',
            'MULTI_SPEAKER': '多人合语音(显示名含＆)', 'MULTI_OPTION': '动态解析多候选(见candidates)',
            'UNCERTAIN': '不确定(无静态调用点/UNDEF占位/引擎回调/动态槽无名)', 'NOT_FOUND': '该行无对话'},
        'evo_vote_rule': '场景×角色段两级投票(标志性台词=语料频次<=3且长度>=4, char_id仅计全局>=0x100): '
                         '段内top1前缀占比>=70%->段映射; 段内分裂->多人共用; 跨场景不一致->scene_dependent',
        'prefix_stats': f'evo_prefix_stats_{game}.json(s2 --prefix-stats)',
        'names_source': f'speaker_names_t_name_{game}.json' if names else '(降级: 无t_name)'},
        'entities': entities, 'evo_shared_prefixes': sorted(evo_shared), 'lines': records}
    out_p = os.path.join(W, f'voice_lookup_index_{game}.json')
    json.dump(index, open(out_p, 'w', encoding='utf-8'), ensure_ascii=False)
    print(f'行记录: {sum(st.values())} {dict(st)}')
    print(f'实体: {len(entities)} '
          f'({sum(1 for e in entities.values() if e["vote_scope"] == "global_unanimous")}全局一致, '
          f'{sum(1 for e in entities.values() if e["vote_scope"] == "scene_dependent")}场景依赖)')
    print(f'EVO多人共用前缀(段内分裂): {sorted(evo_shared)}')
    print(f'输出: {out_p}')


if __name__ == '__main__':
    main()
