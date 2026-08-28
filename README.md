# SkyStructureAligner — 空轨 1st Remake ↔ EVO 结构化语音匹配

基于**控制流结构（块图同构 + 函数级对齐）**的台词-语音匹配工具。
不依赖全局文本模糊搜索，以「锚点投票 + 沿边传播 + 段内/函数级精化」完成 Remake 台词 → EVO voice_id 的定位。

> 与 [TrailsInTheSkyRemakeScriptAligner](https://github.com/lxr2010/TrailsInTheSkyRemakeScriptAligner) 的线性匹配互补：
> 本工具以 `synonyms.normalize` 为唯一文本归一化入口（说话人约束优先），结构维度独立演进。

---

## 最终匹配结果（FC / 空轨1st）

| 指标 | 数值 |
|------|------|
| Remake 总台词 | 46,016 |
| **唯一确定** | **24,446（53.1%）** |
| 多候选（差分/复用，需人工二选一） | 776（1.7%） |
| 无候选（无语音/改写，硬上限） | 20,794（45.2%） |
| 说话人不对应 | 0（说话人约束融入锚点判定） |

对比 gt（match_result.csv 人工校对，matched 28,680）：
- 一致（我唯一 = gt）约 23,4xx
- 错配 116（其中差分 seq 差异为主）
- 少匹配 2,252（gt 有、我无/多候选不含）—— 已全部导出供检查

详细对比报告见 `diff_export.csv`。

---

## 流水线（4 层）

```
0. 结构重建
   build_remake_ast.py      Remake: 函数→基本块(Label/块尾JUMP/Return)→台词{speaker,text}，含 next/jump/cond 边
   build_evo_structure_v2.py EVO:   py(结构+边) × msg(跨行台词/多段[x02])，voice_id 关联，switch 边
                            （须排除 lambda_ 异步任务、talk_num 按顺序位置关联）

1. 块级对齐（非 TK 剧情流）
   锚点 = (角色码, normalize) 两边唯一        ← 说话人约束优先：约修亚≠艾丝蒂尔
   投票 → 1:1（77%）/ 1:N 按锚点 EVO 块变化点拆分
   沿边传播（语义镜像：Remake cond_false ↔ EVO cond_true）+ 候选不唯一时说话人消歧

2. 段内精化（句级）
   段 EVO 块内：(角色码+文本精确) → 编辑距离 th70（rapidfuzz）
   命中判定注意：vid_to_evoblk[cand] in gt_blocks（voice_id vs EVO块，类型别搞错）

3. 函数级对齐（孤立块兜底）
   函数内长句锚点投票到 EVO 函数（67% 唯一）
   函数内短句：说话人 + 编辑距离 th80 → 救回 ~800（97.4% 正确）

4. 分流
   TK_xxx（选项菜单 66%/单次对话 32%）→ 角色码+文本定位，跳过图匹配（与 EVO 剧情块异构）
   非 TK_xxx（EV/chr/LP/ST…剧情流）→ 图匹配（上述 1-3）
```

## 关键结构结论（详见 docs/）

1. **条件分支语义镜像**：Remake `JumpWhenFalse`（假分支跳转）↔ EVO `Jc`（真分支跳转）
2. **JumpWhenFalse/True 是块内条件跳转，不是块尾**；块尾只有 `JUMP`/`Return`（fallthrough 修正使有台词块 6,486→10,737）
3. **Remake 台词含 `<k>/<P1>/<R>` 等控制码**，须 `re.sub(r'<[^>]*>','',text)`（去后锚点 +7,390）
4. **EVO msg 一条 ChrTalk 可含多段 [x02]**（每段一个 voice_id）；`_1/_2` 变体 talk_num 重新编号，须按顺序位置关联
5. **lambda_57EA 等异步任务**内部 label/Jump 污染主流程，须排除
6. **差分对话**：Remake 同 (speaker,text) 重复 2 次（2,888 处）；EVO Switch case 分支，核心台词「文本同 voice_id 不同」（5.1%）；核心在 Remake 被合并为汇合块，理论不可唯一
7. **ch 记录 (角色码,region,seq) 是完整坐标**：说话人是第一强约束；region+seq 是场景内单调坐标（夹逼依据）
8. **漏配主因不是文本改写**：99% 的漏句编辑距离 ≥90，是块级对齐失败（孤立 `_entry` 无前驱 1,160 / 无锚点串 503 / 候选不唯一 259）

## 数据依赖

| 文件 | 说明 |
|------|------|
| `script_data_fc.json` | EVO 语音台词表（voice_id+text+source_file），来自项目 v1.0-extra release |
| `match_result.csv` | gt 人工校对结果（28,680 matched），来自项目 v1.0 release |
| `additional_voice_fc.json` | 脚本外语音 1,987 条（补 +53） |
| `speaker_map_fc.json` | Remake speaker → EVO 角色码映射 |
| `SoraVoiceScripts-zhenjian/cn.fc/{py,out.msg}/` | EVO 反编译脚本（py 控制流 + msg 台词） |
| `remake_jp/mp*.py` | Remake 反编译脚本 |

## 运行

```bash
# 1. 重建结构（依赖上述数据源）
python3 build_remake_ast.py       # → remake_structure.json
python3 build_evo_structure_v2.py  # → evo_structure.json

# 2. 生成匹配结果
python3 generate_my_match.py       # → my_match_result.csv（46,016 行）

# 3. 导出错误/差异报告
python3 export_diff.py             # → diff_export.csv（错配+少匹配 2,368 条）

# 辅助验证
python3 divert_e2e.py              # 分流端到端精度（gt 验证）
python3 segment_refine_v2.py       # 段内精化阈值实验
```

## 输出文件

| 文件 | 内容 |
|------|------|
| `my_match_result.csv` | 每句 Remake 台词 → MyVoiceId / Candidates / MatchType / Source / SpeakerMatch |
| `diff_export.csv` | 错配 116 + 少匹配 2,252，含两侧结构/控制流/位置/类型 |
| `docs/结构匹配重大发现记录.md` | 九节完整发现记录 |
| `docs/特征文档_差分对话与TK聚合.md` | 差分对话 & TK_xxx 聚合特征 |
