# SkyStructureAligner — 空之轨迹 Remake ↔ EVO 结构化语音匹配

基于**控制流结构（块图 + 连续录音段 + 位置约束）**的台词-语音匹配工具。
把 Remake（空轨 1st / 2nd Demo）的台词对齐到 EVO 版语音 ID，输出 FC `match_result.csv`
式 16 列详表与配套人工审查表。与
[lxr2010/TrailsInTheSkyRemakeScriptAligner](https://github.com/lxr2010/TrailsInTheSkyRemakeScriptAligner)
的线性/LLM 匹配互补：本工具以结构上下文为主、文本为辅，无 LLM 依赖。

> 与线性匹配（v1.1.0）对同一 29,305 行 SC Demo 数据对比：双匹配一致率 98%。

---

## 快速开始（一键）

前置：Python 3.12+、[uv](https://docs.astral.sh/uv/)、Remake 反编译脚本（见下）、（可选）简中反编译脚本。

```bash
git clone https://github.com/lxr2010/SkyStructureAligner.git
cd SkyStructureAligner
uv venv && uv pip install jaconv rapidfuzz zstandard

# SC (2nd Demo) 全流程：自动下载 EVO 结构等数据资产 -> 匹配 -> 详表
uv run python run.py --game sc --py-dir <日文反编译py目录> [--py-dir-sc <简中py目录>]
```

产物在 `data/`：

| 文件 | 内容 |
|------|------|
| `match_result_sc_detailed.csv` | 16+6 列详表：RemakeVoiceID / 场景行号 / RemakeFunction+Block / Old\*（EVO语音ID/角色/台词）/ Evo\* 结构定位 / 中文翻译 / SpeakerCheck / Annotation |
| `my_match_result_sc.csv` | 匹配中间结果（含 Candidates 全量、Source 分层来源） |
| `speaker_review_sc.csv` | 说话人审查长表：一行一候选，含被拒候选/EVO结构/编号解释/Verdict 空列 |
| `speaker_map_sc.json` / `speaker_map_scene_sc.json` | 说话人映射（全局 + 场景条件） |

仅下载数据资产：`uv run python run.py --game sc --download-only`

## Remake 脚本反编译（一次性）

```powershell
# 工具: kuro_dlc_tool(解包) + KuroTools(反编译), 参考 TrailsInTheSkyRemakeScriptAligner 的 decompile_pac.ps1
git clone https://github.com/eArmada8/kuro_dlc_tool.git
git clone https://github.com/nnguyen259/KuroTools.git
python kuro_dlc_tool/sky_extract_pac.py "<游戏目录>\pac\steam\script.pac"      # -> script/scena/*.dat
foreach ($f in Get-ChildItem script\scena\*.dat) { python KuroTools/dat2py.py --decompile True --markers False $f }   # -> py/
# 简中版: 对 script_sc.pac 重复一次（翻译列用）
```

EVO 侧结构已随 Release 分发（`evo_structure{,_sc,_3rd}.json`），无需自建；如需重建：

```bash
git clone https://github.com/ZhenjianYang/SoraVoiceScripts.git SoraVoiceScripts-zhenjian
uv run python s2_build_evo_structure.py sc    # 或 fc / 3rd
```

## 流水线（分步）

```
s1  Remake 反编译 py -> 块图结构（函数/基本块/边 + 台词）
    变体: Cmd_text_00/06 普通对话, 13 带立绘(UNKNOWN_05_13 为别名), 08 选项/系统文本
derive_speaker_map  锚点投票推导 说话人码->EVO角色码（全局 + 场景条件两层，65535 旁白哨兵排除）
s2  (可选) SoraVoiceScripts -> EVO 结构（ChrTalk/NpcTalk/AnonymousTalk, CP932外字, 注音块, 6位系统音）
s4  结构匹配（见下）
s6  详表生成（FC 16 列 + RemakeFunction/Block + EvoScene/Function/Block + SpeakerCheck）
```

## 匹配层架构（s4 内部，全部带 Source 标注与验证一致率）

**分流**：`TK_/QS*` 全局文本直配（Remake 重构了测验分支，块图对不齐）｜其余走图匹配。

| 场景 | 层 | 机制 | 验证 |
|------|----|------|------|
| 1:1 | 锚点+沿边传播+段内夹逼 | 块图镜像语义（Remake cond_false ↔ EVO cond_true） | 基石 |
| 1:1 | 块内模糊≥80 / 块内文本唯一·角色放松 / 全局唯一·段外 | 无候选救援 | 86-94% |
| 1:N | 夹逼 / 场景唯一 / 最近邻 | 邻行已定 vid 的末四位连续性 | 84-97% |
| N:1 | 块内连续段（精确/复制/复用/模糊链） | 整块串链：同场景+尾号递增+完整链唯一 | 92-97% |
| 说话人 | 场景条件映射 / 链内角色校验 | 广播音依场景映射；角色冲突打标 | 95% |

**块映射代数**：两侧块对应是**多对多关系**——1:1（73%）/ 1:N 拆分 / N:1（粒度差、文本复用、事件复制）。
以台词行为原子，块结构只提供定位上下文。

## 人工审查工作流

1. `speaker_review_sc.csv`（说话人 ~400 行）：按 ReviewReason 过滤，候选一行一条，填 Verdict；
   确认后的映射回填 `speaker_map_sc.json` 重跑可自动转正；
2. 多候选行（`MatchType=multi`）：Candidates 列全量列出，人工二选一；
3. 对比其他匹配器（可选）：`tmp_gap_review_sc.py` 生成与线性匹配的差异表
   `gap_review_sc.csv`（BlockAlert 同块告警）+ `gap_block_review_sc.csv`（块级汇总）。

## 数据资产（Release v1.0.0）

| 资产 | 说明 |
|------|------|
| `evo_structure.json / _sc / _3rd` | EVO 三部曲控制流结构（源自 SoraVoiceScripts，解析含 NpcTalk/外字/注音/尾部找回） |
| `additional_voice_{fc,sc,3rd}.json` | 脚本外语音（继承自前项目） |
| `speaker_map_{fc,sc}.json` | 说话人映射种子 |

## 许可

代码 GPL-3.0（继承 lxr2010/TrailsInTheSkyRemakeScriptAligner，吸收其 synonyms.py）。
数据来源与署名见 [NOTICE](NOTICE)。

## 已知边界

- 无候选行中 ~97% 为「EVO 无此文本」（Remake 新增/无语音台词），属内容上限；
- 短泛用句（『……』『うん』类）多 take 无法自动区分，保留 multi 待人工；
- fc 流程沿用 `remake_jp` 目录约定（mp*.py），sc/3rd 见 run.py。
