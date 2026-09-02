# 校对 Agent 工具套组

对 SkyStructureAligner 的匹配结果做**自动化逐块校对**：Flash 级模型也能可靠完成，全量 29,305 行约 ¥69-137（GLM-5.3-Flash API）或走 coding-plan 订阅配额零边际成本。

## 快速开始

### 方式一：ZCode 子智能体（推荐，已实测）

Agent 定义文件（放在 `~/.zcode/agents/`）已包含全部指令，创建后在 ZCode 中说「校对 N 个块」即可：

```
模型: GLM-5.3-Flash (或任何支持工具调用的模型)
指令: 见下方「Agent Instruction」或直接用 REVIEW_AGENT_FLASH.md 的正文
```

### 方式二：agent_runner.py（纯 API 直连）

```bash
export RT_BASE_URL=https://open.bigmodel.cn/api/paas/v4
export RT_API_KEY=<你的key>
export RT_MODEL=glm-5.3-flash
cd review_agent
uv run python agent_runner.py --blocks 3 --game sc
```

也支持本地 llama-server（需工具调用支持）：
```bash
export RT_BASE_URL=http://<本地llama-server地址>/v1
export RT_MODEL=Qwen3.6-35B-A3B
```

### 方式三：autocheck 预检（零 token，先跑这个）

```bash
cd review_agent
uv run python rt.py autocheck <场景> <函数>   # 单块
```

四条全满足才批量 OK：全匹配 + 体检零异常 + 文本全等 + 无审查标记。全量约 21% 的块可直接过，其余才需要模型。

## 工具命令（rt.py）

| 命令 | 用途 | 消灭的苦活 |
|---|---|---|
| `todo [N]` | 按优先级领块 | 任务调度 |
| `pack 场景 函数` | 工作包：全行+匹配+EVO邻域+说话人码 | 读CSV/关联大JSON |
| `runcheck 场景 函数` | 自动体检：序号断裂/倒序/跨场景/低相似/复用分歧 | 连续性分析 |
| `vid 语音ID` | 单语音详情+msg原文 | 编码安全读日文源文件 |
| `find 文本 [--char] [--scene]` | 归一化检索EVO台词 | 模糊检索+术语归一化 |
| `submit '<JSON>'` | 提交裁定（自动校验） | 保证JSONL不写坏 |
| `autocheck 场景 函数` | 确定性预检（零token批量OK） | 干净块不进LLM |

## 校对内容

每块按行匹配状态分流：

**已匹配行（A 类精核）**：
1. 文本核对（归一化全等或长句 ≥90；存疑时 vid 调 msg 原文复核）
2. 存在性（语音ID在EVO结构中找得到）
3. 块对应（EVO 落点聚在同一函数；录音序号连续递增 =「锚点 run」）
4. 说话人（vid 前3位 = SpeakerChar；不符则记录 Remake 改派）
5. 复用冲突（同 Remake 语音多行配到不同 EVO 语音 → 人工判断）

**未匹配行（B 类寻配）**：
1. 归一化全等检索（任意角色）
2. 同角色高相似
3. 锚点插值（前后已定行的序号区间内找）
4. t_voice 反查（Remake 原始语音表字幕比对）
5. additional_voice（脚本外语音池）

**Verdict 七选一**：

| 值 | 含义 | 附加要求 |
|---|---|---|
| OK | 核对通过 | — |
| WRONG | 错配 | 必须给 correct_vid + 证据 |
| SUSPECT | 存疑 | 说明理由 |
| FOUND | B 类找到唯一匹配 | 必须给 correct_vid |
| CANDIDATES | B 类多候选 | 列出全部候选 |
| NO_VOICE | EVO 有对应行但未配音 | evidence 给行位置 |
| UNRESOLVED | EVO 连对应行都没有 | — |

## 产出

| 文件 | 内容 |
|---|---|
| `review_pack/verdicts.jsonl` | 逐行裁定（UTF-8 JSONL，RemakeVoiceID 为主键） |
| `review_pack/progress.log` | 每块完成摘要（含各 verdict 计数） |
| `review_pack/<场景>_<函数>.json` | 工作包（review_batch.py 生成） |

## 人工复核：match_voice_checker.html

自动校对 + `apply_verdicts.py` 产出 `*_detailed_corrected.csv` 之后，用**语音试听检查页**做逐行人工复核。
单文件网页（无构建、无依赖），支持：

- EVO（og​​g）与 Remake（opus）双侧试听，本地拆包语音优先、在线 CDN 回退，双音量独立调节
- 多选过滤器（脚本/判定/类型/说话人）、判定 chips、人工状态筛选、行多选/反选/导出
- 行级人工校对编辑器：候选下拉（↑↓/滚轮切换并自动试听）、状态随语音自动联动、
  翻页/刷新不丢（localStorage）、导出 `manual_verdicts_<N>.jsonl`
- `apply_manual_verdicts.py`：把人工裁定应用到 `*_detailed_corrected.csv`，产出
  `data/match_result_*_detailed_manual.csv`（change 行反查 EVO 结构补全 Old\*/Evo\* 列）+
  `manual_apply_summary.json` 应用摘要；网页端「导出CSV(含人工)」为同规则的轻量版

```bash
python voice_check_server.py        # 起服务（--voice 可指定本地语音目录），浏览器开
                                    # http://127.0.0.1:8613/review_agent/match_voice_checker.html
```

详细用法见 **[manual_review_guide.md](manual_review_guide.md)**（图文版）。

## 实测数据

| 模型 | 块行数 | 工具调用 | token | 耗时 | 完成度 | 质量 |
|---|---|---|---|---|---|---|
| **GLM-5.3-Flash** + rt.py | 32 行 | 9 次 | 216K（6.7K/行） | 4 分钟 | 32/32 | 全部正确 |
| 强模型 + rt.py | 76 行 | 34 次 | 1.87M（24.6K/行） | 11 分钟 | 76/76 | 抓到 1 个真错配 |
| autocheck 预检 | 6,024 行 | 0 | 0 | < 1 分钟 | 全量 | 与 Agent 等价 |

## 本地模型实测（Qwen3.6-35B-A3B @ llama-server）

**不推荐用于本任务**。实测本地 llama-server 上的 Qwen3.6-35B-A3B（MoE 3B 激活，带思维链）：

| 指标 | 实测值 | 问题 |
|---|---|---|
| 完成度 | **28/32 行**（漏 4 行） | 思维链吃满 8192 token 后停摆 |
| 工具调用 | 35 次（含重复 pack ×2） | 效率低（Flash 仅 9 次） |
| 耗时 | 420 秒 | 比 Flash 慢 74% |
| 动作准确率 | 误调 `pack 3`（应为 `todo 3`） | 动作名混淆 |
| 判定质量 | 全 OK（该块本就干净） | 判定本身未出错 |
| 卡死 | 第二块开始后无响应 | max_tokens 打满后不恢复 |

**根本原因**：A3B 级模型的指令遵循能力不足以支撑多步工具循环——每轮思维链消耗大量 completion token，长会话累积后触发截断；工具调用偶发混淆动作名；且无漏行自查能力。

## 模型能力要求

选择校对 Agent 的模型时，按重要度排列：

### 必须具备（缺一不可）

1. **工具调用（Function Calling）可靠性 ≥ 95%**
   - 能正确区分 7 个命令名（todo/pack/vid/find/runcheck/submit/autocheck）不混淆
   - 能在单轮内发出多个工具调用（批量 submit 场景）
   - 参数格式正确（JSON 字符串、数组元素类型）
   - 参考：[BFCL V4](https://gorilla.cs.berkeley.edu/leaderboard.html) ≥ 60 分

2. **指令遵循（长指令 + 反模式禁令）**
   - 任务书约 2,500 token，含 4 条反模式禁令（禁逐 vid 枚举、无 issue 直接 OK 等）
   - 模型必须在整个会话中持续遵守，不因上下文增长而遗忘
   - A3B 级模型在此项上表现不佳（违反了「不逐条枚举」的禁令）

3. **日语文本比对能力**
   - 能判断两句日语台词是否「归一化全等」或「高相似」（sim≥90）
   - 理解注音块删除、控制码清洗等归一化规则的效果
   - 对『……』/『うん』等泛用短句不强行区分

### 强烈建议

4. **结构化输出（JSON）稳定性**
   - submit 的 verdict JSON 字段完整且类型正确
   - WRONG 必须带 correct_vid（10 位数字字符串）
   - 不需要完美——rt.py 的 submit 会校验并拒绝非法格式

5. **多轮上下文保持（≥ 8 轮工具循环）**
   - 一次块校对约 5-15 轮工具调用，模型须记住任务书规则与已处理行
   - 上下文窗口 ≥ 32K 即可（工作包约 15-30K token）

### 可以妥协

6. **推理深度**：本任务的判断逻辑已固化在 runcheck 等工具中，模型只需「读结论→判断→提交」，不需要深度推理。Flash 级足够。
7. **输出速度**：每块 4-10 分钟均可接受，非实时任务。
8. **长上下文（>128K）**：单块工作包不超 30K，无需超长窗口。

### 推荐档位

| 档位 | 代表模型 | 预期表现 | 备注 |
|---|---|---|---|
| ✅ 推荐 | GLM-5.3-Flash, Gemini Flash, Qwen Flash | 可靠完成，¥0.17/块 | BFCL≥60, 指令遵循好 |
| ⚠️ 勉强 | DeepSeek-chat, GPT-4o-mini | 可跑但效率低（203 调用/块） | 动作混淆偶发 |
| ❌ 不推荐 | 本地 A3B/MoE 小模型 | 漏行/卡死/混淆 | 指令遵循不足 |
| 💪 高配 | 强模型 + rt.py | 深度推理，能抓复杂错配 | 25 倍 token 成本 |
## Agent Instruction（ZCode 子智能体用）

直接粘贴 [REVIEW_AGENT_FLASH.md](REVIEW_AGENT_FLASH.md) 的正文（去掉 frontmatter）。

子智能体定义文件示例（`~/.zcode/agents/voice-align-reviewer.md`）：
```yaml
---
name: "voice-align-reviewer"
description: "空之轨迹 Remake↔EVO 语音匹配校对员"
color: yellow
model: "custom:builtin%3Abigmodel-coding-plan:GLM-5.3-Flash"
injectAgentsMd: false
---
（粘贴 REVIEW_AGENT_FLASH.md 正文）
```

## 性能报告

完整的多智能体校对性能统计（261 代理 / 85 波 / 29,305 行 / 331M token）见 [AGENT_PERFORMANCE_REPORT.md](AGENT_PERFORMANCE_REPORT.md)，含：
- 任务总量与裁定分布
- 时间/token/工具调用分阶段演进
- 工具层优化实测（pickle 缓存 5 倍提速、批量命令 0.18 调用/行）
- UNRESOLVED 批量再分类（2 秒/10,749 行/零 LLM）
- 最终校对结果统计
- 成本汇总

## 注意

- 工作目录须为 `review_agent/`（或设 `SKYSA_HOME` 指向 data/）
- 依赖 `paths.py`/`synonyms.py`（在仓库根目录，rt.py 已自动处理）
- `review_pack/` 可安全删除重跑（verdicts 从头累积）
- `review_backup/`、`data/*.csv`、`data/unres_reclassify.json` 等校对产物仅存本地，不入库
