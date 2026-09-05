#!/usr/bin/env python3
"""用 ast 重建 Remake 剧情结构（含顺序边 fallthrough）。

块尾: JUMP / JumpWhenFalse / JumpWhenTrue / Return
边:   jump=无条件, cond_false=假分支, cond_true=真分支,
      next=纯顺序fallthrough
      (JumpWhenFalse 的真分支 / JumpWhenTrue 的假分支 也记为顺序边 cond_true/cond_false)
"""
import ast, glob, json, re
import os
from collections import OrderedDict, Counter
from paths import W, resolve, require
GAME_S1 = 'fc'   # 由 __main__ 按 argv 设置，决定接受的 Cmd_text 变体

def parse_remake_ast(path):
    src = open(path, encoding='utf-8').read()
    tree = ast.parse(src)
    nodes = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
            if node.func.id in ('set_current_function', 'Label', 'Command', 'JUMP',
                                'JumpWhenFalse', 'JumpWhenTrue', 'CallFunction'):
                nodes.append((node.lineno, node.col_offset, 'call', node))
        elif isinstance(node, ast.Return):
            nodes.append((node.lineno, node.col_offset, 'return', node))
    nodes.sort(key=lambda x: (x[0], x[1]))

    funcs = OrderedDict(); cur_func = None; cur_block = None
    cur_order = []; cur_end = {}
    disp_last = {}   # 说话人ID -> 就近生效的 chr_set_display_name 显示名
    def ensure_block():
        nonlocal cur_block
        if cur_func is not None and cur_block is None:
            cur_block = '_entry'
            funcs[cur_func]['blocks'].setdefault('_entry', [])
            if '_entry' not in cur_order:
                cur_order.append('_entry')
    def add_edge(f, t, ty):
        funcs[cur_func]['edges'].append({'f': f, 't': t, 'type': ty})
    def flush_order():
        nonlocal cur_order, cur_end
        for i, lab in enumerate(cur_order):
            if i == len(cur_order) - 1:
                break
            nxt = cur_order[i + 1]
            if cur_end.get(lab) is None:  # 无 JUMP/Return 结尾 = 纯顺序 fallthrough
                add_edge(lab, nxt, 'next')
        cur_order = []; cur_end = {}

    for lineno, col, kind, node in nodes:
        if kind == 'return':
            if cur_func is not None and cur_block is not None:
                cur_end[cur_block] = 'return'
            cur_block = None
            continue
        name = node.func.id
        if name == 'set_current_function':
            if cur_func is not None:
                flush_order()
            cur_func = node.args[0].value
            funcs.setdefault(cur_func, {'blocks': OrderedDict(), 'edges': []})
            cur_block = None
        elif name == 'Label':
            cur_block = node.args[0].value
            funcs[cur_func]['blocks'].setdefault(cur_block, [])
            cur_order.append(cur_block)
        elif name == 'CallFunction':
            # chr_set_display_name(INT(说话人), "显示名", ...) 运行时改名时间线
            # (动态槽位20xxx/变装/匿名 的唯一名字来源; 按出现顺序就近生效)
            if node.args and isinstance(node.args[0], ast.Constant) and node.args[0].value == 'chr_set_display_name' \
                    and len(node.args) > 1 and isinstance(node.args[1], ast.List):
                elts = node.args[1].elts
                if len(elts) >= 2 and isinstance(elts[0], ast.Call) and getattr(elts[0].func, 'id', '') == 'INT' \
                        and elts[0].args and isinstance(elts[0].args[0], ast.Constant) \
                        and isinstance(elts[1], ast.Constant) and isinstance(elts[1].value, str):
                    disp_last[elts[0].args[0].value] = elts[1].value
        elif name == 'Command':
            if cur_func is None or len(node.args) < 2: continue
            cmd_type = node.args[0].value
            # sc: 00/06=普通对话, 13=带立绘对话(UNKNOWN_05_13 为其反编译别名), 08=分支选项/系统文本
            ok_types = ('Cmd_text_00', 'Cmd_text_06') if GAME_S1 == 'fc' else \
                       ('Cmd_text_00', 'Cmd_text_06', 'Cmd_text_13', 'UNKNOWN_05_13', 'Cmd_text_08')
            if cmd_type not in ok_types: continue
            if not isinstance(node.args[1], ast.List): continue
            spk = None; texts = []; rid = None
            skind = 'unknown'
            first = node.args[1].elts[0] if node.args[1].elts else None
            if isinstance(first, ast.Call) and getattr(first.func, 'id', '') == 'INT' \
                    and first.args and isinstance(first.args[0], ast.Constant):
                skind = 'fixed'; spk = first.args[0].value
            elif isinstance(first, ast.Call) and getattr(first.func, 'id', '') == 'LoadVar':
                skind = 'var'   # 动态传参(公共库PARAM/VAR): 静态说话人不定
            for elt in node.args[1].elts:
                if isinstance(elt, ast.Constant) and isinstance(elt.value, str):
                    texts.append(elt.value)
            # 内嵌语音表ID: INT(11), INT(id) 相邻对 -> t_voice.tbl 行号（Remake 自有语音文件名）
            _is_int = lambda e: isinstance(e, ast.Call) and getattr(e.func, 'id', '') == 'INT' and e.args and isinstance(e.args[0], ast.Constant)
            for a, b in zip(node.args[1].elts, node.args[1].elts[1:]):
                if _is_int(a) and a.args[0].value == 11 and _is_int(b):
                    rid = b.args[0].value
                    break
            text = ''.join(texts)
            text = re.sub(r'<[^>]*>', '', text)  # 去所有 <...> 控制码标记
            if text:
                ensure_block()
                funcs[cur_func]['blocks'][cur_block].append({
                    'speaker': spk, 'text': text, 'rid': rid,
                    # 说话人不确定性/场景依赖标注(供s4标记与s7索引):
                    # skind: fixed=固定ID / var=动态传参(参数或VAR,静态不定) / unknown=首参非INT
                    # disp:  chr_set_display_name 就近生效的运行时显示名(变装/匿名/动态槽)
                    # line:  反编译行号(查询接口坐标系)
                    'skind': skind,
                    'disp': disp_last.get(spk) if spk is not None else None,
                    'line': lineno,
                })
        elif name == 'JUMP':
            if cur_func is not None and cur_block is not None:
                add_edge(cur_block, node.args[0].value, 'jump')
                cur_end[cur_block] = 'jump'; cur_block = None
        elif name == 'JumpWhenFalse':
            if cur_func is not None and cur_block is not None:
                add_edge(cur_block, node.args[0].value, 'cond_false')
                # 真分支 fallthrough，cur_block 不变（块内继续）
        elif name == 'JumpWhenTrue':
            if cur_func is not None and cur_block is not None:
                add_edge(cur_block, node.args[0].value, 'cond_true')
                # 假分支 fallthrough，cur_block 不变
    if cur_func is not None:
        flush_order()
    return funcs

if __name__ == '__main__':
    import sys
    # 用法: python s1_build_remake_structure.py [fc|sc] [反编译py目录]
    #   目录省略时用内置默认(fc: remake_jp, sc: remake2nd_demo/py，相对数据根W)
    from synonyms import normalize
    args = [a for a in sys.argv[1:] if not a.startswith('-')]
    GAME = (args[0].lower() if args else 'fc')
    assert GAME in ('fc', 'sc'), f'未知游戏代号: {GAME}'
    GAME_S1 = GAME
    PY_DIR = args[1] if len(args) > 1 else {'fc': 'remake_jp', 'sc': 'remake2nd_demo/py'}[GAME]
    # sc 扫描全部 scena 脚本（含 e*/system/sys_event——与 scena_voice_kuro_extractor 一致）
    PATTERN = 'mp*.py' if GAME == 'fc' else '*.py'
    # fc 保持原文件名（下游依赖），sc 带后缀
    OUT = os.path.join(W, 'remake_structure.json' if GAME == 'fc' else f'remake_structure_{GAME}.json')
    remake_structure = {}
    _cand = os.path.join(W, PY_DIR) if not os.path.isabs(PY_DIR) else PY_DIR
    _base = _cand if os.path.isdir(_cand) else PY_DIR   # 兼容 cwd 相对路径
    for f in glob.glob(os.path.join(_base, PATTERN)):
        scene = os.path.basename(f)[:-3]
        try:
            funcs = parse_remake_ast(f)
            if funcs: remake_structure[scene] = funcs
        except Exception as e:
            print(f'ERR {scene}: {e}')
    json.dump(remake_structure, open(OUT, 'w'), ensure_ascii=False)
    nf = nb = ne = nl = 0
    for sc, funcs in remake_structure.items():
        nf += len(funcs)
        for fn, f in funcs.items():
            nb += len(f['blocks']); ne += len(f['edges']); nl += sum(len(b) for b in f['blocks'].values())
    print(f'Remake[{GAME}] -> {os.path.basename(OUT)}: {len(remake_structure)}场景 函数{nf} 块{nb} 边{ne} 台词{nl}')
    rc = Counter(e['type'] for sc in remake_structure.values() for f in sc.values() for e in f['edges'])
    print('边类型:', dict(rc))
