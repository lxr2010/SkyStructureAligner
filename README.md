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
| `match_result_sc_detailed.csv` | 16+9 列详表：RemakeVoiceID（合成号，默认100000起，`--new-id-start N` 可覆盖，行级唯一）/ RemakeOriginalVoiceID（原始内嵌语音表ID，6,194行）/ 场景行号 / RemakeFunction+Block / Old\*（EVO语音ID/角色/台词）/ Evo\* 结构定位 / 中文翻译 / SpeakerCheck / Annotation / 说话人四列：**RemakeSpeakerID**（说话人ID）、**RemakeCharacterDisplay**（运行时显示名，变装/匿名，791行）、**EvoCharacterDisplay**（EVO角色名，char_names）、**EvoSpeakerNotes**（前缀归属 main/shared/npc + char_id全局/局部 + 本行实体投票分裂实锤） |
| `my_match_result_sc.csv` | 匹配中间结果（含 Candidates 全量、Source 分层来源） |
| `speaker_review_sc.csv` | 说话人审查长表：一行一候选，含被拒候选/EVO结构/编号解释/Verdict 空列 |
| `voice_reuse_review_sc.csv` | 同源语音复用冲突表：同 RemakeOriginalVoiceID 的多行匹配到不同 EVO 语音（多为动物音效族/差分），附全组成员对照与 Verdict 空列 |
| `speaker_map_sc.json` / `speaker_map_scene_sc.json` | 说话人映射（全局 + 场景条件） |

仅下载数据资产：`uv run python run.py --game sc --download-only`

## 说话人/语音统一查询（s7 + voice_lookup_query）

回答"Remake 脚本某行的语音是什么情况"：说话人（含 **VAR 动态传参/引擎回调/动态槽**
等不确定性）、运行时显示名（变装/匿名，日/中双语）、语音号、EVO 匹配
（前缀×角色ID，标注 **多人共用前缀** 与 **场景依赖**）。详见 [docs/voice_lookup.md](docs/voice_lookup.md)。

```bash
uv run python s7_build_voice_lookup.py --game sc --py-dir py/ [--py-dir-sc py_sc/] [--game-dir "<游戏目录>"]
uv run python voice_lookup_query.py mp2000_ev 62412        # 行查询
uv run python voice_lookup_query.py --entity "20700|女性の声"   # 实体查询(含per-scene EVO映射)
uv run python voice_lookup_query.py --shared               # EVO多人共用前缀
```

配套管线改动：s1 结构带说话人不确定性/显示名/行号；s2 `--prefix-stats` EVO前缀归属
（main/shared/npc）；s4 匹配细化「说话人不定·VAR」「不对应·共用前缀」并输出 SpeakerNote
（s6 详表/审查表透传）；review_agent 增加 `rt.py speaker` 说话人辨析命令。

## Remake 语音表 t_voice（可选，RemakeVoiceFilename 列）

`t_voice_{game}.json` 属 Remake 解包产物，**不随 Release 分发**。如需 RemakeVoiceFilename 列，
用你自己的游戏文件生成（放进仓库根或 data/）：

```powershell
python kuro_dlc_tool/sky_extract_pac.py "<游戏目录>\pac\steam	able.pac"   # -> table/t_voice.tbl
python KuroTools/tbl2json.py table/t_voice.tbl                                # -> t_voice.json
# 再精简为 {id: {f: 文件名, t: 字幕}} 存为 data/t_voice_sc.json（或 t_voice_fc.json）
```

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

## 自动校对智能体（review_agent/）

对匹配结果做**逐块自动化校对**——Flash 级模型即可可靠驱动，无需强模型。详见 [review_agent/README.md](review_agent/README.md)。

### 架构

```
主智能体（编排层）
  ├─ 1. autook        零token预检清扫（~20%的块直接过，判据与Agent等价）
  ├─ 2. wave_partition  把剩余待办切成 K 份互不重叠的子智能体任务包
  ├─ 3. 派发 K 个子智能体（ZCode Agent / API直连均可）
  │     子智能体用 claim 认领块（租约45min防并发撞块）
  │     工具循环: pack → runcheck → findmany → submitmany/submitmap
  └─ 4. 汇总 review_pack/verdicts.jsonl
```

### 三种启动方式

| 方式 | 命令 | 适用场景 |
|---|---|---|
| ZCode 子智能体 | 在 ZCode 中「校对 N 个块」 | 交互式，已实测 |
| API 直连 | `RT_BASE_URL=... RT_API_KEY=... uv run python agent_runner.py --blocks 3` | 批量无人值守 |
| autook 预检 | `uv run python rt.py autook` | 开工前一键清扫 |

### 工具套组（rt.py，弱模型专用）

模型**零编码、零文件读取**——所有编码（GBK/CP932）、检索、结构解析都固化在工具里：
`todo / claim / pack / runcheck / vid / find / findmany / submit / submitmany / submitmap / autook`

### 校对内容

- **已匹配行**（A 类）：文本核对（归一化全等或长句≥90）、存在性、块对应（录音序号连续递增 = 锚点 run）、说话人、复用冲突
- **未匹配行**（B 类）：归一化全等检索 → 角色约束 → 锚点插值 → t_voice 反查 → additional 语音池
- **Verdict**：OK / WRONG（须给 correct_vid）/ SUSPECT / FOUND（须给 correct_vid）/ CANDIDATES / NO_VOICE / UNRESOLVED

### 模型要求与实测

| 模型 | 完成度 | 工具调用 | token/行 | 耗时 | 推荐 |
|---|---|---|---|---|---|
| **GLM-5.3-Flash** | 32/32 | 9 次 | 6.7K | 4 分钟 | ✅ |
| 强模型 + rt.py | 76/76 | 34 次 | 24.6K | 11 分钟 | 💪 高配 |
| autook 预检 | 全量 | 0 | 0 | <1 分钟 | ✅ 先跑 |
| 本地 MoE 小模型 | 28/32 | 35 次 | — | 7 分钟 | ❌ |

模型选择要求：**工具调用可靠性 ≥95%**（BFCL≥60）、指令遵循（任务书含反模式禁令）、日语文本比对能力。详见 [review_agent/README.md](review_agent/README.md) 的能力要求分级。

### 全量费用预估

全量 29,305 行（SC Demo），GLM-5.3-Flash API 标准价：**≈¥69-137**（autook 预检后 ¥69；限时五折 ¥35）。走 coding-plan 订阅配额零边际成本。

## 人工审查工作流（自动校对之后的残余）

1. `voice_reuse_review_sc.csv`（同源语音复用 ~70 行）：同一 Remake 语音被多行引用且各配了不同 EVO 语音，从组内选正确 take 或统一；
2. `speaker_review_sc.csv`（说话人 ~400 行）：按 ReviewReason 过滤，候选一条一行，填 Verdict；
   确认后的映射回填 `speaker_map_sc.json` 重跑可自动转正；
3. 多候选行（`MatchType=multi`）：Candidates 列全量列出，人工二选一；
4. 自动校对产出的 `WRONG/SUSPECT/CANDIDATES` 行：从 `review_pack/verdicts.jsonl` 筛选，按 Agent 给出的证据复核；
5. 对比其他匹配器（可选）：`tmp_gap_review_sc.py` 生成与线性匹配的差异表
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

## 鸣谢

本项目受以下开源项目启发或直接受益：

- [TrailsInTheSkyRemakeScriptAligner](https://github.com/lxr2010/TrailsInTheSkyRemakeScriptAligner) — 线性匹配前作（synonyms 归一化、additional_voice 数据、提取器设计）
- [SoraVoiceScripts](https://github.com/ZhenjianYang/SoraVoiceScripts) — EVO 语音脚本（evo_structure 数据之源）
- [KuroTools](https://github.com/nnguyen259/KuroTools) — scena 反编译
- [kuro_dlc_tool](https://github.com/eArmada8/kuro_dlc_tool) — script.pac 解包
- [Ingert](https://github.com/Aureole-Suite/Ingert) — scena 反编译（另一方案）

> ### Acknowledgements
>
> This project is inspired by and directly benefits from the open-source projects listed above.

---

## 版权声明

- 本仓库的**脚本代码**以 [GPL-3.0](LICENSE) 发布。
- 本项目处理涉及的游戏脚本文本、语音、图像及其他资产，其著作权与相关权利**归原游戏公司及权利人所有**（© Nihon Falcom Corp. 及各地区发行商）。**本项目对这些游戏资产不主张任何权利，并放弃一切权利主张**；所分发的派生数据（evo_structure 等）仅为结构与索引信息，随附上游署名（见 [NOTICE](NOTICE)），仅供学习研究。
- 仅用于学习、研究与非商业交流，**严禁**将本项目代码、处理结果或衍生资源用于任何商业用途。
- 使用者应自行确保其行为符合所在地法律法规及相关游戏/平台协议。

> ### Copyright
>
> - The **scripts** in this repository are licensed under [GPL-3.0](LICENSE).
> - All game scripts, voices, images, and related assets processed by this project belong to the original rights holders (© Nihon Falcom Corp. and publishers). **The author claims no rights over these game assets and waives any such claims.** Derived data files are structure/index information only, distributed with upstream attribution (see [NOTICE](NOTICE)) for study and research.
> - For learning, research, and non-commercial use only. Commercial use is strictly prohibited.
> - Users are responsible for compliance with applicable laws and agreements.

## 已知边界

- 无候选行中 ~97% 为「EVO 无此文本」（Remake 新增/无语音台词），属内容上限；
- 短泛用句（『……』『うん』类）多 take 无法自动区分，保留 multi 待人工；
- fc 流程沿用 `remake_jp` 目录约定（mp*.py），sc/3rd 见 run.py。
