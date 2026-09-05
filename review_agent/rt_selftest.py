#!/usr/bin/env python3
"""rt.py 全参数自测矩阵：每条命令 × 合法/缺参/非法参数，检查无静默空返回。可重复跑(优化前后对比)。"""
import json, subprocess, sys, time, statistics

RT = ['uv', 'run', 'python', 'rt.py']
CASES = [
    # (名称, 参数, 判定函数: 输出文本 -> None通过 / 错误描述)
    ("vid 合法", ['vid', '0010470171'], lambda o: None if '"found": true' in o and '"msg_id"' in o else '未找到/msg_id缺失'),
    ("vid 补录take", ['vid', '0010290543'], lambda o: None if '"unreferenced_take": true' in o and 'で、でも' in o else '补录提示缺失'),
    ("vid 不存在", ['vid', '9999999999'], lambda o: None if '"found": false' in o and 'note' in o else '无明确note'),
    ("vid ch/V前后缀", ['vid', 'ch0010470171V'], lambda o: None if '"found": true' in o else '格式容错失败'),
    ("vid 缺参", ['vid'], lambda o: None if 'error' in o or '用法' in o or '缺少' in o else '静默'),
    ("vid 乱码", ['vid', 'abc'], lambda o: None if o.strip() else '空输出'),
    ("find 基本", ['find', 'ふむ……', '--char', '003'], lambda o: None if ('hits' in o or 'error' in o) and o.strip() else '空'),
    ("find scene场景名", ['find', 'ふむ……', '--scene', 'T0700'], lambda o: None if 'query' in o else '异常'),
    ("find 非法char", ['find', 'テスト', '--char', '99'], lambda o: None if 'error' in o else '非法参数未报错'),
    ("find 缺文本", ['find'], lambda o: None if ('error' in o or 'query' in o) and o.strip() else '静默空'),
    ("findmany 批量", ['findmany', '[["ふむ……","003"],["うん、そうだね","001"]]'], lambda o: None if '"count": 2' in o else 'count!=2'),
    ("findmany 空数组", ['findmany', '[]'], lambda o: None if '"count": 0' in o else '空数组无显式count'),
    ("findmany 非法JSON", ['findmany', 'notjson'], lambda o: None if 'error' in o else '静默'),
    ("runcheck 缺口块", ['runcheck', 'mp0000_ev', 'EV_04_26_00'], lambda o: None if 'take缺口候选' in o else '缺口issue未出现'),
    ("runcheck 不存在的块", ['runcheck', 'xxx', 'yyy'], lambda o: None if 'error' in o or 'issues' in o else '静默'),
    ("rowhint 缺口行", ['rowhint', 'mp0000_ev', 'EV_04_26_00', '101946'], lambda o: None if '"group_conflict": true' in o and '0010290543' in o else '缺口候选缺失'),
    ("rowhint 块外行", ['rowhint', 'mp0000_ev', 'EV_04_26_00', '999999'], lambda o: None if 'error' in o else '静默'),
    ("rowhint 缺参", ['rowhint', 'a', 'b'], lambda o: None if '用法' in o or 'error' in o else '静默'),
    ("bank 主角", ['bank', '005'], lambda o: None if 'アガット' in o and 'char_id+T_NAME' in o else '名字/状态缺失'),
    ("bank 未知码", ['bank', '998'], lambda o: None if '"found": false' in o and 'note' in o else '无note'),
    ("bank 缺参", ['bank'], lambda o: None if o.strip() else '空输出'),
    ("bank 混杂输入", ['bank', 'ch005V'], lambda o: None if 'アガット' in o else '提取失败'),
    ("speaker 行级", ['speaker', 'mp2000_ev', '62412'], lambda o: None if 'CONFIRMED' in o else '状态缺失'),
    ("speaker 不存在场景", ['speaker', 'zzz', '1'], lambda o: None if ('NOT_FOUND' in o or 'error' in o) and o.strip() else '静默空'),
    ("speaker 缺参", ['speaker'], lambda o: None if '用法' in o or 'error' in o else '静默'),
    ("speaker entity未知", ['speaker', '--entity', '不存在|xxx'], lambda o: None if o.strip() and ('error' in o or 'vote_scope' in o or 'FOUND' in o) else '静默空'),
    ("claim 无待办", ['claim', 'SELFTEST'], lambda o: None if ('没有可领取' in o or 'todo' in o or 'scene' in o) else '静默'),
    ("claim 缺参", ['claim'], lambda o: None if ('error' in o or '没有可领取' in o) and o.strip() else '静默'),
    ("pack 重取", ['pack', 'mp0000_ev', 'EV_04_26_00'], lambda o: None if ('rows' in o or 'error' in o) and o.strip() else '静默'),
    ("pack 不存在块", ['pack', 'x', 'y'], lambda o: None if 'error' in o else '静默'),
    ("release 未租约块", ['release', 'x', 'y'], lambda o: None if o.strip() else '空输出'),
    ("submitmany 非法裁定", ['submitmany', '[{"bad":1}]'], lambda o: None if 'errors' in o and '"ok": 0' in o else '校验未拦截'),
    ("submitmap 不存在块", ['submitmap', 'x', 'y', '{"1":"OK"}'], lambda o: None if 'error' in o else '静默'),
    ("autook 全库扫描", ['autook'], lambda o: None if o.strip() else '空输出'),
    ("未知命令", ['subm'], lambda o: None if 'closest' in o else '无纠错建议'),
    ("help", ['-h'], lambda o: None if '领包 / 提交' in o else '帮助缺失'),
]

def main():
    results = []
    for name, args, check in CASES:
        t0 = time.perf_counter()
        r = subprocess.run(RT + args, capture_output=True, text=True, encoding='utf-8', errors='replace')
        dt = time.perf_counter() - t0
        out = r.stdout
        err = check(out) if r.returncode == 0 or out else '进程失败/无输出: ' + r.stderr[:80]
        results.append((name, 'PASS' if err is None else 'FAIL: ' + str(err), dt))
    fails = [x for x in results if x[1].startswith('FAIL')]
    total = sum(x[2] for x in results)
    print(f'{"用例":<24}{"结果":<12}{"耗时"}')
    for name, st, dt in results:
        print(f'{name:<24}{st:<12}{dt:.2f}s')
    print(f'\n合计 {len(results)} 用例, 失败 {len(fails)}, 总耗时 {total:.1f}s, 平均 {total/len(results):.2f}s')

if __name__ == '__main__':
    main()
