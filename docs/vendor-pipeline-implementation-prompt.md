# BuyerReach Apollo/Hunter 独立全流程管线：VS Code 执行 Prompt

用途：将本文直接交给 VS Code 中具备仓库读写与终端能力的 Coding Agent 执行。

执行目录：`D:\buyer reach`

需求范围：品牌探查与精准匹配任务均支持仅 Apollo、仅 Hunter、Apollo + Hunter；每个选中的 Vendor 从品牌/公司搜索执行到联系人与邮箱获取。

## 0. 执行纪律

你正在 BuyerReach 仓库中实施生产功能，不是在制作演示或孤立原型。持续工作直到功能、迁移、测试、前端和交付证据全部完成；遇到真实阻塞时，先穷尽仓库内安全可执行的检查和替代方案，再报告阻塞。

必须遵循以下优先级：

1. 法律、安全和用户明确指令；
2. `docs/DEVELOPMENT_RULES.md`；
3. `docs/pipeline-production-architecture-v1.md`；
4. `docs/production-integrity-audit-2026-07-17.md`；
5. 本文的产品需求和验收标准；
6. 模块局部约定与工具默认行为。

本文不是第二套架构规则。若本文与更高优先级规则冲突，采用更高优先级规则，并把冲突、决定、影响和验证方式写入 `docs/vendor-pipeline-implementation-decisions.md`。

禁止：

- 覆盖或清理无关用户修改；
- 绕过 `transition_task`、`transition_candidate`、Pipeline Stage Runner、Checkpoint、TaskItem、Outbox 或审计；
- 在 `services.py` 新建一条巨型 Vendor 业务旁路；
- 复制 `execute_provider`、Provider Adapter、去重、事件或 Outbox 核心逻辑；
- 在前端实现后端 Vendor 路由和能力规则；
- 删除失败测试、降低断言、吞掉异常或关闭类型检查；
- 在快照、日志、事件、审计或来源证据中保存 API Key、Authorization、Token、密码或明文 Credential；
- 把估算调用量、推断邮箱或未知验证结果显示为真实测量结果。

## 1. 开始前必须完成

完整阅读：

- 根目录 `AGENTS.md`；
- `docs/DEVELOPMENT_RULES.md`；
- `docs/pipeline-production-architecture-v1.md`；
- `docs/production-integrity-audit-2026-07-17.md`；
- 本文；
- 相关模型、迁移、Provider Adapter、Pipeline、Worker、API、前端创建入口和测试。

追踪现有完整路径：

```text
创建任务
→ configuration_snapshot / TaskVendorPlan
→ 排队与 Worker
→ company/provider_search
→ 候选过滤、评分和状态机
→ Company / Brand
→ Contact / ContactPosition
→ EmailAddress / EmailVerification
→ TaskItem / PipelineStageRun / Checkpoint
→ Outbox / 进度 / 失败恢复
```

检查工作树并保留无关修改。记录基线命令及真实结果：

```text
cd backend
python -m pytest -q
python -m ruff check app tests migrations
python -m compileall -q app migrations

cd ../frontend
pnpm exec vue-tsc --noEmit
pnpm run build
```

无法执行的命令必须说明原因和风险，不得写成通过。创建或更新：

- `docs/vendor-pipeline-implementation-status.md`
- `docs/vendor-pipeline-implementation-decisions.md`

状态文档只记录事实、命令和结果，不得成为第二套架构来源。

## 2. 用户结果与业务不变量

品牌探查 `brand_discovery` 和精准匹配 `exact_brand` 创建时都必须选择：

- 仅 Apollo；
- 仅 Hunter；
- Apollo + Hunter。

业务不变量：

1. Apollo-only 任务的 Hunter 商业接口调用次数必须为零；
2. Hunter-only 任务的 Apollo 商业接口调用次数必须为零；
3. Apollo + Hunter 必须执行两条管线，不得在第一个 Vendor 成功后停止；
4. 未选择的 Vendor 不得被 `enabled_providers()`、默认策略或隐藏 fallback 自动补回；
5. 单条管线失败不得回滚另一条已经提交的结果；
6. 公司、联系人和邮箱去重后，仍保留每个 Vendor 的独立来源证据；
7. 旧任务继续按照已冻结的 legacy waterfall 计划运行；
8. 新任务运行期间不得因系统配置变化而改变 Vendor、Adapter、Credential 引用或阶段路由；
9. API Key 只在后台系统配置中维护，任务仅保存受治理的 Credential ID 引用；
10. Apollo/Hunter 没有返回邮箱时必须如实显示“未获取到邮箱”，不得伪造或跨 Vendor 补齐。

## 3. 两类任务的完整流程

### 3.1 品牌探查

```text
解析搜索意图
→ 所选 Vendor 公司搜索
→ 候选规范化
→ 黑名单与已有客户过滤
→ 国家、品类、官网证据处理
→ AI/规则相关性评分
→ 结果分类
→ 仅对合格候选执行联系人和邮箱阶段
→ 正式实体持久化
→ 跨 Vendor 去重和来源合并
```

只有满足以下条件的候选才能进入联系人与邮箱付费阶段：

- 未被黑名单排除；
- 不是已有客户；
- 国家、品类与官网条件满足；
- 达到最低相关性；
- 未超过 `brand_limit`；
- 任务未暂停或取消；
- 未超过预算、Vendor 额度、重试上限和最大调用数。

品牌探查不再只停留在候选列表；合格候选需要沿现有状态机进入 enriching，并在成功后形成可用的公司、品牌、联系人和邮箱结果。不得对所有未过滤原始候选直接执行联系人搜索。

### 3.2 精准匹配

```text
目标品牌名 + 用户确认的官方域名
→ 所选 Vendor 公司搜索
→ 品牌名与官方域名严格校验
→ 联系人搜索
→ 邮箱获取
→ 邮箱验证或真实性评估
→ 正式持久化
→ 跨 Vendor 合并
```

用户确认的官方域名是最高优先级身份依据。Vendor 返回的其他域名不得覆盖它；域名不匹配时不得创建错误品牌、联系人或邮箱。保留现有 approved candidate 路径及 Candidate 状态机。

## 4. Vendor 全流程定义

### 4.1 Apollo

```text
Apollo Company Search
→ Apollo People Search
→ Apollo People Bulk Match / Enrichment
→ 提取 People Search 或 Bulk Match 返回的邮箱
→ 本地规范化与真实性评估
→ 正式持久化
```

要求：

1. 公司搜索复用 Apollo versioned Workflow Adapter；
2. 联系人搜索使用现有 Apollo People Search；
3. 对可用联系人执行 Apollo Bulk Match；
4. People Search 或 Bulk Match 任一结果包含邮箱，即作为 Apollo 来源邮箱；
5. 保存 Apollo Person ID、Provider record ID、Vendor request ID、Adapter 版本和必要来源证据；
6. Apollo 无邮箱时记录 `no_email_returned` 或等价的明确业务状态；
7. Apollo-only 禁止调用 Hunter Domain Finder、Domain Search、Email Finder 和 Email Verifier；
8. 是否揭示个人邮箱、电话等敏感字段继续由受治理的系统配置决定；
9. UI 只能承诺“执行完整邮箱获取流程”，不能承诺每个联系人必定获得邮箱。

### 4.2 Hunter

```text
Hunter Discover / Company Enrichment
→ Hunter Domain Search
→ Hunter Email Finder
→ Hunter Email Verifier
→ 本地规范化与真实性评估
→ 正式持久化
```

精准匹配已有官方域名时优先使用用户域名，不无意义调用 Domain Finder。Hunter 返回域名只能作为证据，不能覆盖用户确认域名。

### 4.3 Apollo + Hunter

```text
Apollo 公司 → People Search → Bulk Match ┐
                                          ├→ 规范化 → 去重 → 来源合并
Hunter 公司 → 联系人 → 邮箱发现/验证      ┘
```

两条管线是独立正式执行单元，不是 primary/fallback。可以有界并发或确定性顺序调度，但最终结果不得依赖完成顺序。初版优先复用现有 Worker 和持久化 Stage，不新增分布式服务。

## 5. API 合约

`SearchTaskCreate` 增加：

```python
VendorName = Literal["apollo", "hunter"]

selected_vendors: list[VendorName] | None = Field(
    default=None,
    min_length=1,
    max_length=2,
)
```

服务端派生模式：

```text
["apollo"]            → apollo_only
["hunter"]            → hunter_only
["apollo", "hunter"] → apollo_hunter
None                    → legacy compatibility only
```

规则：

- 新前端创建两种任务时必须传值；
- 显式空数组、重复值和未知 Vendor 返回 422；
- 双 Vendor 顺序由服务端规范化；
- 不允许前端同时提交 `selected_vendors` 和 `execution_mode`；
- 服务端验证 Vendor 已启用、Credential 存在、连接状态可用；
- 不得在响应中返回 Credential 或敏感连接信息；
- API 与所有查询执行组织/租户隔离。

新增只读能力接口，具体路径遵循现有 API 约定：

```text
GET /vendor-capabilities?task_mode=brand_discovery
GET /vendor-capabilities?task_mode=exact_brand
```

至少返回 Vendor 名称、是否启用、是否可用、支持阶段、邮箱获取方式和不可用原因。前端只负责展示与禁用，不负责决定后端路由。

## 6. TaskVendorPlan 与配置冻结

保留现有字段，additive 增加：

```python
execution_mode: Mapped[str]
# legacy_waterfall | apollo_only | hunter_only | apollo_hunter

selected_vendors: Mapped[list]
# [] | ["apollo"] | ["hunter"] | ["apollo", "hunter"]

pipeline_source: Mapped[str]
# legacy_global_strategy | user_selection

vendor_routes: Mapped[dict]
# 无密钥的冻结 Stage 路由
```

`vendor_routes` 至少冻结：

- Vendor；
- Adapter version；
- Credential ID 引用；
- 每个 Stage 的 Provider 名称；
- Stage 是否启用；
- 邮箱获取方式；
- 与任务有关的安全配置引用。

创建任务时先完成输入校验和可用性检查，再在同一创建事务中保存 `SearchTask`、`configuration_snapshot` 和 `TaskVendorPlan`。运行、重试、恢复均读取冻结计划，不重新解析全局 Vendor Strategy。

旧字段 `primary_vendor`、`fallback_vendors`、`verification_vendor` 暂不删除，供旧任务和旧读取器兼容。

## 7. Pipeline 与模块边界

不要把所有流程写进 `backend/app/modules/services.py`。优先在现有架构中形成以下职责：

```text
pipeline.vendor_routing
- 读取和验证冻结计划
- 按 task + vendor + stage 解析 Provider
- 阻止未选择 Vendor 被自动补回

pipeline.stages
- vendor_company_search
- candidate_filtering / scoring / classification（复用现有 Stage）
- vendor_contact_search
- vendor_contact_enrichment
- vendor_email_discovery
- vendor_email_verification
- vendor_result_merge

pipeline.runner
- Stage 幂等、lease、恢复和下游调度

providers.workflows / providers.vendors
- Vendor URL、认证、请求构造、响应映射、能力与错误映射

modules.services
- Company/Brand/Contact/Email 正式实体服务
- 业务去重、TaskItem、审计
```

如果现有文件结构可用更小的改动达到同样边界，可以复用，但不得建立绕过真实执行路径的新抽象。

## 8. Stage、事务和幂等

每个外部或付费 Stage 必须具有独立数据库执行记录。幂等键至少包含：

```text
task_id + entity_scope + vendor + stage_name + stage_version + input_hash
```

每次付费调用前必须检查：

- 任务暂停/取消；
- 任务预算；
- Vendor 额度；
- 重试次数；
- rollout；
- 已完成 Checkpoint；
- Stage lease。

调用成功后，规范化结果、正式/阶段结果、Checkpoint 完成状态和 Outbox 事件必须在同一事务提交；提交成功后才调度下游。仅 `flush` 不代表独立持久化。

Worker 必须恢复 queued、retryable、过期 running 和过期 lease。重复投递、Worker 重启和下游重复唤醒不得产生重复付费调用或重复正式实体。

## 9. 来源证据

`DiscoveryCandidateHit` 继续表达候选与任务之间的匹配/评分关系。不得为 Apollo、Hunter 对同一 candidate/task 重复插入该表，因为现有 `(candidate_id, task_id)` 唯一约束不允许这样做。

优先复用并 additive 扩展现有 `SourceEvidence`，根据实际模型补充必要的可空字段：

```text
task_id
stage_run_id
vendor_request_id
provider_record_id
adapter_version
input_hash
observed_at
normalized_evidence
```

来源证据可关联 DiscoveryCandidate、Company、Brand、Contact、EmailAddress。设计可重放的唯一约束或等价幂等规则，避免重复事件生成重复来源记录。

不得保存完整敏感 Vendor 响应；只保留业务需要、已脱敏、可审计的证据。

## 10. 去重与确定性合并

### 公司/品牌

1. 用户确认官方域名；
2. 规范化域名；
3. 无域名时 `normalized_name + country`。

用户域名不得被 Vendor 覆盖。相同实体只创建一次，但每个 Vendor 命中分别保存来源证据。

### 联系人

按以下顺序判断：

1. 同 Vendor Person ID；
2. 规范化 LinkedIn URL；
3. Company + 规范化姓名；
4. Company + 姓名 + 当前职位。

不确定是否同一人时不要强制合并。复用 ContactPosition 表达职位关系。

### 邮箱

按组织内 `normalized_address` 去重。相同邮箱只创建一个 EmailAddress，但 Apollo/Hunter 来源分别保存；每个 Vendor 的验证结果分别创建 EmailVerification，最终状态由现有确定性真实性策略计算，不能采用最后写入者覆盖。

字段冲突必须定义明确优先级：允许有证据的非空值补充空值，不允许无审计覆盖用户确认值或已有高置信值。

## 11. 状态与失败语义

Vendor Stage 支持：

```text
queued | running | completed | failed | retryable | cancelled | skipped | unsupported
```

规则：

1. 至少一个 Vendor 产生可用正式结果：任务可完成；其他 Vendor 失败时记录 `provider_warnings` 和部分失败信息；
2. 所有 Vendor 成功调用但无匹配结果：显示“未找到结果”，不得描述为技术故障；
3. 所有 Vendor 因技术故障失败：任务 failed/retryable；
4. 单 Vendor 无额度时不得切换到未选择 Vendor；
5. 暂停/取消后不再启动新付费调用，已提交结果保留；
6. 恢复从未完成 Stage 继续；
7. 重试只重试 failed/retryable Stage；
8. `unsupported` 与 `no_results`、`quota_exhausted`、`request_failed` 必须区分；
9. Apollo 没返回邮箱是有效空结果，不自动调用 Hunter。

为保持兼容，初版可使用现有 task `completed` 加 `provider_warnings` 表达部分成功，不强制新增 `completed_with_warnings` 枚举。

## 12. 预算、成本和可观测性

品牌探查必须先筛选合格候选，再发起联系人和邮箱调用。严格执行：

- `brand_limit`；
- `contacts_limit_per_brand`；
- `budget_limit`；
- 最大 Provider 调用数；
- Vendor quota；
- 有界并发。

记录 task/vendor/stage 维度：调用次数、成功、失败、重试、费用、排队时间、运行时间、公司数、联系人数、邮箱数、去重数和部分失败数。

日志关联：

```text
trace_id, task_id, candidate_id/company_id, stage_run_id, vendor, vendor_request_id
```

## 13. 前端

品牌探查和精准匹配都显示三项单选卡：仅 Apollo、仅 Hunter、Apollo + Hunter。不要使用 Prospeo，不允许空选择。

品牌探查表单增加或启用：

- 目标职位；
- 每品牌联系人上限；
- 成本/调用规模提示。

任务详情显示执行模式以及每个 Vendor 的公司、联系人、邮箱阶段状态、实际数量、实际费用和错误/警告。覆盖 loading、queued、running、partial、completed、no-results、failed、paused、cancelled、permission-denied 和 reconnect 状态。

必须检查键盘操作、焦点、语义标签、非颜色状态提示、对比度、桌面和窄屏布局。

修改所有创建入口：普通创建、快速创建、AI 协调创建、API 创建和复制任务。复制任务复制业务选择，但重新捕获当前无密钥配置与 Credential ID；Vendor 不可用时保持 draft 并提示，不静默改选其他 Vendor。

## 14. 迁移与兼容

先检查当前 Alembic head，不要盲目硬编码 revision。已知当前仓库最后迁移曾为 `20260721_0029_query_slicing_additive.py`；若仍为 head，创建下一 additive revision。

迁移至少包括 TaskVendorPlan 新字段，以及 SourceEvidence 所需追踪字段/索引。旧记录默认：

```text
execution_mode = legacy_waterfall
selected_vendors = []
pipeline_source = legacy_global_strategy
vendor_routes = {}
```

要求：

- 不删除旧字段和历史结果；
- 不猜测回填旧任务 Vendor 语义；
- 新旧读取器兼容；
- 空数据库可初始化；
- 已有数据库可升级；
- downgrade one revision 后可再次 upgrade；
- downgrade 只删除本次新增结构。

## 15. Rollout 与回滚

通过系统配置提供：

```text
disabled → shadow → review → active + rollout_percentage
```

先部署 additive migration，再部署兼容后端、能力 API 和前端；依次验证 Apollo-only、Hunter-only、双 Vendor，观察费用、失败率、重试率和去重质量后逐步全量。

回滚时先关闭 Feature Flag 和回滚应用镜像。已创建的新任务继续按冻结计划运行；不得在运行中重写 Vendor。回滚不得删除历史 Stage、来源、费用或正式结果。

## 16. 实施顺序

### Phase 1：模型、迁移、API 与计划冻结

- 扩展 Schema、TaskVendorPlan 和 SourceEvidence；
- 创建 additive migration；
- 新增 Vendor capability API；
- 创建任务时校验并冻结无密钥计划；
- 覆盖旧客户端和旧任务兼容测试。

### Phase 2：严格 Vendor 路由

- 新增或收敛 task-aware Vendor routing；
- 阻止 `enabled_providers()` 自动补回未选择 Vendor；
- legacy waterfall 仅用于旧任务；
- 为每个 Vendor Stage 建立持久化执行记录。

### Phase 3：Apollo 纵向切片

- 两种任务的 Company Search；
- People Search；
- Bulk Match；
- 邮箱提取、持久化、来源证据；
- Apollo-only 零 Hunter 调用测试。

### Phase 4：Hunter 纵向切片

- 两种任务的公司、域名、联系人、邮箱、验证；
- Hunter-only 零 Apollo 调用测试。

### Phase 5：双 Vendor 合并

- 两边都执行；
- 公司、联系人、邮箱去重；
- 多来源证据；
- 单边失败、定向重试与部分成功。

### Phase 6：品牌探查联系人/邮箱下游

- 只丰富合格候选；
- 接通 Candidate 状态机；
- 目标职位、联系人上限、预算和调用上限。

### Phase 7：前端、运维与发布验证

- 所有创建入口和任务详情；
- 桌面/窄屏视觉检查；
- 指标、告警、运行恢复和 Rollout。

每个 Phase 必须形成可运行、可测试、接入真实路径的纵向切片，不得先创建一批未接线的模型或接口。

## 17. 必须测试的行为

### 路由隔离

- brand_discovery + Apollo-only：Hunter 调用为 0；
- exact_brand + Apollo-only：Hunter 调用为 0；
- brand_discovery + Hunter-only：Apollo 调用为 0；
- exact_brand + Hunter-only：Apollo 调用为 0；
- 双 Vendor 两边均执行；
- 第一个 Vendor 成功后第二个仍执行。

### Apollo

- People Search 直接返回邮箱；
- People Search 无邮箱后执行 Bulk Match；
- Bulk Match 邮箱正确写入；
- Apollo 无邮箱时明确记录空结果；
- Apollo-only 不调用 Hunter 补齐。

### Hunter

- 公司、联系人、邮箱和验证完整；
- 有官方域名时不重复 Domain Finder；
- Hunter-only 不调用 Apollo。

### 去重与来源

- 相同品牌、联系人、邮箱只创建一份正式实体；
- Apollo/Hunter 来源均保留；
- 重复事件和重试不重复写入；
- Vendor 完成顺序不影响最终结果；
- 用户确认官方域名不被覆盖。

### 可靠性

- 单边失败不回滚另一边；
- 429、无额度、超时和解析失败；
- Worker 重复投递；
- Vendor 成功后、提交前崩溃；
- 提交后、下游调度前崩溃；
- 暂停、取消、恢复；
- 预算耗尽保留部分结果；
- 只重试失败 Stage；
- Outbox 失败后安全重试。

### 兼容与安全

- 旧任务 waterfall 行为不变；
- 新任务空选择、重复和未知 Vendor 返回 422；
- 配置变化不影响在途任务；
- 租户隔离和越权拒绝；
- 快照、日志、事件和来源证据无密钥；
- 未知新增状态/字段不会使前端崩溃。

## 18. 验证命令

先执行针对性测试，再执行完整验证。测试文件名按实际实现调整，不要为了匹配本文制造空壳测试。

```text
cd backend
python -m pytest tests/test_vendor_execution_plan.py -q
python -m pytest tests/test_provider_waterfall.py -q
python -m pytest tests/test_pipeline_integrity.py -q
python -m pytest tests/test_vendor_workflow_state.py -q
python -m pytest -q
python -m ruff check app tests migrations
python -m compileall -q app migrations

alembic upgrade head
alembic downgrade -1
alembic upgrade head

cd ../frontend
pnpm exec vue-tsc --noEmit
pnpm run build
```

运行检查：

- `/health`、`/ready`；
- 数据库 revision；
- Worker 与恢复日志；
- Celery Beat；
- Outbox backlog；
- Vendor 调用、费用、重试和失败指标。

前端必须实际检查桌面与窄屏的创建、运行、部分成功、失败、暂停、取消和重试路径。构建成功不能替代视觉验证。

## 19. 完成定义

只有全部满足才可宣称完成：

- [ ] 两种任务均能选择 Apollo、Hunter 或两者；
- [ ] Apollo 从公司搜索执行到 People Search、Bulk Match 和邮箱持久化；
- [ ] Hunter 从公司执行到邮箱发现和验证；
- [ ] 单 Vendor 模式严格零跨 Vendor 商业调用；
- [ ] 双 Vendor 模式两边都执行；
- [ ] 品牌探查只丰富合格候选；
- [ ] 公司、联系人、邮箱正确去重；
- [ ] 每个 Vendor 来源证据完整；
- [ ] Stage 幂等、暂停、取消、重试和恢复有效；
- [ ] 单边失败不回滚另一边结果；
- [ ] 旧任务行为不变；
- [ ] 配置快照和运行数据无密钥；
- [ ] 迁移 upgrade/downgrade/upgrade 通过；
- [ ] 后端全量测试、ruff、compileall 通过；
- [ ] 前端类型检查和构建通过；
- [ ] 桌面和窄屏 UI 已实际验证；
- [ ] Rollout 和回滚步骤明确且可执行；
- [ ] 状态与决策文档和实际证据一致。

## 20. 最终交付格式

完成后按以下顺序报告：

1. 已实现的用户结果；
2. 关键架构选择；
3. 修改文件列表；
4. 数据库迁移与兼容处理；
5. Apollo、Hunter、双 Vendor 的实际行为；
6. 安全、预算、幂等、恢复和来源证据处理；
7. 实际执行的每条验证命令及结果；
8. UI 实际检查范围；
9. Rollout 与回滚步骤；
10. 未验证项、剩余风险和后续工作。

不得把未执行的测试写成通过，不得把仅能编译描述为生产可用。
