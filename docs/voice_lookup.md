# 说话人/语音统一查询接口（voice_lookup）

> 一次构建，回答"Remake 脚本某行的语音到底是什么情况"：说话人（含不确定性）、
> 显示名（日/中）、语音号、EVO 匹配（前缀×角色ID，含多人共用/场景依赖标注）。

## 快速开始

```bash
# 一键构建（从零：自动下载 Release 资产 + 重跑 s1-s6 + 建索引）
uv run python s7_build_voice_lookup.py --game sc \
    --py-dir <日文反编译py目录> \
    [--py-dir-sc <简中反编译py目录>] \
    [--game-dir "<游戏安装目录>"]        # 含 pac/steam/table.pac，用于 t_name 角色名；缺省降级为无名模式

# 复用已有 my_match_result，只重建索引
uv run python s7_build_voice_lookup.py --game sc --py-dir <py目录> --skip-pipeline

# 查询
uv run python voice_lookup_query.py mp2000_ev 62412
uv run python voice_lookup_query.py --entity "20700|女性の声"
uv run python voice_lookup_query.py --list 21000
uv run python voice_lookup_query.py --shared
```

产物 `data/voice_lookup_index_sc.json`；Python 接口 `voice_lookup_query.VoiceLookup`。

## 输入

| 参数 | 说明 |
|---|---|
| `file` | scena 场景名（去 .py），如 `mp2000_ev` |
| `line` | **日文反编译 py 行号**，与 match_result 详细表 `RemakeScenaScriptLineno` 同坐标系 |

## 输出与状态

```jsonc
{
  "status": "CONFIRMED|NO_VOICE|MULTI_SPEAKER|MULTI_OPTION|UNCERTAIN|NOT_FOUND",
  "speaker_id": 21000, "name_jp": "…",                    // t_name.tbl
  "display_name_jp": "女性の声", "display_name_sc": "女子的声音",  // chr_set_display_name 就近
  "voice_id": 32958, "text_jp": "…", "text_sc": "…",
  "candidates": [...],          // 仅 MULTI_OPTION
  "speaker_note": "…",          // 不确定原因: VAR回溯/UNDEF占位/引擎回调/共用前缀说明
  "segment": "mp2000_ev::21000|女性の声::r1",   // 场景×角色段
  "entity": {
    "vote_scope": "global_unanimous|scene_dependent|no_vote",
    "evo": {"prefix": "088", "char_id": null, "char_id_consistent": false,
            "confidence": 1.0, "prefix_shared": false},
    "evo_scene_dependent": {"mp0000_ev": "021", "mp8010_01": "088"},
    "evo_in_this_scene": {"prefix": "088", "prefix_shared": false},
    "has_multi_shared_group": true
  },
  "evo_match": {                // 该行实际匹配到的 EVO 语音（来自 s4 的 my_match_result）
    "voice_file": "0880220220V", "prefix": "088", "char_id": "0x21",
    "match_type": "唯一", "speaker_match": "说话人不对应·共用前缀(语音角色088)", "prefix_shared": false
  }
}
```

| status | 判定 |
|---|---|
| `CONFIRMED` | 固定说话人 + 带语音号 |
| `NO_VOICE` | 固定说话人，无语音号（未配音行） |
| `MULTI_SPEAKER` | 多人合语音（显示名含 `＆`，如 エステル＆クローゼ 共用一个语音号） |
| `MULTI_OPTION` | VAR 动态解析出多个候选（`candidates`），或按不确定处理 |
| `UNCERTAIN` | 不确定：无静态调用点（引擎回调）/ UNDEF 占位 / 动态槽无名 |
| `NOT_FOUND` | 该行无对话命令 |

## 背景规则（为什么有这些限制）

**Remake 侧**：
- 说话人 = Cmd_text_00/06/13 的 arg0；`skind` 分 fixed/var（s1 已标注）。
- 20xxx/15xxx/61xxx 为动态事件槽，名字由 `chr_set_display_name(id,"名")` 运行时设置
  （变装/匿名/广播）；同场景同 ID 多名 = 槽位复用，按"角色段"切段。
- add_struct(nb_sth1=3, vals[0]==5) 数据表对话：说话人恒在 vals[2]（与内联镜像按语音号去重）。

**EVO 侧**：
- 语音文件前 3 位 = 语音前缀。主角段(001-022)对应全局 char_id(0x101+)；其余为 NPC 前缀，
  存在多人共用（088=播报员、423/425=强化猎兵…），见 `evo_prefix_stats_{game}.json`。
- script_data 的 character_id 中 ≥0x100 才是全局角色ID，0x8-0x20 是场景局部槽位（跨场景非同一人）。

**投票**（s7）：标志性台词（语料频次≤3 且长度≥4）+ 全局 char_id → 场景×角色段两级：
- 段内前缀分裂 → `evo_shared_prefixes`（EVO 多人共用）+ `has_multi_shared_group`
- 跨场景不一致 → `scene_dependent`（合法差异，保留 per-scene 映射）
- 全场景一致 → `global_unanimous`（char_id 仅在共识 ≥60% 时给出，否则为噪音置空）

## 与管线的关系

- s1 结构新增 `skind/disp/line` 字段（向后兼容，旧字段语义不变）。
- s2 `--prefix-stats` 产出前缀归属统计（main/shared/npc），s4 用它把
  「说话人不对应」细分为「·共用前缀」（差异前缀为 shared/npc 时大概率是合法改派）。
- s4 新增 `说话人不定·VAR` 标签与 `SpeakerNote` 列（第 16 列）；s6 详表/审查表透传该列。
- s6 详表另含说话人四列：RemakeSpeakerID / RemakeCharacterDisplay（运行时显示名）/
  EvoCharacterDisplay（char_names 角色名）/ EvoSpeakerNotes（前缀归属+char_id全局/局部+
  投票分裂标注；"本行实体投票分裂"=该行所属角色段分裂实锤，"该前缀存在分裂记录"=他段共用提示）。
- rt.py 新增 `speaker <场景> <行号>` 子命令（校对 Agent 的说话人辨析出口）。
