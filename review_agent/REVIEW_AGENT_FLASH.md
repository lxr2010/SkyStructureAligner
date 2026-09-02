# 校对任务书（Flash 版 · 仅用工具，不读源文件）

你是语音匹配校对员。只允许调用 `rt.py` 的命令，**禁止直接读任何源文件/JSON/CSV**（编码陷阱多，工具已替你处理）。所有输出中文。

## 命令速查

```
uv run python rt.py todo 5                      # 领任务：前5个待办块
uv run python rt.py claim <代理ID> [场景 函数]    # 直接领任务包：认领待办块+返回精简工作包(租约防撞块)
uv run python rt.py pack <场景> <函数>           # 工作包：块内全部行(行内含evo_char/evo_text/seq/scene/func/talk_num/新旧block/rvfile)
uv run python rt.py runcheck <场景> <函数>       # 自动体检：序号断裂/倒序/跨场景/文本低相似/复用分歧
uv run python rt.py vid <10位语音ID>            # 语音详情：EVO结构定位+msg原文
uv run python rt.py find <文本> --char 003 --scene 047   # 检索EVO台词(--char=角色码=vid前3位; --scene=语音场景3位数字=vid第4-6位; --evoscene T0131_1=EVO结构场景名)
uv run python rt.py rowhint <场景> <函数> <行号|RemakeVoiceID>   # 行级结构提示: 该行的录音组/双侧锚点/take区间+区间内未占用候选(sim排序)
uv run python rt.py submit '<JSON>'             # 提交裁定(自动校验)
uv run python rt.py findmany '[["文本","003"],..]'   # 批量检索(B类必用,一个进程跑整批; '-'读stdin)
uv run python rt.py submitmany '[{verdict},..]'      # 批量提交(一个进程整批写入,每批10-20条; '-'读stdin)
uv run python rt.py submitmap <场景> <函数> '{"行号":"OK",..}'   # 整块批量OK/UNRESOLVED(服务端回填id,已裁定自动跳过)
```

## 传参规则（批量命令通用）

- **过滤参数两套体系勿混**：`--char`/findmany第2元 = vid前3位角色码；`--scene`/findmany第3元 = vid第4-6位**语音场景3位数字**（如047）；`--evoscene`/findmany第4元 = **EVO结构场景名**（如T0131_1）。find/findmany 结果 hits 里的 `scene` 字段是 EVO 结构场景名——它对应 `--evoscene`，不是 `--scene`。
- 两种传参：①内联：`submitmany '[{...}]'`（JSON 外层用单引号包裹，内部一律双引号）；②stdin：`submitmany - <<'EOF'` 换行写 JSON 再换行 `EOF`（长内容或含引号时用）。
- heredoc 结束符必须带引号（`<<'EOF'`），防止 shell 展开；submitmap 的 stdin 形式是 `submitmap <场景> <函数> - <<'EOF' ... EOF`。
- 传错参数时工具会返回 usage 示例，照着重试即可，不要盲试其他形式。

## 领域知识（最小集）

1. 语音ID三段式：`003 028 0674` = 角色3 + 语音场景3 + 录音序号4。**同块台词的序号应连续递增**（差分对并列1-2个）；序号断裂/跳场景=疑点。
2. 块内已匹配行构成"锚点run"：如 0180→0182 连续段。run 外的孤立take（哪怕文本全等）= 错配嫌疑。
3. `sim=100`（find 结果）= 归一化全等。长句 ≥90 可接受；短句（……/うん/えっ 类）多take属天然歧义，**不强行裁定**。
4. 说话人：vid前3位 应等于 pack 行的 evo_char；不符但录音实况如此 → 只记录不纠错。注意：spk 是 remake 侧码表（0/65535/21000 等），**不能**用作 find/findmany 的角色过滤值。evo_char 若为 `0x` 开头的十六进制说话人码（如 0xA/0x10A）**也不能**当过滤值（会静默零命中）——过滤一律用 vid 前3位数字：已匹配行看自己 vid 前缀，未匹配行用同块同说话人已匹配行的 vid 前缀，无把握就不过滤。
5. 检索范围 = evo_structure + additional 补充语音表：find/findmany 命中 `scene:"additional"`、`vid` 返回 `source:"additional"` 均为补充表录音（现配为补充表的行，pack 行内也有文本可比对）。B 类行对补充表的唯一 sim=100 命中 + 锚点/语境佐证 → 同样可 FOUND+correct_vid。仅当 vid 提示「不在evo_structure也不在additional补充表」才是 script_data/新录音，此时查证不到就 UNRESOLVED。
6. EV 事件块常有"平行分支会话"：对齐器易把**相邻块同文本 take** 错配到本块，而本块自己的录音组 take 成为孤儿。信号：现配 vid 的 evo_block/录音组与本块锚点 run 不符、`vid` 查得 referenced_by_remake 为空且与 run 内正句成对 → `WRONG` + correct_vid（填孤儿 take）。
7. **find/findmany 双程兜底（工具自动行为）**：带 `--char/--scene/--evoscene`（或 findmany 第2-4元）过滤、且过滤结果中无有声 sim=100 命中时，工具**自动追加一轮无过滤检索**，在结果里附 `unfiltered_fallback`（无过滤前5）。所以过滤零命中/无全等时**不要再手动去掉过滤重查一遍**——直接读该字段；hits 与 `unfiltered_fallback` 都空才是真无命中。
8. **rowhint = 锚点区间的现成答案**：给出行号或 RemakeVoiceID，返回该行所在录音组、双侧锚点（行号+vid）、take 区间（如 `0180-0182`）和区间内**未占用候选**（按 sim 排序前6）。A 类疑点行（序号断裂/跨场景）用它一步定位"区间内是否有全等 take"；B 类候选行用它验证"命中是否落在锚点 run 内"。返回的候选只是**证据**：sim=100 且区间内唯一 → 结论性强；低 sim 候选是改写形态，仍按改写判定规则（语境/唯一性）裁量；区间不可用（无双侧同组锚点）时不要硬推。

## 反模式禁令（重要）

1. **禁止逐条 vid 枚举查证**：pack 行内已含每条已匹配行的 evo_text/evo_scene/evo_func——直接读它对比，`vid` 命令只用于 runcheck 报出的疑点行。
2. **已匹配+无 issue 的行直接 OK**，不要任何额外查证调用。
3. find 结果 sim=100 且只有 1 支 → FOUND，不需再 vid 确认。
4. 每块工具调用预算：**行数 × 1.5 次**封顶。超了说明你在走弯路。
5. **不要手动做工具已内置的事**：过滤检索零命中后去掉过滤重查（`unfiltered_fallback` 已自动给出）；自己从 pack 行推算锚点区间（`rowhint` 一次给出）。

## 每块流程

1. 领块：有分配清单时 `claim <代理ID> <场景> <函数>` 直接取工作包；自主领块用 `claim <代理ID>`（自动取队列首块，`todo` 仅用于查看队列）。领到后 `runcheck` 体检。
2. **已匹配行**（有 vid）：逐条核对文本（pack 行内 text vs evo_text）+ runcheck 的 issues：
   - runcheck 无 issue 且文本相等/高相似 → `OK`
   - `序号断裂`类 issue：先 `rowhint <场景> <函数> <行号>` 看区间内是否有全等候选（sim=100 唯一 → 直接 `WRONG` + correct_vid）；区间不可用再用 `find <该行文本> --char <角色> --scene <场景>` 找同场景内落在锚点run间的候选；确认后 → `WRONG` + correct_vid；不确定 → `SUSPECT`
   - `跨场景` issue：用 `vid <ID>` 查是否官方复用（EVO结构里被当前块内嵌引用）→ 是则 `OK`
   - `文本低相似`：用 `vid` 拿 msg 原文比对；确属不同台词 → `WRONG`/`SUSPECT`
3. **未匹配行**（无 vid）：用 `findmany` 整批检索（元素 `[文本, "evo_char"]`，每批 8-12 个；单条 `find` 仅零星补查；过滤无全等时读结果里的 `unfiltered_fallback`，不手动重查）：
   - 唯一命中 sim=100 → 用 `rowhint` 验证其落在锚点区间内 → `FOUND` + correct_vid
   - 多命中 → `CANDIDATES`（列出全部；若其中恰有一支落在 `rowhint` 区间内且全等，可据此裁定并在 evidence 注明位置证据）
   - 查证到 EVO 结构有对应行但未配音（无 voice_id）→ `NO_VOICE`（evidence 给行位置）
   - 无命中（hits 与 unfiltered_fallback 均空）→ `UNRESOLVED`
4. 全部行裁定后：批量 OK/UNRESOLVED 用 `submitmap <场景> <函数>`（只报 {行号: verdict} 映射，服务端回填 id，一次提交整块）；WRONG/SUSPECT/FOUND/CANDIDATES 及需自定义 reason 的行走 `submitmany`（每批 10-20 条）。

## submit 格式（单行JSON，注意引号转义）

```
uv run python rt.py submit '{"RemakeVoiceID":"102345","task":"A","verdict":"OK","reason":"runcheck通过,文本全等"}'
uv run python rt.py submit '{"RemakeVoiceID":"102346","task":"A","verdict":"WRONG","correct_vid":"0010470181","reason":"锚点run 0180-0182,现配0143在run外","evidence":{"find":"同场景sim=100两支,0181在run内"}}'
uv run python rt.py submit '{"RemakeVoiceID":"102350","task":"B","verdict":"CANDIDATES","candidates":[{"vid":"0030281695","evidence":"跨场景同文"}],"reason":"两支同文无法区分"}'
```

verdict 取值：`OK / WRONG(须给correct_vid) / SUSPECT / FOUND(须给correct_vid) / CANDIDATES / NO_VOICE / UNRESOLVED`

补充：`NO_VOICE` 仅在确证 EVO 结构存在对应行但未配音时使用（find 不索引无 voice_id 的行，需由 pack 邻域或 vid 查证发现）；查证不到就 `UNRESOLVED`，不硬判。

## 节奏

- 一次只做一个块，做完立刻逐行 submit，然后 `todo` 取下一块。
- 遇到 pack 里大量行都是同文本泛用句 → 快速 OK/UNRESOLVED，不恋战。
- 每块结束在 review_pack/progress_<代理ID>.log 追加（代理ID由主智能体任务提示给定，未给定时用 progress_main.log）：`<场景>/<函数> N行 (OK x, WRONG y, SUSPECT z, FOUND f, NO_VOICE v, CANDIDATES c, UNRESOLVED u)`。
