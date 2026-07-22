# BuyerReach 查询切片：跨平台统一执行 Prompt

用途：将本文“统一执行 Prompt”原样交给 Codex、Claude Code、Cursor Agent、GitHub Copilot Agent 或其他具备仓库读写和终端能力的编码 Agent。  
执行目录：`D:\buyer reach`  
唯一设计来源：`docs/query-slicing-production-implementation-plan.md`  

## 跨平台一致性规则

1. 所有平台使用下方同一 Prompt 正文，不复制并维护平台专属设计版本。
2. 平台专属文件只能引用本 Prompt、`AGENTS.md` 和 `docs/DEVELOPMENT_RULES.md`，不得重述或修改架构语义。
3. 若 Agent 上下文有限，应按 Phase A-E 分次继续，但每次必须重新读取本 Prompt、唯一设计方案和进度文件。
4. 实现进度以仓库内 `docs/query-slicing-implementation-status.md` 为准，不以聊天记录为准。
5. 任何偏离唯一设计方案的决定必须写入 `docs/query-slicing-implementation-decisions.md`，包含原因、兼容影响、替代方案和验证；不得静默偏离。
6. 一致性由相同源码、迁移、测试、验收命令和交付证据保证，不能以模型口头声明代替。

---

## 统一执行 Prompt

你正在 BuyerReach 仓库 `D:\buyer reach` 中工作。请按照 `docs/query-slicing-production-implementation-plan.md` 完整实现生产级查询切片功能。必须形成真实可运行的纵向闭环，不得只新增未接入执行路径的表、Schema、接口或 UI。

### 一、最终用户结果

完成后，普通用户应当能够：

1. 用一句话描述目标商家；
2. 选择目标合格商家数量；
3. 查看系统理解并直接开始搜索；
4. 可选地展开、编辑、新增、复制、禁用或删除查询切片；
5. 锁定后的切片真实驱动现有 company-search Provider 执行；
6. 在多任务同时运行时看到排队原因、当前阶段、部分结果和真实计数；
7. 暂停、恢复、取消或继续获取新增数据，不丢失已完成结果；
8. 来源耗尽、预算用尽或部分失败时看到真实停止原因，系统不得为了凑数降低筛选条件。

本次继续使用现有 Hunter、Apollo、Prospeo 和 Provider Waterfall，不接入 Wikidata、政府注册库、展会目录、协会目录、OSM、Common Crawl、搜索引擎或其他新来源。必须预留 Vendor 中立 Adapter 契约，但禁止实现没有接入真实路径的投机性框架。

### 二、开始前必须执行

1. 完整阅读：
   - 根目录 `AGENTS.md`；
   - `docs/DEVELOPMENT_RULES.md`；
   - `docs/pipeline-production-architecture-v1.md`；
   - `docs/production-integrity-audit-2026-07-17.md`；
   - `docs/query-slicing-production-implementation-plan.md`；
   - 本 Prompt。
2. 从当前目录向上检查实际 Git 根目录；若工作区没有 Git 元数据，记录事实，但继续使用文件级差异检查，不得因此覆盖用户文件。
3. 检查工作树和现有改动，保留所有无关用户修改。
4. 追踪并记录当前完整调用链：
   - AI/local intent 解析；
   - `plan_provider_queries`；
   - `provider_query_planning` Stage；
   - `execute_search_task`；
   - `execute_provider_waterfall`；
   - `TaskVendorPlan`、`TaskStageCheckpoint` 和 `PipelineStageRun`；
   - Candidate 去重、来源命中、评分、Outbox、SSE；
   - Celery Worker、Beat 和恢复扫描；
   - `TasksView.vue` 创建、轮询、暂停、恢复和取消体验。
5. 执行并记录基线：

```text
cd backend
python -m pytest -q
python -m ruff check app tests migrations
python -m compileall -q app migrations

cd ../frontend
pnpm exec vue-tsc --noEmit
pnpm run build
```

6. 如果基线已有失败，不要归咎于本次改动；记录可复现证据，继续完成不受影响的工作，并确保没有扩大失败范围。
7. 创建或更新 `docs/query-slicing-implementation-status.md`，采用本文规定的统一格式。在修改代码前写明基线、当前 Phase 和验收清单。
8. 制定执行计划，严格按 Phase A-E 推进。每个 Phase 完成后先验证并更新状态文件，再进入下一 Phase。

### 三、权威与冲突处理

优先级：

1. 法律、安全和用户明确指令；
2. `docs/DEVELOPMENT_RULES.md`；
3. `AGENTS.md` 及更具体目录规则；
4. `docs/query-slicing-production-implementation-plan.md`；
5. 本 Prompt；
6. 现有代码约定；
7. 工具或平台默认行为。

若计划与当前实现存在无法兼容的冲突：

- 先验证冲突确实存在；
- 选择最小、可回滚、向后兼容的实现；
- 写入 `docs/query-slicing-implementation-decisions.md`；
- 不得通过删除历史字段、绕过状态机、降低测试断言或改写权威开发规则解决冲突。

### 四、强制架构约束

- PostgreSQL 是 Query Plan、Slice、SliceRun、任务、游标、候选、事件和成本的最终真相来源。
- Redis/Celery 只负责唤醒和短期协调；Redis 清空或 Worker 重启后必须可恢复。
- 查询切片必须 Vendor 中立，不能保存 Hunter/Apollo 专用请求结构。
- Vendor URL、认证、请求/响应映射、分页协议和错误映射只能位于版本化 Adapter。
- 任务开始时锁定 Query Plan、配置快照、版本、Vendor Plan、筛选、预算和 rollout 决策。
- 运行中系统配置变化不能改变已锁定任务的业务语义。
- 每个外部调用的最小执行单元是“一个切片 × 一个来源 × 一页”。
- 每个外部调用必须有持久 Run、唯一幂等键、取消检查、预算检查、额度检查、超时、重试上限和成本记录。
- Candidate 和 Task 状态变化必须使用现有统一状态机边界；新增 Plan/Slice/SliceRun 也必须使用统一 transition 函数。
- 数据变化和事件使用同一事务写入 Outbox。
- Provider 成功结果持久化后才可调度下游；completed 幂等键必须复用已存结果。
- 一个大任务不得一次性占满全部 Worker；按组织和任务公平轮转。
- 第一版同一任务同时最多执行一个发现切片页，除非压力测试证明更高并发安全并通过配置启用。
- 目标数量表示合格商家目标，不等于 Provider 原始返回数量。
- 不得为了达到目标数量自动降低最低相关性、国家、品类、官网或商家类型条件。
- 未验证候选显示为待验证/待审核，不得伪装为合格。
- 老任务没有 Query Plan 时继续使用 legacy `task.filters` 路径。
- 迁移必须 additive，当前版本不删除 `brand_limit`、历史评分或旧任务字段。
- Query Plan、Policy、Schema、Prompt、Pipeline 和 Adapter 语义变化必须分别版本化。
- 不在日志、快照、事件和审计中保存 API Key、Authorization、Token、密码或不必要的联系人 PII。

### 五、统一进度文件格式

`docs/query-slicing-implementation-status.md` 必须使用以下结构，并由任何平台继续维护：

```markdown
# Query Slicing Implementation Status

- plan_version: 1.0
- current_phase: A|B|C|D|E|complete
- overall_status: not_started|in_progress|blocked|complete
- last_updated_at: ISO-8601
- last_updated_by: <agent/platform>

## Baseline
- command: ...
  result: pass|fail|not_run
  evidence: ...

## Phase A
- status: ...
- completed:
- remaining:
- changed_files:
- verification:
- risks:

## Phase B
...

## Decisions
- See docs/query-slicing-implementation-decisions.md

## Next Exact Action
- 一条可以由下一个 Agent 直接执行的具体动作
```

状态文件只能记录事实，不能将未运行测试写成通过。若切换平台，下一个 Agent 必须先读取状态文件并核对文件/测试证据，再继续。

### 六、Phase A：领域、迁移和 API

实现 Query Plan、Query Slice、Query Slice Run、容量 Lease 及来源命中所需的最小持久模型，严格依据唯一实施方案。

要求：

- 使用新的 additive Alembic revision，正确链接当前 head；
- 添加数据库约束、索引、唯一幂等键、租户字段和时间字段；
- 如果现有 `DiscoveryCandidateHit` 能无损表达切片/来源命中则扩展，否则新增专用表，并记录选择原因；
- 给 `SearchTask` 增加兼容字段，但保留 `brand_limit`；
- 实现 Query Plan、Slice、SliceRun 状态机；
- 实现稳定规范化和哈希：Unicode、trim、casefold、国家标准化、数组排序去重、稳定 JSON；
- 重复切片必须阻止锁定；
- 锁定后不可修改；
- 使用乐观并发控制，两个浏览器不得静默覆盖草稿；
- 锁定 Query Plan、配置快照和 TaskVendorPlan 必须同事务完成；
- 实现 preview、创建、读取、编辑、切片 CRUD、锁定和运行记录 API；
- API 必须鉴权并执行组织/租户隔离；
- `preview` 不能信任前端隐藏字段，最终创建时服务端重新校验；
- AI 关闭或失败时，本地规则生成最小有效切片；不得伪造 AI 成功。

Phase A 验证：

- 模型约束、迁移、状态转换；
- 规范化哈希稳定性；
- 重复切片；
- 锁定不可变；
- 并发编辑冲突；
- 权限和租户隔离；
- AI/local fallback；
- 旧任务读取和 legacy 执行。

只有 Phase A 相关测试和适用静态检查通过后才进入 Phase B。

### 七、Phase B：低操作量用户体验

将任务创建流程改为“单页快速创建 + 可选切片抽屉”，不得要求普通用户维护内部技术参数。

默认只显示：

- 搜索目标；
- 目标合格商家数；
- 无法从目标可靠识别时才要求国家；
- 系统理解摘要；
- “开始搜索”主按钮；
- “查看和调整查询方向”次要入口。

实现：

- precision/balanced/volume 三个业务预设，默认 balanced；
- 切片折叠摘要；
- 编辑、复制、新增、禁用、删除、优先级和恢复建议；
- 生成原因和覆盖目标概念；
- Provider 不支持筛选时显示后置筛选说明，但不暴露 Adapter、offset、哈希等内部细节；
- 高级数量、候选上限、调用上限、预算和重复模式默认折叠；
- 锁定后通过“修改条件”创建新版本；
- 保留现有暂停、恢复、取消和任务列表能力；
- 加载、空、慢、部分成功、失败、权限不足、离线/重连状态；
- 键盘、焦点、语义标签、非颜色状态、合理对比度和响应式布局。

Phase B 验证：

- 普通用户最多三次操作启动任务；
- AI 不可用时仍可创建；
- 所有草稿编辑操作；
- 乐观冲突提示；
- 桌面和窄屏真实渲染检查；
- `vue-tsc` 和生产构建。

编译通过不能替代视觉检查。

### 八、Phase C：切片真实执行闭环

修复当前断点：`provider_query_planning` 生成的计划必须正式保存并真实驱动 `provider_search`，不能继续只将原始 `task.filters` 交给 Provider。

实现：

- 定义最小 `DiscoverySourceAdapter`/能力契约；
- 用桥接器复用现有 `execute_provider_waterfall`，不复制 Vendor 逻辑；
- 将每个切片转换为现有 Provider payload；
- 记录 unsupported filters，并以后置验证方式处理；关键条件无法验证时不得使用该来源；
- 每次只执行一个切片页；
- 使用独立 Provider/operation 游标；
- 跨切片、跨页、跨 Provider 去重，同时保存全部来源命中；
- 每页持久化后重新判断目标量、候选上限、调用上限、预算、取消和来源耗尽；
- 达量立即停止剩余调用；
- 单切片失败不使已有结果丢失；
- fallback 只能使用任务快照中的 Vendor Plan；
- 标准化切换原因；
- `low_quality` 只有达到版本化最小样本后才允许触发；
- 下游证据和评分继续使用现有 Pipeline V2，不生成虚构分数。

Phase C 验证：

- 每个切片页最多一次成功外部调用；
- 重复 Celery 投递复用持久结果；
- 达量、候选上限、预算和全部耗尽；
- 跨来源去重与命中保留；
- 429、5xx、额度不足和 fallback；
- 暂停、取消、恢复和 Worker 崩溃；
- legacy 任务回归。

### 九、Phase D：多任务公平调度和明显感知

实现数据库支持的准入与容量 Lease。不得仅用进程内变量统计并发。

调度顺序：

```text
按组织轮转
-> 组织内按任务轮转
-> 每个任务每轮最多一个发现页
-> 用户交互任务优先于自动刷新
```

实现治理配置：

- system_max_active_tasks；
- tenant_max_active_tasks；
- tenant_max_queued_tasks；
- task_max_active_slices；
- provider_max_concurrency；
- credential_max_concurrency；
- website_fetch_max_concurrency；
- ai_max_concurrency；
- queue_backlog_limit；
- slice_max_count；
- slice_empty_page_threshold；
- lease_duration、heartbeat_interval、retry_backoff。

要求：

- 配置走现有系统配置治理入口；
- 任务级业务限制进入配置快照，纯运行容量可以动态变化但不得改变任务语义；
- 扩展现有恢复扫描：queued、过期 leased/running、retryable、过期容量 Lease；
- Redis 重启后可根据数据库继续；
- API 查看、暂停和取消不能被后台任务阻塞；
- 队列达到上限时返回明确、可操作的错误；
- 任务状态提供 queue_reason、last_progress_at 和切片计数。

前端任务中心显示：

- 运行中、排队中、需要处理、已完成；
- 真实业务阶段；
- 已完成切片/总切片；
- 候选、新增、合格、待审核和目标数；
- 排队、限流、额度、重试等待的用户语言原因；
- 部分结果可立即查看；
- 新结果不自动打乱用户正在操作的列表。

启用现有认证 SSE，并实现：

- Last Event ID 补读；
- event_id 去重；
- 断线退避重连；
- SSE 不可用时轮询降级；
- 初次加载以数据库快照为准。

Phase D 验证：

- 一个大任务不阻塞多个小任务；
- 多组织公平；
- 同一 Credential 不超配；
- Worker、Redis 重启恢复；
- SSE 断线不丢事件、不重复计数；
- 背景繁忙时交互 API 可用；
- 队列过载保护。

### 十、Phase E：重复执行、统计、发布和文档

实现：

- `new_only`：默认，继续安全游标并排除历史候选；
- `refresh_stale`：新增发现与旧数据刷新分别统计；
- `re_evaluate`：不调用发现 Provider，新增评分历史；
- 语义未变切片才可继承游标，兼容判断必须确定性并可审计；
- 统一漏斗：raw、normalized、cross-slice duplicate、historical duplicate、hard filtered、evaluated、qualified、review、rejected；
- 明确 stop_reason；
- 指标、告警信号和运行恢复文档；
- 更新用户手册、架构、部署和故障排查；
- disabled/shadow/review/active 和 rollout percentage；
- 任务创建时冻结 rollout 决策；
- 应用回滚优先关闭 rollout，历史数据不删除。

Phase E 验证：

- 重复执行不会默认重抓相同页；
- 修改语义后不错误继承游标；
- 漏斗分项与任务汇总一致；
- shadow 不改变正式执行结果；
- review/active 与百分比稳定；
- 回滚到 legacy 路径不丢 Query Plan、命中、事件、成本或审计。

### 十一、迁移验证

迁移必须遵循 expand -> migrate/backfill -> switch -> contract。本版本不执行破坏性 contract。

必须验证：

1. 空数据库 `alembic upgrade head`；
2. 现有数据库克隆升级；
3. 旧数据和旧任务读取；
4. downgrade 一版；
5. 再次 upgrade head；
6. downgrade 不删除旧候选和历史评分；
7. 老应用兼容 additive 字段的回滚路径。

不得对真实生产数据库执行破坏性验证；使用备份或隔离克隆。

### 十二、最终强制验证

完成全部 Phase 后实际执行并记录：

```text
cd backend
python -m pytest -q
python -m ruff check app tests migrations
python -m compileall -q app migrations

cd ../frontend
pnpm exec vue-tsc --noEmit
pnpm run build
```

若仓库包含适用 Go 服务且本次没有修改，可记录未受影响；若修改则执行：

```text
cd services/email-verifier
go vet ./...
go test ./...
```

还必须执行：

- 迁移 upgrade/downgrade/upgrade；
- `/health`、`/ready`；
- Worker、Beat、Outbox 和恢复日志检查；
- 多任务并发与故障注入场景；
- SSE 重连和轮询降级；
- 桌面与窄屏视觉检查；
- 扫描真实密钥、Authorization、Token、密码、调试输出和硬编码 Vendor URL；
- 核对所有用户要求是否真实实现。

如果某项因外部环境无法运行，必须说明具体命令、阻塞原因、未验证风险和人工验证步骤。不得把未运行写成通过。

### 十三、禁止事项

- 不新增未接入真实路径的“未来框架”来假装完成；
- 不将所有新逻辑继续堆入巨型 `services.py`；
- 不复制现有状态、评分、Provider 或配置策略；
- 不在前端实现后端业务规则；
- 不让 UI 直接拼 Vendor 请求；
- 不用 Redis 作为最终状态；
- 不通过增加 Worker concurrency 掩盖缺少准入和公平调度；
- 不删除或跳过失败测试；
- 不降低断言、关闭类型检查或吞掉异常以获得绿色构建；
- 不提交 `.env`、数据库 dump、备份、真实商家联系人或密钥；
- 不执行真实生产放量和真实昂贵 Provider 压力测试；
- 不宣称未经迁移、并发、故障和视觉验证的功能已生产可用。

### 十四、最终交付格式

最终回复必须按以下固定结构输出，以便不同平台结果可比较：

```markdown
# 用户结果
一句话说明用户现在可以完成什么。

# 实现范围
- Phase A: complete|partial|not_started
- Phase B: ...
- Phase C: ...
- Phase D: ...
- Phase E: ...

# 关键架构决定
- ...

# 变更文件
- 绝对路径或仓库路径：作用

# 数据库迁移
- revision、upgrade、downgrade、兼容和回滚结果

# 配置与启用
- 新配置、默认值、rollout 和安全启用步骤

# 验证证据
- command: ...
  result: pass|fail|not_run
  summary: ...

# 视觉与并发验证
- 桌面、窄屏、多任务、公平性、恢复、SSE

# 发布与回滚
- 上线顺序
- 中止信号
- 回滚步骤

# 未完成项和风险
- 不得隐藏

# 下一步
- 一条最小、安全、具体的后续动作
```

同时将相同事实更新到 `docs/query-slicing-implementation-status.md`。最终回复和状态文件不一致时，以测试日志和仓库事实为准并修正状态文件。

若上下文、时间或环境不足以一次完成全部 Phase，不要虚假完成：交付已经形成真实纵向价值的 Phase，完成对应验证，更新状态文件，并在“Next Exact Action”留下下一个平台可直接继续的具体动作。

---

## 各平台启动方式

以下只改变启动方式，不改变 Prompt 正文。

### Codex / Claude Code / Cursor Agent / Copilot Agent

在仓库根目录打开 Agent，将本文从“统一执行 Prompt”开始的全部内容原样提交。

### 上下文较小的平台

第一次提交统一 Prompt，并附加：

```text
本次只执行 Phase A。完成后运行 Phase A 验证并更新
docs/query-slicing-implementation-status.md，不进入 Phase B。
```

后续会话提交同一统一 Prompt，并附加：

```text
先读取并核验 docs/query-slicing-implementation-status.md，
从 Next Exact Action 继续，只执行下一个未完成 Phase。
```

### 平台切换

新平台必须先执行：

```text
完整读取统一 Prompt、唯一实施方案、状态文件和决定记录；
核对状态文件列出的变更文件与测试证据；
不要重做已经验证完成的 Phase；
从 Next Exact Action 继续。
```

不得要求新平台依赖前一平台的聊天上下文。
