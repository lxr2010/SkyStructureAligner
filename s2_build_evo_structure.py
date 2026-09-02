#!/usr/bin/env python3
"""重建 EVO 真实控制流：py(结构+边) 与 msg(跨行台词) 按【顺序位置】串联。

关键：talk_num 在 _1/_2 变体场景会重新编号(py 从0、msg 有偏移)，故不能按 talk_num
相等关联，必须按 ChrTalk 的出现顺序一一对应。

输出 evo_structure.json:
  scene -> 函数 -> {blocks: {label: [台词...]}, edges: [{f,t,type}]}
  台词 = {talk_num, speaker, voice_id(10位V,无语音为None), text(日语清洗后)}
"""
import ast, re, glob, json, os, sys, codecs
from collections import OrderedDict, Counter
from paths import W, resolve

# 用法: python s2_build_evo_structure.py [fc|sc|3rd]，默认 fc
GAME = (sys.argv[1].lower() if len(sys.argv) > 1 else 'fc')
assert GAME in ('fc', 'sc', '3rd'), f'未知游戏代号: {GAME}'

def find_sora_root():
    """定位 SoraVoiceScripts 数据根目录（内含 cn.{fc,sc,3rd}/py 与 out.msg）。"""
    for cand in ('SoraVoiceScripts-zhenjian', 'sora-voice-matcher/SoraVoiceScripts'):
        p = os.path.join(W, cand)
        if os.path.isdir(os.path.join(p, f'cn.{GAME}', 'py')):
            return p
    raise SystemExit(f'未找到 SoraVoiceScripts 数据目录（需含 cn.{GAME}/py），'
                     '请在 W 下放置 SoraVoiceScripts-zhenjian 或 sora-voice-matcher/SoraVoiceScripts')
SORA = find_sora_root()

def _sjis_one_byte(exc):
    """Shift_JIS 非法序列只跳 1 字节：防止非法前导字节把 ASCII 尾字节(如'[')一起吞掉
    （❤等字会破坏 [x02] 控制码导致整条语音丢失）"""
    return ('?', exc.start + 1)
codecs.register_error('sjis_keep_ascii', _sjis_one_byte)

def clean_text(raw):
    raw = re.sub(r'^0x[0-9A-Fa-f]+', '', raw)
    raw = re.sub(r'#\d+R[^#]*#', '', raw)   # 注音块(ルビ): 如 '絵柄#4Rスート#' → '絵柄'，整块删除防读法漏入正文
    raw = re.sub(r'#\d+[A-Za-z]', '', raw)
    raw = re.sub(r'\[x[0-9A-Fa-f]{2}\]', '', raw)
    raw = re.sub(r'[^\[]x[0-9A-Fa-f]{2}\]', '', raw)   # 解码吞掉'['后的残骸
    raw = raw.replace('�', '')
    return raw.strip()

# ---------- msg: 顺序列表 [{talk_num, speaker, segs:[{voice_id,text}]}] ----------
def parse_msg(path):
    raw = open(path, 'rb').read()
    # 段结束标记[x02]在字节层检测——❤等非Shift_JIS字会与'['组成合法双字节对，解码后[x02]消失
    raw_lines = raw.split(b'\n')
    # cp932(含NEC/IBM扩展)才能解出 Falcom 外字(如 =㈱→normalize按gaiji删除)，shift_jis会变乱码吞控制码
    lines = [b.decode('cp932', errors='sjis_keep_ascii') for b in raw_lines]
    talks = []
    i = 0
    while i < len(lines):
        s = lines[i].strip()
        m = re.match(r'(ChrTalk|AnonymousTalk|NpcTalk)\s+#(\d+)', s)
        if m:
            tnum = int(m.group(2))
            i += 1
            speaker = lines[i].strip() if i < len(lines) else ''
            if m.group(1) == 'NpcTalk':
                i += 2   # NpcTalk 头部: hex行 + 说话人名字行(+空行)，跳过防名字拼入正文
            segs = []; buf = []; vid = None
            while i < len(lines):
                line = lines[i]
                if re.match(r'(ChrTalk|AnonymousTalk|NpcTalk)\s+#(\d+)', line.strip()):
                    break
                vm = re.search(r'#(\d{3})(\d{3})(\d{4})V', line) or re.search(r'#(\d{6})V', line)
                if vm and vid is None:
                    vid = vm.group(0)[1:-1]   # 10位(角色+场景+行号) 或 6位(OP/系统音)
                buf.append(line.rstrip('\n'))
                if b'[x02]' in raw_lines[i]:
                    segs.append({'voice_id': vid, 'text': clean_text(''.join(buf))})
                    buf = []; vid = None
                i += 1
            talks.append({'talk_num': tnum, 'speaker': speaker, 'segs': segs})
        else:
            i += 1
    return talks

# ---------- py: 结构(块+边) + 全局 talk 顺序 ----------
def parse_py(path):
    src = open(path, 'rb').read().decode('gbk', errors='replace')
    tree = ast.parse(src)
    lambda_ranges = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Lambda):
            lambda_ranges.append((node.lineno, getattr(node, 'end_lineno', node.lineno)))
        elif isinstance(node, ast.FunctionDef) and node.name.startswith('lambda_'):
            lambda_ranges.append((node.lineno, getattr(node, 'end_lineno', node.lineno)))
    def in_lambda(lineno):
        return any(lo <= lineno <= hi for lo, hi in lambda_ranges)
    events = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
            nm = node.func.id
            if nm in ('label', 'Jc', 'Jump', 'Switch') and not in_lambda(node.lineno):
                events.append((node.lineno, nm, node))
        elif isinstance(node, ast.Return) and not in_lambda(node.lineno):
            events.append((node.lineno, 'Return', node))
    for lineno, line in enumerate(src.split('\n'), 1):
        if in_lambda(lineno):
            continue
        m = re.match(r'\s*(ChrTalk|AnonymousTalk|NpcTalk)\(\s*#(\d+)', line)
        if m:
            events.append((lineno, 'Talk', int(m.group(2))))
    events.sort(key=lambda x: x[0])

    funcs = OrderedDict(); cur_func = None; cur_block = None; pending_jc = None
    cur_order = []; cur_end = {}; block_end_type = None
    talk_seq = []  # 全局顺序 [(func, label, talk_num)]
    def add_edge(f, t, ty):
        if cur_func is not None:
            funcs[cur_func]['edges'].append({'f': f, 't': t, 'type': ty})
    def flush_order():
        nonlocal cur_order, cur_end
        for i, lab in enumerate(cur_order):
            if i == len(cur_order) - 1:
                break
            nxt = cur_order[i + 1]
            if cur_end.get(lab) is None:  # 纯顺序 fallthrough
                add_edge(lab, nxt, 'next')
        cur_order = []; cur_end = {}
    for lineno, kind, payload in events:
        if kind == 'label':
            if cur_block is not None:
                cur_end[cur_block] = block_end_type
            lab = payload.args[0].value
            m = re.match(r'(Function_\d+)_', lab)
            if m:
                if cur_func is not None:
                    flush_order()
                cur_func = m.group(1)
                funcs.setdefault(cur_func, {'blocks': OrderedDict(), 'edges': []})
                cur_block = lab; funcs[cur_func]['blocks'].setdefault(lab, [])
                cur_order.append(lab)
                if pending_jc: add_edge(pending_jc, lab, 'cond_false'); pending_jc = None
            else:
                cur_block = lab; funcs[cur_func]['blocks'].setdefault(lab, [])
                cur_order.append(lab)
                if pending_jc: add_edge(pending_jc, lab, 'cond_false'); pending_jc = None
            block_end_type = None
        elif kind == 'Jc':
            target = payload.args[1].value if len(payload.args) > 1 else None
            if cur_block and target:
                add_edge(cur_block, target, 'cond_true'); pending_jc = cur_block
                block_end_type = 'cond_true'
        elif kind == 'Jump':
            target = payload.args[0].value
            if cur_block and target:
                add_edge(cur_block, target, 'jump')
                cur_end[cur_block] = 'jump'; cur_block = None
        elif kind == 'Switch':
            # Switch(cond, (case, "target"), ...) —— case 目标为 switch 边
            for arg in payload.args[1:]:
                if isinstance(arg, ast.Tuple) and len(arg.elts) >= 2:
                    tgt = arg.elts[1].value if isinstance(arg.elts[1], ast.Constant) else None
                    if cur_block and tgt:
                        add_edge(cur_block, tgt, 'switch')
            cur_end[cur_block] = 'switch'; cur_block = None
        elif kind == 'Return':
            if cur_block is not None:
                cur_end[cur_block] = 'return'; cur_block = None
        elif kind == 'Talk':
            if cur_func and cur_block:
                funcs[cur_func]['blocks'][cur_block].append(payload)
                talk_seq.append((cur_func, cur_block, payload))
            block_end_type = None
    if cur_func is not None:
        if cur_block is not None:
            cur_end[cur_block] = block_end_type
        flush_order()
    return funcs, talk_seq

# ---------- 全量关联 ----------
evo_structure = {}
n_blocks = n_edges = n_v = n_nov = 0
for pyf in glob.glob(os.path.join(SORA, f'cn.{GAME}', 'py', '*.py')):
    scene = os.path.basename(pyf).split('.')[0]
    msgf = os.path.join(SORA, f'cn.{GAME}', 'out.msg', scene + '.txt')
    try:
        funcs, talk_seq = parse_py(pyf)
        talks = parse_msg(msgf) if os.path.exists(msgf) else []
    except Exception:
        continue
    # 关联：按顺序位置对应（第 idx 个 py ChrTalk/NpcTalk = 第 idx 个 msg 同类）
    for fn, f in funcs.items():
        for lab in f['blocks']:
            f['blocks'][lab] = []
    for idx, (fn, lab, tnum) in enumerate(talk_seq):
        if idx >= len(talks):
            continue
        t = talks[idx]
        segs = t['segs'] if t['segs'] else [{'voice_id': None, 'text': ''}]
        for seg in segs:
            funcs[fn]['blocks'][lab].append({
                'talk_num': tnum, 'speaker': t['speaker'],
                'voice_id': seg['voice_id'], 'text': seg['text']})
            if seg['voice_id']: n_v += 1
            else: n_nov += 1
    # 未消费的 msg 尾部（py 反编译丢失的 ChrTalk/NpcTalk）→ 游离台词伪块，保留顺序供全局索引/连续段使用
    if len(talks) > len(talk_seq):
        uf = funcs.setdefault('_unassociated', {'blocks': OrderedDict(), 'edges': []})
        ub = uf['blocks'].setdefault('_tail', [])
        for t in talks[len(talk_seq):]:
            segs = t['segs'] if t['segs'] else [{'voice_id': None, 'text': ''}]
            for seg in segs:
                ub.append({'talk_num': t['talk_num'], 'speaker': t['speaker'],
                           'voice_id': seg['voice_id'], 'text': seg['text']})
                if seg['voice_id']: n_v += 1
                else: n_nov += 1
    for fn, f in funcs.items():
        n_edges += len(f['edges']); n_blocks += len(f['blocks'])
    evo_structure[scene] = funcs

# fc 保持原文件名（下游 exp_*/s3-s5 依赖），sc/3rd 带游戏后缀
OUT = os.path.join(W, 'evo_structure.json' if GAME == 'fc' else f'evo_structure_{GAME}.json')
json.dump(evo_structure, open(OUT, 'w'), ensure_ascii=False)
print(f'EVO 结构[{GAME}] -> {os.path.basename(OUT)}: '
      f'{len(evo_structure)}场景 函数{sum(len(v) for v in evo_structure.values())} '
      f'块{n_blocks} 边{n_edges} 语音{n_v} 无语音{n_nov}')
ec = Counter(e['type'] for sc in evo_structure.values() for f in sc.values() for e in f['edges'])
print('边类型:', dict(ec))
