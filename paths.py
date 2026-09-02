#!/usr/bin/env python3
"""统一路径解析：仓库可独立运行，也兼容旧的「父目录放数据」布局。

数据根目录(W)按以下优先级确定:
  1. 环境变量 SKYSA_HOME
  2. 脚本所在目录(仓库根)或其 data/ 子目录 —— 含已知数据文件者
  3. 脚本所在目录的父目录(旧布局: F:\\trails-in-the-sky\\SkyStructureAligner + 数据在父目录)
resolve(name) 在 W 与 W/data 中查找数据文件。
"""
import os

_HERE = os.path.dirname(os.path.abspath(__file__))
_KNOWN = ('evo_structure.json', 'evo_structure_sc.json', 'evo_structure_3rd.json',
          'additional_voice_sc.json', 'remake_structure_sc.json')

def find_root():
    env = os.environ.get('SKYSA_HOME')
    if env:
        return env
    for base in (_HERE, os.path.dirname(_HERE)):
        for d in (base, os.path.join(base, 'data')):
            if any(os.path.exists(os.path.join(d, k)) for k in _KNOWN):
                return d
    return _HERE

W = find_root()

def resolve(name):
    """数据文件定位: W -> W/data；找不到返回 None（调用方决定是否可选）"""
    for d in (W, os.path.join(W, 'data')):
        p = os.path.join(d, name)
        if os.path.exists(p):
            return p
    return None

def require(name):
    p = resolve(name)
    if p is None:
        raise SystemExit(f'未找到数据文件: {name}（放入 {W} 或 {os.path.join(W, "data")}，'
                         f'或运行 run.py 自动下载）')
    return p
