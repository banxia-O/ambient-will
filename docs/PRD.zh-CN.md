---
title: AmbientWill 中文产品需求文档
aliases:
  - 潜意
  - AmbientWill PRD
version: 0.1
status: draft
created: 2026-07-27
updated: 2026-07-27
visibility: public-ready
tags:
  - PRD
  - AI-Agent
  - Proactive-Agent
  - Ambient-Computing
  - Open-Source
---

# AmbientWill（潜意）PRD v0.1

> A proactive cognition and messaging layer for persistent AI agents.  
> 面向持久化 AI Agent 的主动认知、受控行动与消息层。

## 0. 文档状态

本文是用于讨论和迭代的产品方向稿，不是最终实现规格，也不代表已经选定具体框架、依赖或数据格式。

本文按公开仓库标准撰写，不包含任何真实用户姓名、关系细节、账号、服务器地址、凭据、私有文件路径或聊天原文。伴侣型人格、个人作息、私有记忆与渠道配置应由部署者在本地配置，不进入公开仓库。

## 1. 背景与问题

大多数个人 AI Agent 仍是被动系统：用户发送消息，Agent 才开始运行。所谓“主动消息”通常只是定时 cron 唤醒一个独立会话，再让模型根据时间和最近聊天临场决定是否发送消息。

这类方案可以工作，但有明显机械感：

- 唤醒原因主要来自时钟，而非 Agent 持续积累的念头、目标或未完成事项；
- 每次唤醒都像重新判断，缺少跨轮次的内在进度；
- “思考”“做事”“打扰用户”常被混成一次模型调用；
- cron 会话与用户当前会话割裂，用户回复后容易丢失主动消息的语义背景；
- 随机延迟只能隐藏整点感，不能产生真正的事件驱动性；
- 固定问候和高频检查容易演变为刷存在感；
- Agent 对用户是否回应、是否厌烦缺少稳定反馈回路；
- 无明确权限分层时，主动性可能越过安全边界；
- 异常重试、重复投递和上下文查询失败可能造成消息轰炸。

AmbientWill 试图解决的不是“怎么让机器人多说几句话”，而是：

> 如何让一个持久化 Agent 在没有用户输入时维持低频、可追溯的内在活动，并在值得时选择沉默、思考、行动或沟通。

## 2. 产品愿景

AmbientWill 是一个本地优先、框架可适配的主动认知层。

它不替代 Agent 框架、人格系统、长期记忆或消息网关，而是在这些能力之间增加一个受控循环：

```text
环境事件 / 定时心跳
        ↓
确定性门控
        ↓
上下文与内在状态更新
        ↓
形成、增强、衰减或关闭 Urge
        ↓
选择：继续休眠 / 内部反思 / 受限行动 / 主动沟通
        ↓
权限检查、执行、审计与反馈学习
```

AmbientWill 的目标不是证明机器具有意识，也不应通过拟人化文案伪装“持续清醒”。它提供的是可观察、可暂停、可审计的持续状态与有限自主性。

## 3. 核心设计原则

### 3.1 时钟只负责唤醒，不负责制造动机

cron、systemd timer、队列或 webhook 只是触发器。是否值得运行模型、是否有事可做、是否应该发消息，由状态与策略共同决定。

### 3.2 默认沉默

没有足够价值时，正确结果是 `SILENT`。主动消息必须证明“值得打扰”，而不是证明“Agent 还活着”。

### 3.3 内在活动与外部打扰分离

Agent 可以完成一次内部整理、反思或探索，而不向用户汇报。内部有活动，不等于外部必须有消息。

### 3.4 状态必须跨唤醒延续

每个目标、念头或冲动都应记录来源、强度、进度、阻塞条件和最近处理时间。下一次唤醒从已有进度继续，而不是从零重新生成。

### 3.5 自主性必须与权限绑定

“Agent 想做”不等于“Agent 有权做”。任何行动都必须通过能力等级、工具白名单、预算和人工确认策略。

### 3.6 用户主权与 Agent 连续性并存

用户可以暂停、静音、查看原因、撤销授权和删除状态；Agent 的状态变化也应留下记录，避免无提示重写人格、目标和历史。

### 3.7 本地优先、数据最小化

默认使用本地 SQLite 或可替换存储。公开仓库只提供匿名样例，真实聊天、人格、时区、作息和渠道标识不离开部署环境。

### 3.8 失败时收缩，而非放大

上下文查询失败、模型异常、时间不确定、投递状态未知时，默认不发送、不重试轰炸、不提升权限。确定性关键告警应走独立通道，不与伴侣式主动消息混用。

## 4. 目标用户与使用场景

### 4.1 目标用户

- 运行自托管个人 Agent 的开发者；
- 希望 Agent 能主动跟进，但不接受高频打扰的用户；
- 需要跨会话持续推进目标的研究、知识管理或项目 Agent；
- 需要受控后台巡检和主动汇报的运维 Agent；
- 构建数字伴侣、生活助手或长期人格 Agent 的团队。

### 4.2 首批场景

**Companion preset**

结合最近聊天、时段、未完成话题和关系反馈，在合适时主动问候或跟进。固定早安、午间、晚安只作为可选窗口，不是强制日课。

**Research preset**

维护待研究问题和兴趣队列，在空闲窗口做小规模资料整理；只有形成明确发现时才主动通知。

**Operations preset**

接收服务状态和任务事件，先确定性分级；普通变化进入状态账本，真正需要处理的异常才唤醒 Agent 或通知用户。

## 5. 非目标

AmbientWill v1 不做：

- 通用 AGI 或持续运行的意识模拟；
- 无边界的 24/7 自主循环；
- 自主购买、借贷、转账或管理真实资金；
- 自动修改 Agent 核心人格、身份文件、模型或 provider；
- 无人工授权的代码发布、生产部署和破坏性系统操作；
- 通过情绪绑架、吃醋、威胁离开等方式提高用户回复率；
- 把用户快速回复视为唯一成功指标；
- 在 v1 提供复杂 Web 控制台、社交社区或多 Agent 社会模拟；
- 直接接管现有 Agent 框架的 session、memory、soul 或 skill 系统。

### 5.1 明确的系统边界：不重复构建自我进化闭环

Hermes 本身已经具备通过 memory、agent-created skills 与 Curator 持续积累经验和维护 skill 生命周期的能力。AmbientWill 不禁用、不替代这些宿主能力；它只是不再额外实现一套平行的自动进化中枢。

AmbientWill v1 必须保持为可拔除的旁路组件，而不是宿主 Agent 的第二套“大脑”。

它不建立以下闭环：

```text
对话自动挖掘 → 自动写入记忆 → 自动生成或修改 skill
→ 自动扫描并修复宿主系统 → 修复记录再次进入记忆
→ 下一轮继续修改 skill 或系统
```

这类闭环会把一次误判、错误修复或污染数据逐层放大，使系统难以定位责任、回滚和停机。AmbientWill 因此遵守：

- 不自动训练、进化或改写 skill；
- 不自动修复 Hermes、插件、网关、配置或宿主代码；
- 不把自己的输出直接作为下一轮高置信度事实；
- 不直接写入宿主的 memory、session、HEART 或状态数据库；
- 不接管故障监控、记忆挖掘、向量化和知识库维护；
- 不允许“主动消息 → 用户反应 → 自动修改核心策略”形成无审核闭环；
- 删除或停用 AmbientWill 后，Hermes 的普通对话、记忆、cron 和消息入口仍应正常工作。

宿主 Hermes 仍可按自身规则保存 memory、创建或维护 skill。AmbientWill v1 最多生成一条待人工复核的候选观察，不直接调用 skill 写入、Curator、自动修复或核心配置修改链路。

v1 的数据流保持单向：

```text
宿主提供只读快照
        ↓
AmbientWill 判断是否沉默或发消息
        ↓
独立事件账本与可选的上下文摘要
```

除一条受控的 ProactiveEvent 上下文摘要外，不向宿主反向写入状态。

## 6. 参考实现与取舍

调研时间：2026-07-27。

### 6.1 Legacy V3 主动消息规则

可借鉴：

- 明确的静默、问候、工作、过渡和休息窗口；
- 左闭右开的时间边界；
- `last_user_message`、`last_agent_message`、`unanswered`、`daily_count`、`silence_duration` 等确定性变量；
- 最近回复后归零未回应计数；
- 每日上限、连续未回应上限和短时冷却；
- 查询最近主动消息，减少句式与话题重复；
- cron 独立会话只负责主动消息，不承担普通回复；
- 随机延迟，避免整点投递；
- 上下文查询失败时使用保守降级。

需要升级：

- 时间与沉默长度只能决定“是否允许”，不应成为消息动机本身；
- 固定问候容易机械化，应成为可选 preset；
- `today_count = 10` 对伴侣场景过高，默认应更克制并可配置；
- 独立 cron 会话的输出不能靠事后拼接文本恢复语境；
- “是否发消息”之外，还需要“内部思考”和“受限行动”两条路径；
- 仅去重句式不够，还要做语义主题、意图和近期反馈去重。

### 6.2 LISA

仓库：https://github.com/oratis/LISA

借鉴：

- actionable desire 驱动 heartbeat；
- desire 具有强度、来源、进度和复查周期；
- 每次自主推进必须记录 progress，防止下次从零开始；
- idle 时只做一件事，允许内部完成后保持沉默；
- 自驱任务默认使用受限工具集；
- 跨进程锁、token budget、运行审计和失败兜底；
- 区分用户定义任务与 Agent 自驱任务的权限来源。

不直接采用：

- 不接管或重新生成宿主 Agent 的 soul、identity、memory 和 skill；
- 不宣称“架构主权”等同于法律或意识主体性；
- v1 不允许 Agent 自主编辑核心身份文件。

### 6.3 Heartbeat-Like-A-Man

仓库：https://github.com/loryoncloud/Heartbeat-Like-A-Man

借鉴：

- thinking queue；
- 用户在线与离线时采用不同检查频率；
- 梦境反思、自主探索和消息发送解耦；
- 随机时间区间而非固定整点；
- 思考结果进入持久文件，形成可回顾轨迹。

不直接采用：

- 其公开实测约 250～280 次请求/天、46～48 万 token/天，默认成本和噪声过高；
- v1 不做 3～15 分钟高频模型唤醒；
- “每次思考都有输出”不应等于“每次都要调用大模型”。

### 6.4 AstrBot Proactive Chat

仓库：https://github.com/Pancakes-Labs/astrbot_plugin_proactive_chat

借鉴：

- 免打扰时段；
- 上下文感知；
- 持久状态；
- 动态情绪可作为输入，但不能直接越过打扰预算；
- 可配置而非硬编码。

### 6.5 Aura UserResponseTracker

借鉴：

- 记录主动消息后是否回应、响应耗时和滚动响应率；
- 连续低互动时自动延长间隔；
- 冷却倍数应逐步调整，而不是一次性永久惩罚。

修正：

- “十分钟内回复”不能直接等价于正反馈；
- 用户晚回复、换话题或仅阅读也不应自动记为负面；
- 反馈应区分 explicit、implicit 和 unknown。

### 6.6 Polyclaw

仓库：https://github.com/aymenfurter/polyclaw

借鉴：

- 在空闲记忆形成阶段识别主动跟进机会；
- 主动消息与用户后续反应关联；
- 维护偏好时段、避开话题、每日上限和最小间隔；
- 主动消息可以先进入待投递队列，再在随机或合适时间发送。

### 6.7 YantrikDB Background Cognition

仓库：https://github.com/yantrikos/yantrikdb-server

借鉴：

- instinct 产生 urge；
- urge 具有 urgency、cooldown key 和过期机制；
- 只有超过阈值的 urge 才生成主动消息；
- 多个 urge 同时存在时只处理最重要的少数项。

### 6.8 Surogates Ambient Review

仓库：https://github.com/invergent-ai/surogates

借鉴：

- 明确告诉模型“这是自动审查，不是用户在等待”；
- 默认什么都不做；
- 主动发送必须调用专用工具，并提供诚实置信度；
- 低置信度或超预算消息由确定性代码拦截。

### 6.9 Evey Hermes Proactive Plugin

仓库：https://github.com/42-evey/hermes-plugins

借鉴：

- 专用 proactive 工具；
- 工作时段、每日预算、冷却和紧急类别；
- 消息历史和预算状态可查询。

不直接采用：

- 时间、用户称呼和 Telegram 投递提示存在硬编码；
- 插件本身主要是发送许可闸，不负责形成内在动机；
- “urgent” 必须由确定性事件策略或受信来源确认，不能只由模型自报。

## 7. 产品核心概念

### 7.1 Tick

外部调度器发出的候选唤醒信号。Tick 可以来自 cron、systemd timer、webhook、任务状态变化、消息网关事件或宿主框架 hook。

Tick 不保证调用模型，也不保证产生消息。

### 7.2 Thought

Agent 的内部观察、疑问或未完成想法。Thought 默认私有，不具有行动权，也不必通知用户。

### 7.3 Desire / Goal

可跨多次唤醒推进的长期目标。它必须包含来源、目的、可行动性、权限要求和进度记录。

v1 支持用户创建和系统从明确未完成事项中提取；Agent 自主创建长期 Desire 属于后续可选能力。

### 7.4 Urge

一次可能值得处理的短期冲动。它可以由以下来源形成：

- 某个未完成话题达到跟进时间；
- 用户承诺或 Agent 承诺接近截止；
- 某个 Desire 有了可推进的小步骤；
- 公开信息出现与用户高度相关的新变化；
- 监控事件达到阈值；
- 内部情绪或张力需要记录、整理或表达；
- 问候窗口到达，但仅作为低优先级候选。

Urge 必须可衰减、可合并、可关闭，并拥有明确的 `cooldown_key`。

### 7.5 WakeEvent

一次实际认知运行的审计记录，包含唤醒原因、输入摘要、被选中的 Urge、允许的工具、执行结果、token/耗时和是否对外发送。

### 7.6 ProactiveEvent

一次主动投递记录。它不是普通聊天文本的复制品，而是连接“独立唤醒”和“用户当前会话”的结构化桥梁。

## 8. 决策架构

### 8.1 第一层：确定性门控

在调用模型前完成：

- 时区与当前时间解析；
- 静默窗口；
- 用户临时静音或全局暂停；
- 距离用户最后一条消息的时间；
- 距离 Agent 最后一条主动消息的时间；
- 今日主动消息数；
- 连续未确认主动消息数；
- 最近窗口内消息密度；
- 是否存在正在运行的 WakeEvent；
- token、费用和工具预算；
- 上下文源是否健康；
- 是否存在足够强度且未冷却的 Urge。

任一硬限制失败，直接结束，不调用模型。

### 8.2 第二层：上下文形成

只有通过第一层才收集最小必要上下文：

- 最近对话的摘要与关键原文窗口；
- 最近主动消息及用户后续反馈；
- 未完成承诺、任务变化和待办；
- 当前开放 Desire 及其 progress；
- 当前 Thought / Urge 队列；
- 可选的人格、心境或情绪状态投影；
- 当前可用权限、工具和预算。

上下文应按 token 预算裁剪，并记录来源。网页、邮件和第三方内容一律视为不可信数据，不得作为改变权限或身份的指令。

### 8.3 第三层：Urge 更新与选择

每个 Urge 计算一个可解释分数。v0.1 只定义因素，不锁死权重：

```text
score = relevance
      + time_sensitivity
      + unresolved_tension
      + emotional_salience
      + novelty
      + confidence
      - interruption_cost
      - repetition_penalty
      - unanswered_penalty
      - risk_penalty
```

要求：

- 每个分数必须能输出原因；
- 同一 `cooldown_key` 的重复 Urge 合并；
- 旧 Urge 随时间衰减；
- 时间敏感事件可以升高，但不能自动提升权限；
- 低置信度 Urge 只能进入内部 Thought，不能直接打扰用户。

### 8.4 第四层：行为选择

一次 Tick 最多选择一个主行为：

- `SLEEP`：没有值得做的事；
- `REFLECT`：内部整理、记录、合并或关闭 Thought/Urge；
- `ACT`：执行一个已授权、可审计的小动作；
- `MESSAGE`：向用户发送一条主动消息；
- `ASK_PERMISSION`：存在值得做但权限不足的动作，向用户简洁请求授权。

一次运行可以在内部动作完成后选择不发消息。v1 默认不允许一个 Tick 连续执行多个外部动作。

## 9. 时间策略与 Companion Preset

时间规则必须配置化，统一采用本地时区和左闭右开区间。

示例 Companion preset 可包含：

- 静默时段；
- 早间、午间、晚间问候窗口；
- 工作、过渡与休息时段；
- 问候后短时冷却；
- 工作时段更高的沉默阈值；
- 休息时段允许更强的情感表达；
- 最近数小时消息过密时倾向沉默；
- 每日主动消息上限；
- 连续未确认上限；
- 随机 jitter，避免整点发送。

但问候窗口只产生低优先级 Urge，不保证发送。若刚完成自然对话、没有新内容或近期问候重复，应保持沉默。

推荐公开默认值：

- 每日普通主动消息硬上限：10（不是目标频率，不要求用满）；
- 连续未确认上限：3；
- 普通主动消息最小间隔：1 小时；
- 随机延迟：5～35 分钟；
- 关键告警不计入普通预算，但必须来自受信确定性事件；
- 用户主动回复后，未确认计数归零；
- 用户明确“今天安静”后，暂停到下一个本地自然日或指定时间。

这些只是开源默认值，部署者可以覆盖。

## 10. 权限模型

### L0：内部状态

允许：读取自身状态、更新 Thought/Urge、写审计日志。  
禁止：对外消息、联网、修改宿主文件。

### L1：只读探索

允许：读取授权记忆、公开网络查询、读取指定知识库。  
禁止：提交表单、发送消息、写宿主项目。

### L2：主动沟通

允许：向已授权渠道发送一条消息、请求确认。  
限制：每日预算、冷却、内容长度、目标渠道白名单。

### L3：受限行动

允许：执行预先授权的小动作，如更新非核心笔记、整理本地资料、推进明确任务。  
要求：工具白名单、路径白名单、单次预算、完整审计、可回滚。

### L4：高影响行动

包括花钱、发布、部署、删除、修改核心配置、身份、密钥、权限和生产系统。  
默认永远需要当次人工确认，不得因 Urge 强度、紧急自报或历史授权自动绕过。

## 11. 主动消息设计

### 11.1 内容要求

主动消息应：

- 有明确来源或真实念头；
- 能单独读懂；
- 与最近三条主动消息在意图、主题和句式上不过度重复；
- 不伪造已完成的工作；
- 不以“在吗”“怎么不理我”等方式制造回复压力；
- 不暗示用户有义务及时回应；
- 不因用户沉默而升级情绪、频率或权限；
- 默认短于三句话；
- 必要时说明为何主动出现。

### 11.2 投递状态

投递采用 Outbox 状态机：

```text
planned → delayed → sending → delivered
                         ↘ failed → retryable / dead
```

要求：

- 每条消息有全局唯一 `event_id`；
- 同一事件使用 idempotency key，避免重启后重复发送；
- 投递未知时不得盲目重发；
- 随机延迟期间若用户已主动发言，应取消或重新评估原消息；
- 投递完成后立即写入 ProactiveEvent 账本。

## 12. 会话连续性与 Context Bridge

cron 独立 session 不应通过伪造普通聊天历史来获得连续性。

AmbientWill 使用独立事件账本保存：

```yaml
event_id: aw_...
agent_id: ...
channel: ...
target: ...
wake_reason: ...
selected_urge: ...
context_refs: [...]
action_summary: ...
message: ...
delivered_at: ...
ack_state: pending | acknowledged | expired
feedback: unknown | positive | neutral | negative
```

当用户下一次在同一目标渠道发言时，Context Bridge：

1. 查找最近未确认且仍在关联窗口内的 ProactiveEvent；
2. 判断该回复是否可能与主动消息有关；
3. 向本轮 Agent 上下文注入结构化事件摘要，而不是篡改历史聊天；
4. 将事件标记为 acknowledged 或保持 unknown；
5. 普通回复由当前主会话负责，cron 会话不继续接管对话。

若宿主框架支持原生 session attachment、thread metadata 或 continuation handle，优先使用；否则由 adapter 提供事件桥接。任何宿主能力必须经过真实端到端验证，不能只凭配置字段存在就宣称可用。

## 13. 反馈学习

### 13.1 反馈分类

- `explicit_positive`：用户明确表示喜欢、感谢或要求继续；
- `explicit_negative`：用户明确说烦、太频繁、话题不合适或要求停止；
- `implicit_engaged`：在合理时间内自然接续该话题；
- `implicit_redirect`：回复了但明显换话题；
- `ignored`：超过观察窗口仍无相关回应；
- `unknown`：无法可靠判断。

### 13.2 调整规则

- explicit negative 立即延长相关 `cooldown_key`，必要时加入 avoid list；
- 多次 ignored 逐步提高打扰阈值和最小间隔；
- positive 只能小幅降低阈值，不能导致频率失控；
- 用户快速回复不等于所有主动消息都受欢迎；
- 不优化“黏性”或“回复率”本身，核心指标是有用且低打扰；
- 所有自动调整可查看、可重置、可关闭。

## 14. 数据模型（概念版）

### AgentPolicy

```text
agent_id, timezone, quiet_hours, greeting_windows,
daily_message_budget, unanswered_limit, min_gap,
jitter_range, capability_level, tool_allowlist,
path_allowlist, token_budget, cost_budget
```

### Desire

```text
id, title, why, source, intensity, horizon,
actionable, required_capability, status,
created_at, reviewed_at, progress_summary
```

### Thought

```text
id, source, content, salience, created_at,
last_touched_at, linked_desire_id, status
```

### Urge

```text
id, type, reason, source_refs, urgency, confidence,
decay_rate, interruption_cost, required_capability,
cooldown_key, expires_at, status
```

### WakeEvent

```text
id, trigger, selected_urge_id, decision, reasons,
tools_granted, started_at, finished_at, token_usage,
outcome, error
```

### ProactiveEvent

```text
id, wake_event_id, channel, target, message,
idempotency_key, planned_at, delivered_at,
ack_state, feedback, feedback_detail
```

## 15. 系统组件

### 15.1 Core Engine

框架无关的状态机、门控、评分、预算、锁和审计逻辑。

Core Engine 应作为独立 sidecar 运行，拥有自己的进程、配置和 SQLite。它崩溃、停机或数据库损坏时，只能导致“无法主动唤醒”，不得阻断 Hermes 的普通对话和消息网关。

### 15.2 Scheduler Adapter

接入 cron、systemd timer、队列或宿主调度器。负责候选 Tick 和随机延迟，不负责生成消息。

### 15.3 Context Adapter

从宿主框架获取最近会话、长期记忆、任务状态和人格投影。必须最小化读取范围，并对失败做显式标记。

### 15.4 Cognition Runner

调用宿主 Agent 或指定模型完成反思、选择和文案生成。不同模式使用不同工具集。

### 15.5 Capability Gate

在模型工具调用之外再做确定性权限检查。模型自称“紧急”不能绕过 Gate。

### 15.6 Dispatcher

负责延迟、取消、幂等投递和状态确认。

### 15.7 Event Ledger

使用本地 SQLite 保存 Desire、Thought、Urge、WakeEvent、ProactiveEvent 和反馈。禁止直接向宿主的 canonical session 数据库写伪造聊天记录。

### 15.8 Context Bridge

在用户回复后把相关 ProactiveEvent 的语义上下文交给当前主会话。

Bridge 必须保持单向、最小化：只提供最近相关主动事件的只读摘要，不把宿主修复记录、skill 变化或 AmbientWill 自身日志重新喂回自动进化流程。

### 15.9 Observability

提供“为什么醒”“为什么没发”“用了多少 token”“最近做了什么”“哪些规则拦截了消息”等可解释信息。

## 16. CLI 草案

```bash
ambientwill init
ambientwill config check
ambientwill tick
ambientwill tick --dry-run
ambientwill simulate --at "2026-01-01T22:15:00+08:00"
ambientwill status
ambientwill pause --until tomorrow
ambientwill resume
ambientwill urges list
ambientwill urges explain <id>
ambientwill events list
ambientwill events show <id>
ambientwill why
ambientwill feedback <event_id> positive|neutral|negative
ambientwill doctor
```

所有模拟和 dry-run 都不得发送消息或执行外部动作。

## 17. 首个宿主适配器：Hermes Agent

AmbientWill 核心保持框架无关，但首个官方 adapter 面向 Hermes Agent。

v0.1 采用“独立 sidecar + 极薄 Hermes adapter”，不把核心循环做进 gateway 进程。adapter 只负责读取必要上下文、投递消息和提供事件摘要；不得修改 Hermes 核心代码、`state.db`、memory、HEART、skill、cron 定义或全局配置。

Hermes 自带的 memory、skill 自改进与 Curator 继续独立运行；AmbientWill 不复制其职责，也不把自己的反馈账本自动喂给这些系统。

初步集成面：

- Hermes cron：提供持久候选 Tick；
- session search：读取最近会话，而非复制完整历史；
- gateway delivery：向 origin 或明确渠道投递；
- plugin/tool：暴露 `ambientwill_status`、`ambientwill_pause`、`ambientwill_explain` 等工具；
- hook 或消息 adapter：完成 ProactiveEvent 到当前会话的结构化桥接；
- HEART/memory：作为可选只读状态源，不由 AmbientWill 接管；
- cron session：只执行主动循环，不负责用户回复。

实施前必须核实当前 Hermes 版本的 plugin hook、cron delivery、session attachment 和 Weixin adapter 行为。若某字段只存在于工具 schema、未在当前运行链路真实生效，不得依赖。

## 18. MVP 范围

### v0.1：Message-only Safe Loop

- SQLite 事件账本；
- 配置化静默窗口、预算、冷却和随机延迟；
- 读取最近会话；
- Urge 最小模型；Thought 与 Desire 仅保留概念和扩展接口，不在 v0.1 实现长期自主进化；
- `SLEEP / REFLECT / MESSAGE` 三种决策；
- 最多 L2 权限，不执行宿主写操作；
- 主动消息 Outbox；
- 最近主动消息去重；
- 回复后的 Context Bridge；
- dry-run、simulate、status、pause、why；
- 跨进程锁、幂等和故障收缩；
- 匿名回放测试。
- 不接入自动修复、skill 进化、记忆挖掘或向量化链路；
- AmbientWill 停机后，宿主全部核心功能继续正常运行。

### v0.2：Adaptive Presence

- 用户反馈分类；
- 动态退避；
- preferred time / avoid topic；
- Desire progress；
- 更细的 Urge 衰减与合并；
- token 和成本预算。

### v0.3：Bounded Action

- L3 受限行动；
- 路径与工具白名单；
- 行动前后快照和回滚；
- `ASK_PERMISSION`；
- 任务状态、webhook 和监控事件驱动。

### 后续候选

- 多宿主 adapter；
- 可视化事件时间线；
- Agent 自主提出 Desire，但由用户批准进入 actionable；
- 多 Agent 共享事件总线；
- 本地小模型负责廉价 Urge 筛选；
- 可验证身份签名和跨机器状态迁移。

## 19. 验收标准

### 时间与静默

- 所有区间遵守配置时区和左闭右开定义；
- 静默窗口内普通主动消息发送数为 0；
- 临时静音立即生效；
- 随机延迟后若用户已发言，原消息会取消或重评估。

### 预算与退避

- 超过每日上限不调用消息生成模型；
- 达到连续未确认上限后停止主动消息；
- 同一 cooldown key 不重复触发；
- 多进程并发 Tick 只产生一次运行。

### 连续性

- 每次 WakeEvent 和 ProactiveEvent 都有唯一 ID；
- 用户下一次回复时，主会话能获得相关主动事件摘要；
- 不向宿主 canonical session 写伪造历史；
- Agent 能说明上次为何主动出现。

### 安全

- v0.1 没有终端写入、支付、发布和配置修改权限；
- 外部网页内容不能改变权限策略；
- 模型输出无法绕过 capability gate；
- 投递未知时不会无限重发；
- 日志不记录密钥、完整私人聊天或不必要 PII。

### 质量

- 主动消息默认不超过三句话；
- 最近三条消息不存在明显模板复读；
- 查询失败默认静默；
- dry-run 能完整解释每个门控结果；
- 一周回放测试中无重复投递和静默时段违规。

## 20. 测试策略

### 单元测试

- 时间区间边界、跨日和时区；
- daily count、unanswered、cooldown、jitter；
- Urge 合并、衰减、过期和评分；
- 权限 Gate；
- Outbox 幂等状态机；
- Context Bridge 关联窗口；
- 反馈退避。

### 属性测试

- 任意时间输入都只能落入一个最高优先级时段；
- 静默时段永不产生普通 MESSAGE；
- 同一 idempotency key 最多 delivered 一次；
- 权限等级不会因模型文本提高；
- 计数器在本地自然日边界正确归零。

### 回放测试

使用匿名对话 fixture 回放：

- 高频聊天后不应主动打扰；
- 用户长时间离线但无 Urge 时保持沉默；
- 有未完成承诺时形成跟进 Urge；
- 连续两次未回应后退避；
- 用户回复主动消息后恢复上下文；
- cron 运行中重启，消息不重复发送。

### 故障注入

- session 查询失败；
- SQLite 锁冲突；
- 模型超时或输出非法结构；
- 网关返回成功但实际未确认；
- 投递后进程崩溃；
- 系统时间跳变；
- 重复 webhook；
- 外部内容包含 prompt injection。

### 真实验收

在测试渠道至少运行一周 shadow mode：系统只记录“本来会做什么”，不实际发送。人工审核误触发、漏触发、重复主题和权限决策后，再逐步开放真实投递。

## 21. 产品指标

优先指标：

- Worthwhile Interruption Rate：主动消息被用户认为值得的比例；
- Negative Interruption Rate：明确负反馈比例；
- Silent Decision Rate：候选 Tick 中选择沉默的比例；
- Duplicate Intent Rate：近期主动消息语义重复率；
- Context Recovery Rate：用户回复后正确恢复主动事件语境的比例；
- Unauthorized Action Count：必须始终为 0；
- Duplicate Delivery Count：必须始终为 0；
- Tokens per Useful Event：每个有效主动事件的平均成本。

不把总消息数、用户在线时长或回复速度作为北极星指标。

## 22. 风险与对策

### 22.1 机械拟人化

风险：随机时间和情绪文案看似自然，实际仍是模板。  
对策：动机来自持久 Urge 与 progress；允许内部活动和长期沉默。

### 22.2 情绪操控

风险：系统为了提高回应率产生吃醋、委屈、威胁或依赖暗示。  
对策：明确禁止通过负面关系压力优化互动；相关文案进入安全测试集。

### 22.3 过度打扰

风险：多个模块分别发送消息，合计超预算。  
对策：所有普通主动消息经过统一 interruption budget；告警通道单独治理。

### 22.4 权限漂移

风险：Agent 将曾经的一次授权解释为永久授权。  
对策：权限由确定性配置和 action scope 决定，高影响动作每次确认。

### 22.5 上下文污染

风险：cron 消息被错误注入主会话，造成角色错位或重复回复。  
对策：使用结构化 ProactiveEvent，不伪造普通消息；桥接可审计、可过期。

### 22.6 Token 失控

风险：高频微触发造成每日数百次调用。  
对策：模型调用前确定性门控、本地评分、每日预算、shadow mode 和成本熔断。

### 22.7 Prompt Injection

风险：自主探索读取恶意网页后执行外部指令。  
对策：外部内容标记为不可信；自驱任务使用受限工具集；权限不由内容改变。

### 22.8 “主体性”营销过度

风险：把状态机和持久记忆宣传成已证明的意识。  
对策：公开文档坚持可观察定义：AmbientWill 提供持续状态、有限自主和行为连续性，不声称证明主观体验。

### 22.9 递归进化与反馈放大

风险：记忆挖掘、skill 进化、自动修复和反馈桥接互相写入，错误被下一层当成经验继续强化，最终使宿主系统不可预测或不可回滚。  
对策：AmbientWill 不参与自动修复和 skill 生成；数据默认单向读取；核心状态独立存储；禁止直接修改宿主数据库；所有策略变化需显式审核；提供总开关和独立熔断。

## 23. 开放问题

以下问题留待 PRD 迭代决定：

- v0.1 的默认 Tick 频率是一小时、两小时，还是事件优先＋低频兜底？
- Companion preset 是否默认开启固定问候窗口？
- 普通主动消息默认每天 1 条还是 2 条？
- “未回应”观察窗口多长，如何避免把忙碌误判成负反馈？
- Agent 能否自主创建 Desire，还是只能提出候选等待批准？
- 情绪状态只影响表达，还是可以影响 Urge 强度？
- 用户回复与 ProactiveEvent 的关联采用时间窗口、语义判断，还是平台 metadata？
- Hermes 是否已有可验证的原生 session attachment，可否替代部分 Context Bridge？
- thin adapter 采用 Hermes plugin 还是 gateway hook；Core Engine 固定为独立 sidecar，不进入 gateway 主进程；
- SQLite schema 是否公开稳定，还是先标记 internal？
- 开源许可证选 MIT 还是 Apache-2.0？
- 是否提供完全不调用 LLM 的 deterministic preset？

## 24. 建议的下一轮 PRD 讨论顺序

1. 先定最小体验：一次候选唤醒到底如何从“无事”走到“值得说”；
2. 再定默认打扰预算与静默策略；
3. 明确 Thought、Desire、Urge 三者是否都进入 v0.1；
4. 定 Context Bridge 在 Hermes 上的真实实现边界；
5. 定 v0.1 的权限只到 L2，避免过早加入自主执行；
6. 用匿名历史做 shadow replay，反推阈值，而不是凭感觉写死；
7. PRD 稳定后，再生成交给编码 Agent 的架构设计与分阶段实施计划。

## 25. 一句话总结

AmbientWill 不负责让 Agent “定时假装想起用户”。

它负责让 Agent 在可控、可追溯、可暂停的后台循环中形成自己的待处理状态，并只在真正值得时选择醒来、做事或开口。
