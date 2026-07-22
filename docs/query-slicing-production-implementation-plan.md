# BuyerReach 查询切片生产实施方案

状态：可执行设计稿  
适用版本：Pipeline V2 及后续兼容版本  
范围：查询切片、目标数量、筛选、重复执行、多任务并发、现有 Provider 路由  
不在本期：Wikidata、政府注册库、展会/协会目录、搜索引擎和其他新数据源的具体接入

## 1. 决策摘要

本期将现有 `provider_query_planning` 从“只写 Stage 输出但不参与执行”升级为正式、可审核、可锁定、可恢复的查询契约。普通用户只需输入目标、确认目标数量并点击开始；系统自动生成和校验切片，用户可选地展开、修改或补充。

任务启动后，调度器以“切片的一页”为最小工作单元，公平调度多个组织和多个任务。每次调用前检查取消、预算、并发槽位、Provider 额度和幂等记录；每页完成后持久化结果与游标，再决定是否继续。数据库始终是真相来源，Celery/Redis 只负责唤醒和短期限流。

本期继续使用现有 Hunter/Apollo/Prospeo 能力与 `TaskVendorPlan`，但查询切片保持 Vendor 中立。后续新增免费数据源时，只实现标准 `DiscoverySourceAdapter` 并加入能力路由，不修改查询计划、任务主流程或主要 UI。

## 2. 用户结果与业务不变量

### 2.1 用户结果

用户能够：

1. 用一句自然语言描述目标商家；
2. 选择希望获得的合格商家数量；
3. 快速确认系统理解，直接开始搜索；
4. 可选地查看、修改、新增、复制、禁用或删除查询切片；
5. 在任务运行时看到排队原因、真实阶段、部分结果和每个切片的产出；
6. 暂停、恢复或取消，而不丢失已经获得的结果；
7. 再次执行时默认只获取新增商家并延续已有游标；
8. 在来源不足时看到真实停止原因，而不是虚假的成功或自动降低质量门槛。

### 2.2 业务不变量

- 目标数量是“符合筛选条件的合格商家目标”，不是截取 Provider 前 N 条。
- 系统不得为了凑足数量降低用户锁定的筛选条件。
- 未经官网或等价企业级证据验证的候选不得显示为已验证合格。
- 用户修改后的切片必须原样保存；运行中不得被 AI 或系统配置静默覆盖。
- 任务启动后锁定 Query Plan、版本、Provider 路由、预算和筛选策略。
- 跨切片、跨页、跨 Provider 的同一域名只形成一个候选，但保留全部来源命中记录。
- 每个外部调用必须有持久执行记录、幂等键、超时、取消检查、额度检查和成本记录。
- PostgreSQL 是计划、切片、任务、游标、事件和结果的最终真相来源。
- 老任务和在途任务继续兼容原有 `task.filters` 执行路径。

## 3. 范围和安全假设

### 3.1 本期包含

- Query Plan 与 Query Slice 的数据库模型和状态机；
- AI/本地规则生成切片；
- 单页快速创建与可选切片抽屉；
- 目标合格量、候选上限、最大调用数和预算；
- 按切片执行现有 company-search Provider；
- 切片级游标、幂等、暂停、恢复、重试和耗尽；
- 多组织、多任务公平调度与并发槽位；
- 切片级漏斗统计和任务级汇总；
- SSE 实时更新，断开后以数据库事件补读，轮询降级；
- additive API、迁移、功能开关、shadow/review/active 发布；
- 后续数据源的标准能力契约。

### 3.2 本期不包含

- 新增任何外部数据源或抓取器；
- 自动降低最低相关性或扩大国家范围；
- 自动采集受限目录中的个人联系方式；
- 重新实现联系人和邮箱阶段；
- 构建通用爬虫或全网搜索引擎；
- 对外宣称 AI 能直接生成经过验证的商家数据。

### 3.3 初始规模假设

- 单任务最多 20 个启用切片；
- 单任务目标合格量最多 5,000，保持现有上限；
- 第一版同一任务同时只运行一个发现切片页；
- 多个任务由调度器轮转，不由单个大 Job 长时间占用 Worker；
- 所有上限必须来自系统配置并可降低，不在业务函数中硬编码。

## 4. 用户体验

### 4.1 最短路径

默认创建页只要求：

- 搜索目标；
- 目标合格商家数量；
- 当自然语言无法可靠识别时才要求补充国家。

AI 或本地规则生成后显示：

> 将寻找意大利和法国的女包品牌、零售商及进口商，目标 100 家。系统已生成 8 个查询方向。

主要按钮为“开始搜索”，次要入口为“查看和调整查询方向”。高级设置默认折叠。

### 4.2 默认预设

提供三个业务预设：

- `precision`：精准优先，严格官网、国家、产品和商家类型证据；
- `balanced`：默认，合格结果加待审核结果；
- `volume`：扩大同义词和探索切片，但不降低合格标准。

预设只转换为版本化的 `filter_policy` 和生成策略，不在 UI 与业务服务中复制规则。

### 4.3 切片抽屉

折叠状态仅显示：

- 启用切片总数；
- 核心产品、本地语言、商家类型、相邻探索的数量；
- 预计 Provider 调用范围；
- 重复切片或无法表达条件的警告。

展开后支持：

- 编辑、复制、新增、禁用、删除；
- 调整优先级；
- 查看生成原因和哪些用户目标被覆盖；
- 恢复系统建议版本；
- 查看 Provider 可能忽略的筛选条件，但不展示 Adapter、offset 或幂等键。

锁定后的计划不可直接编辑；“修改条件”创建新版本，并明确不会改变正在运行的版本。

### 4.4 任务感知

全局任务中心显示：运行中、排队中、需要处理、已完成数量。任务卡片显示：

- 当前业务阶段；
- 已完成切片数/总切片数；
- 原始候选、新增、合格、待审核数量；
- 目标数量；
- 排队或等待原因；
- 最后一次进展时间；
- 暂停、继续、取消和查看部分结果入口。

不展示未经数据支持的精确剩余时间。若未来有足够历史样本，只显示范围估计。

实时新结果不得在用户选择表格行时改变排序；显示“发现 N 条新结果”，由用户主动合并。

## 5. 模块边界

新增模块建议如下：

```text
backend/app/query_planning/
├─ models.py          # 若项目仍集中模型，则仅保留领域类型，SQLAlchemy 模型留在 modules/models.py
├─ schemas.py         # Query Plan/Slice API schema
├─ generator.py       # AI/local 生成与规范化，不包含 Vendor 协议
├─ policy.py          # 数量、筛选、停止和重复执行策略
├─ state_machine.py   # Plan/Slice/SliceRun 状态转换
├─ repository.py      # 查询与带锁领取
├─ scheduler.py       # 公平调度、准入和下一步决策
├─ capabilities.py    # Vendor 中立能力与语义
└─ service.py         # API 使用的领域服务

backend/app/providers/
└─ discovery.py       # DiscoverySourceAdapter 标准契约与现有 Provider 桥接
```

职责：

- `generator` 只生成业务切片；
- `policy` 决定停止条件、重复模式和可解释的自动行为；
- `scheduler` 只选择下一个可执行工作，不映射 Vendor 请求；
- `DiscoverySourceAdapter` 将业务切片映射为具体 Vendor 请求；
- 现有 `execute_provider_waterfall` 在本期通过桥接器复用；
- Candidate 去重、证据和评分继续由现有模块负责；
- API 只负责鉴权、输入输出和事务边界。

## 6. 数据模型

所有迁移采用 expand，不删除现有字段。

### 6.1 `search_query_plan`

| 字段 | 类型 | 约束/说明 |
|---|---|---|
| id | UUID | 主键 |
| task_id | UUID | FK search_task，索引 |
| organization_id | UUID | 租户隔离，索引 |
| version | int | 每任务单调递增 |
| schema_version | varchar(40) | 初始 `1.0.0` |
| generator_type | varchar(20) | ai/local_rules/user |
| generator_version | varchar(80) | Prompt 或规则版本 |
| status | varchar(20) | draft/review/locked/superseded |
| target_result_count | int | 合格结果目标 |
| candidate_fetch_limit | int | 候选采集上限 |
| max_provider_calls | int nullable | 最大外部调用数 |
| budget_limit | float nullable | 沿用任务预算语义 |
| repeat_mode | varchar(40) | new_only/refresh_stale/re_evaluate |
| filter_policy | JSON | 版本化业务筛选快照 |
| source_policy | JSON | 允许的能力、路由和自动切换策略快照 |
| created_by/locked_by | UUID | 审计 |
| locked_at | timestamptz nullable | 锁定时间 |
| created_at/updated_at | timestamptz | 标准字段 |

约束：`unique(task_id, version)`；每个任务最多一个 `locked` 计划，使用部分唯一索引或事务校验。

### 6.2 `search_query_slice`

| 字段 | 类型 | 约束/说明 |
|---|---|---|
| id | UUID | 主键 |
| plan_id | UUID | FK，级联仅限未运行草稿；生产历史禁止物理删除 |
| slice_key | varchar(80) | 计划内稳定标识 |
| label | varchar(255) | 用户可读名称 |
| purpose | varchar(40) | core/synonym/local_language/business_type/adjacent/exploratory |
| target_concept_ids | JSON | 对应 SearchIntent 概念 |
| countries | JSON | Vendor 中立国家列表 |
| target_concepts | JSON | 规范化产品/行业概念 |
| business_types | JSON | 品牌/零售商/进口商等 |
| include_terms/exclude_terms | JSON | 查询与后置过滤用词 |
| match_mode | varchar(10) | any/all |
| priority | int | 值小优先 |
| enabled | bool | 草稿可修改 |
| origin | varchar(20) | generated/user_added/user_modified |
| reason | text | 可解释生成理由 |
| target_count | int nullable | 可选分配目标，不强制凑数 |
| candidate_limit | int nullable | 单切片候选保护上限 |
| normalized_hash | varchar(64) | 规范化去重哈希 |
| version | int | 切片语义版本 |
| created_at/updated_at | timestamptz | 标准字段 |

约束：`unique(plan_id, slice_key)` 和 `unique(plan_id, normalized_hash)`；锁定计划下禁止修改。

### 6.3 `search_query_slice_run`

| 字段 | 类型 | 约束/说明 |
|---|---|---|
| id | UUID | 主键 |
| task_id/plan_id/query_slice_id | UUID | 索引 |
| plan_version | int | 快照关联 |
| provider | varchar(80) | 实际来源 |
| operation | varchar(80) | discover/company_search 等 |
| adapter_version | varchar(80) | 实际 Adapter 版本 |
| input_hash | varchar(64) | 规范化输入 |
| cursor_key | varchar(255) | page/offset/token 的规范化表示 |
| cursor | JSON | Adapter 私有游标，仅 Adapter 解释 |
| status | varchar(30) | queued/leased/running/retryable/completed/exhausted/failed/cancelled |
| lease_owner | varchar(120) nullable | Worker 标识 |
| lease_acquired_at/lease_expires_at/heartbeat_at | timestamptz | 恢复 |
| next_retry_at | timestamptz nullable | 退避 |
| attempts | int | 重试次数 |
| raw/new/duplicate/filtered/qualified/review_count | int | 漏斗 |
| call_count | int | 调用数 |
| cost | float | 实际成本 |
| consecutive_empty_pages | int | 耗尽判断 |
| vendor_request_id | varchar(255) nullable | 可追踪 ID |
| normalized_output | JSON | 已完成页的规范化结果或引用 |
| error_code/error_message | varchar/text | 标准错误 |
| started_at/completed_at | timestamptz | 时序 |

幂等唯一约束：

```text
task_id + query_slice_id + provider + operation + adapter_version + input_hash + cursor_key
```

### 6.4 `discovery_candidate_source_hit`

现有 `DiscoveryCandidateHit` 若能无损表达以下内容则扩展复用，否则新增表：

- candidate_id；
- task_id、plan_id、query_slice_id、slice_run_id；
- provider、operation；
- source_record_id、source_url、source_edition；
- observed_at；
- evidence 摘要；
- 规范化输入哈希。

候选按域名或现有 name+country 规则去重，但每次命中都保留来源记录。

### 6.5 `scheduler_capacity_lease`

第一版优先使用数据库租约表统一管理系统、租户、Provider、凭证和任务槽位，避免多 Worker 的进程内计数不一致。字段包括：

- scope_type、scope_key；
- holder_type、holder_id；
- slots；
- lease_owner、lease_expires_at；
- created_at/updated_at。

唯一性与行锁确保容量不超卖。Redis 可作为后续优化的短期令牌桶，但删除 Redis 后仍可根据数据库恢复。

### 6.6 `search_task` additive 字段

新增 nullable/默认兼容字段：

- active_query_plan_id；
- target_result_count；
- candidate_fetch_limit；
- max_provider_calls；
- repeat_mode；
- queue_reason；
- queued_at、admitted_at、last_progress_at；
- active_slice_count、waiting_slice_count、completed_slice_count。

`brand_limit` 保留。新任务将其兼容映射到 `candidate_fetch_limit`；旧客户端继续可读写，在兼容窗口内不得删除。

## 7. 状态机

### 7.1 Query Plan

```text
draft -> review -> locked
draft/review -> superseded
locked -> superseded  # 仅当新版本锁定，历史仍只读
```

只有 `draft/review` 可编辑。锁定与任务配置快照、`TaskVendorPlan` 更新必须在同一事务完成，并写 Outbox。

### 7.2 Slice Run

```text
queued -> leased -> running -> completed
                         |-> exhausted
                         |-> retryable -> queued
                         |-> failed
                         |-> cancelled
leased -> retryable      # lease 过期恢复
queued/leased/running/retryable -> cancelled
```

`completed` 表示当前页成功；是否继续下一页由调度器创建下一条 Run 决定。`exhausted` 表示当前“切片 × 来源”没有可继续的游标或连续无新增达到锁定阈值。

### 7.3 Task 聚合状态

- `queued`：等待准入或等待可执行切片；
- `running`：至少一个切片运行或调度器正在推进；
- `partial`：存在可用结果，但预算、部分失败或部分耗尽导致未完全达到目标；
- `completed`：达到目标或所有计划正常结束；
- `failed`：没有可用结果且不存在可恢复路径；
- `paused/cancelled`：沿用现有状态机，已获得结果保留。

所有状态变化必须调用统一 transition 函数并与 Outbox 事件同事务写入。

## 8. 生成、规范化与锁定

### 8.1 生成输入

复用 `SearchIntent`，生成器必须输出 Vendor 中立字段。`plan_provider_queries` 改为返回版本化领域模型，不接收单一 Provider 的能力后直接丢弃不支持条件。

生成至少覆盖：

- 每个核心目标概念；
- 本地语言/同义词（有证据时）；
- 用户指定商家类型；
- 复合目标的 any/all 语义；
- 明确的包含与排除词；
- 生成原因与覆盖的 intent concept IDs。

AI 不可用或关闭时，本地确定性规则必须生成最小有效切片。不得伪造 AI 成功状态。

### 8.2 规范化

哈希前执行：

- Unicode 规范化；
- trim/casefold；
- 国家映射到系统标准代码；
- 数组排序和去重；
- 空值移除；
- 保留 `any/all`、purpose 和业务类型语义；
- 哈希输入使用稳定 JSON 序列化。

重复切片在草稿阶段提示并阻止锁定；系统可建议合并，但不静默丢弃用户输入。

### 8.3 数量默认值

- `target_result_count` 默认沿用当前用户输入的 `brand_limit`；
- `candidate_fetch_limit` 默认由版本化策略计算，例如目标数的安全倍数，但必须受系统上限约束；
- 默认 `max_provider_calls` 由切片数和当前 Provider 分页能力计算；
- UI 显示这些是上限/估计，不显示为保证值。

## 9. 来源能力与接口切换

### 9.1 标准契约

```python
class DiscoverySourceAdapter(Protocol):
    def capabilities(self) -> SourceCapabilities: ...
    def plan(self, query_slice, context) -> SourceRequestPlan: ...
    def execute(self, request_plan, cursor) -> SourcePage: ...
    def normalize(self, response) -> list[SourceCandidate]: ...
    def check_availability(self, context) -> AvailabilityResult: ...
```

`SourceCapabilities` 至少声明：

- 支持的 operation；
- 国家、行业、关键词、公司类型 include/exclude 能力；
- 每种筛选的语义：exact/recall/unsupported；
- country_semantics；
- 分页方式、页大小和最大深度；
- 速率、并发和额度检查能力；
- 结果是否可保存及来源证据要求。

### 9.2 本期桥接

现有 Provider Waterfall 包装成 `ConfiguredProviderDiscoveryAdapter`。切片先转换为现有 payload，再调用 `execute_provider_waterfall`。Provider 不支持的条件必须：

1. 记录在 `unsupported_filters`；
2. 在 UI 以业务语言提示将进行后置筛选；
3. 保留原始用户筛选，不能静默删除；
4. 若关键条件不可后置验证，则该来源不可用于该切片。

### 9.3 切换策略

切换原因标准化：

- unavailable；
- quota_exhausted；
- rate_limited；
- unsupported_filters；
- no_results；
- no_new_candidates；
- slice_exhausted；
- low_yield；
- low_quality；
- budget_guard。

本期默认自动切换仅使用任务创建时锁定的 `TaskVendorPlan`。`low_quality` 必须达到版本化最小样本量后才能触发。路由策略可以动态选择锁定列表中的下一来源，但不得引入任务快照之外的新 Vendor。

每个来源拥有独立游标；切换不得覆盖前一来源进度。

## 10. 调度、并发与过载保护

### 10.1 最小工作单元

最小 Job 是“一个切片在一个来源上的一页”，禁止将整个任务或所有切片放在单个长事务/长 Job 中。

每页执行：

```text
领取 SliceRun lease
-> 检查任务状态/取消/预算/重试/rollout
-> 获取系统、租户、Provider、凭证和任务容量槽
-> 调用 Adapter
-> 持久化规范化结果和 vendor_request_id
-> 候选去重、命中记录与事件同事务写入
-> 释放容量槽
-> 调度器重新计算停止或下一步
```

### 10.2 公平策略

按组织轮转，再按组织内任务轮转。用户交互任务优先于自动刷新，但所有优先级都受 Provider 和预算限制。一个任务每轮最多领取一个发现页，避免大任务占满队列。

### 10.3 配置项

加入治理配置而非环境散落常量：

- system_max_active_tasks；
- tenant_max_active_tasks；
- tenant_max_queued_tasks；
- task_max_active_slices（第一版默认 1）；
- provider_max_concurrency；
- credential_max_concurrency；
- website_fetch_max_concurrency；
- ai_max_concurrency；
- queue_backlog_limit；
- slice_max_count（默认不超过 20）；
- slice_empty_page_threshold；
- lease_duration、heartbeat_interval、retry_backoff。

配置加入任务快照的部分：切片数、预算、停止和来源路由。纯运行容量不冻结，但变化不能改变业务语义。

### 10.4 恢复

扩展现有 `recover_durable_work`：

- 恢复 queued SliceRun；
- 将过期 leased/running 转为 retryable；
- 清理过期 capacity lease；
- 重新计算父任务聚合状态；
- 重复发送 Celery 唤醒消息必须安全；
- completed 幂等键直接返回已存结果，不重复外部调用。

Vendor 不支持原生幂等时，继续遵守现有“远端提交后、本地落盘前崩溃无法证明 exactly-once”的边界，并保存 vendor_request_id 与完整审计。

## 11. 停止、重复执行与筛选

### 11.1 停止条件

每页后按顺序检查：

1. 用户取消或暂停；
2. 合格数量达到 `target_result_count`；
3. 候选处理量达到 `candidate_fetch_limit`；
4. 调用次数达到 `max_provider_calls`；
5. 成本达到预算；
6. 所有启用切片的允许来源耗尽或终止；
7. 不存在可恢复的失败。

停止时写明确 `stop_reason`。不足目标不得自动修改筛选。

### 11.2 重复模式

- `new_only`：默认。继续未耗尽游标并排除历史候选；
- `refresh_stale`：发现新增，同时刷新超过策略年龄的旧候选；
- `re_evaluate`：不调用发现来源，复用历史候选/证据并新增评分历史。

修改切片产生新计划版本。未修改切片可根据 normalized_hash 继承安全游标；语义变化的切片必须从新游标空间开始。是否继承必须由确定性兼容函数判断并记录审计。

### 11.3 漏斗

统一统计：

```text
raw_provider_results
-> normalized_candidates
-> cross_slice_duplicates
-> historical_duplicates
-> hard_filtered
-> evidence_evaluated
-> qualified
-> review
-> rejected
```

不得把“Provider 返回数”称为“找到的目标商家数”。

## 12. API 设计

所有 API 继续使用现有认证、权限和组织隔离。建议新增：

```text
POST   /api/v1/search-task-plans/preview
POST   /api/v1/search-tasks/{task_id}/query-plans
GET    /api/v1/search-tasks/{task_id}/query-plans
GET    /api/v1/search-tasks/{task_id}/query-plans/{version}
PATCH  /api/v1/search-tasks/{task_id}/query-plans/{version}
POST   /api/v1/search-tasks/{task_id}/query-plans/{version}/slices
PATCH  /api/v1/search-tasks/{task_id}/query-plans/{version}/slices/{slice_id}
DELETE /api/v1/search-tasks/{task_id}/query-plans/{version}/slices/{slice_id}
POST   /api/v1/search-tasks/{task_id}/query-plans/{version}/lock
GET    /api/v1/search-tasks/{task_id}/query-slice-runs
POST   /api/v1/search-tasks/{task_id}/continue-new
```

`preview` 不创建任务，只返回 intent、默认数量、切片、警告和是否需要确认。最终创建任务时服务端必须重新校验并保存相同语义，不能信任前端回传的隐藏字段。

锁定 API 支持 `If-Match`/revision 或显式 `updated_at` 乐观锁，防止两个浏览器覆盖切片草稿。

API 与 SSE 仅做 additive 变更；未知 purpose/status 在旧前端安全显示。

## 13. 事件与实时体验

新增版本化事件：

- query_plan.generated；
- query_plan.locked；
- task.waiting；
- task.admitted；
- slice.queued/started/progress/waiting/completed/exhausted/failed；
- candidate.available；
- task.partial/completed/failed/cancelled。

事件至少包含 event_id、schema_version、task_id、plan_version、slice_id、slice_run_id、trace_id 和安全的计数变化。不得包含 Credential、Authorization、API Key 或不必要的联系人 PII。

前端启用现有认证 SSE：

- 按最后事件 ID 重连补读；
- 按 event_id 去重；
- 断线自动退避；
- SSE 不可用时回退数据库轮询；
- 任务详情初次加载始终以数据库快照为准，再接增量事件。

## 14. 安全与隐私

- 所有计划、切片、运行记录按 organization_id/任务所有权隔离；
- 输入限制：目标描述 2,000 字、切片数、词条数、单词长度和 JSON 深度均设上限；
- 将来 URL 型来源必须使用现有 SSRF 保护；本期切片不允许用户直接写 Vendor URL；
- Query Plan 快照不保存 API Key，只引用受治理 Credential ID；
- 原始 Provider 响应按必要性保存并递归脱敏；
- 审计用户修改、锁定、暂停、恢复、取消、手动重试和切换确认；
- 不把公开页面中的个人联系信息自动提升为可营销联系人；
- 错误消息对用户可操作，对日志保留 trace 标识但不泄密。

## 15. 迁移与兼容

### 15.1 迁移顺序

1. 新增 Query Plan/Slice/SliceRun/Capacity Lease 和来源命中表或扩展；
2. 给 `search_task` 添加 nullable/默认字段；
3. 部署只读兼容代码；
4. 为新任务在 shadow 模式生成计划但仍走旧路径；
5. 验证计划与旧结果差异；
6. review 模式允许用户查看切片，执行仍由功能开关控制；
7. active 百分比切换到切片执行；
8. 稳定窗口后再讨论 `brand_limit` contract，当前版本不删除。

不需要为全部历史任务回填完整切片。旧任务无 active_query_plan_id 时走 legacy 路径；用户选择“继续获取新增”时才按旧 filters 生成一个兼容计划并明确版本。

### 15.2 功能开关

```text
query_slicing_rollout_mode = disabled|shadow|review|active
query_slicing_rollout_percentage = 0..100
query_slicing_sse_enabled
query_slicing_scheduler_enabled
```

任务创建时冻结 rollout 决策。运行中配置变化不切换执行模式。

## 16. 分阶段实施清单

### Phase A：领域与迁移

目标：正式保存、编辑和锁定查询计划，不改变生产搜索结果。

主要文件：

- `backend/app/modules/models.py`；
- `backend/app/modules/schemas.py` 或新增 query_planning schemas；
- 新 Alembic migration；
- `backend/app/query_planning/*`；
- `backend/tests/test_query_plan_models.py`；
- `backend/tests/test_query_plan_api.py`。

验收：

- 生成结果可保存；
- 重复切片被拒绝；
- 锁定后不可修改；
- 并发编辑触发冲突而非覆盖；
- 权限和租户隔离有效；
- 旧任务可读可运行。

### Phase B：低操作量 UI

目标：单页快速创建，可选切片抽屉。

主要文件：

- `frontend/src/views/TasksView.vue`，必要时拆出 `QueryPlanDrawer.vue`、`TaskProgressCard.vue`；
- `frontend/src/api/client.ts`；
- `frontend/src/api/compat.ts`；
- 相关前端测试。

验收：

- 普通用户三次操作内启动；
- AI 不可用时仍可创建；
- 用户能新增/编辑/禁用/删除草稿切片；
- 高级设置默认折叠；
- 桌面与窄屏、键盘、焦点、非颜色状态均通过检查。

### Phase C：切片执行闭环

目标：锁定计划实际驱动现有 Provider 搜索。

主要文件：

- `backend/app/modules/services.py`（仅编排入口，避免继续扩大）；
- `backend/app/query_planning/scheduler.py`；
- `backend/app/providers/discovery.py`；
- `backend/app/tasks/celery_app.py`；
- `backend/app/pipeline/definition.py` 新增不可变版本或新 stage 版本；
- Provider、Pipeline 和幂等测试。

验收：

- 每个切片每页最多一次成功外部调用；
- 跨切片正确去重并保留命中来源；
- 达量立即停止；
- 单切片失败不丢失其他结果；
- 暂停、取消、重试和 Worker 重启安全；
- 旧路径仍通过回归测试。

### Phase D：公平调度与实时任务中心

目标：多任务并发时系统稳定且用户感知明确。

主要文件：

- Capacity Lease 模型/迁移；
- scheduler/recovery；
- SSE 事件和前端任务中心；
- Docker/Celery 配置文档，不盲目提高 concurrency。

验收：

- 大任务不阻塞小任务；
- 多组织轮转公平；
- 同一 Credential 不超配；
- API 在后台繁忙时仍可查看、暂停和取消；
- SSE 断线重连不丢事件、不重复计数；
- 队列达到保护上限时给出明确提示。

### Phase E：统计、重复执行与 rollout

目标：新增模式和生产渐进发布。

验收：

- `new_only` 延续安全游标；
- 修改语义的切片不错误继承游标；
- 漏斗与任务汇总一致；
- shadow/review/active 可回滚；
- 运维可识别停滞、积压、429、Lease 过期和成本异常。

## 17. 验证方案

### 17.1 重点测试

- Query Plan/Slice 状态转换；
- 规范化哈希稳定性和冲突；
- 锁定事务和乐观并发；
- 任务配置快照不可变；
- SliceRun 幂等与重复投递；
- Provider 失败、429、额度耗尽和 fallback；
- 达量、预算、候选上限和全部耗尽；
- 跨切片/跨 Provider 去重与来源保留；
- 暂停、取消、恢复和 Lease 过期；
- 多组织公平性和容量槽不超卖；
- 权限、租户隔离、输入上限和脱敏；
- SSE 断线、补读、重复事件和轮询降级；
- 老任务、旧客户端和 in-flight 任务兼容。

### 17.2 必须执行的命令

按仓库开发规则，在相应阶段至少执行：

```text
cd backend
python -m pytest -q
python -m ruff check app tests migrations
python -m compileall -q app migrations

cd ../frontend
pnpm exec vue-tsc --noEmit
pnpm run build

cd ../backend
alembic upgrade head
alembic downgrade <previous_revision>
alembic upgrade head
```

还必须验证：

- 空数据库初始化；
- 生产数据库克隆升级；
- 老任务读取和执行；
- `/health`、`/ready`；
- Worker、Beat、Outbox 和恢复日志；
- 桌面和窄屏关键路径；
- 并发/故障注入测试，而不是只跑 happy path。

### 17.3 并发场景

测试规模根据 staging 容量配置，不在设计文档虚构生产数字。至少覆盖：

1. 一个 20 切片任务与多个 1 切片任务同时运行；
2. 多组织同时提交；
3. 同一 Credential 被多个任务共享；
4. Worker 在远端调用前、调用中、结果落盘后分别终止；
5. Redis 重启；
6. PostgreSQL 短暂不可用；
7. Provider 连续 429/5xx；
8. SSE 大量重连；
9. 用户运行中暂停和取消；
10. 队列达到配置上限。

## 18. 可观测性与发布门槛

新增指标：

- task admission wait duration；
- time to first candidate / first qualified candidate；
- active/queued tasks by tenant；
- slice queue/run duration；
- slice raw/new/duplicate/qualified rates；
- provider concurrency、429、fallback 和 quota；
- expired leases、recovery count、duplicate delivery；
- queue oldest age、Outbox backlog；
- database pool saturation、API latency；
- SSE connections/reconnects/fallback polls；
- stop reason distribution；
- candidate cost and qualified-candidate cost。

发布中止阈值必须在 staging 基线后配置，至少包括：

- 任务无进展持续超阈值；
- Lease 过期或重复调用异常增长；
- 429、Provider 成本或 fallback 异常；
- Outbox/队列持续积压；
- 数据库连接池接近耗尽；
- 用户 API 延迟显著恶化；
- 新旧路径结果出现不可解释的数据完整性差异。

## 19. 发布与回滚

### 19.1 发布

1. 备份 PostgreSQL；
2. 部署 additive migration；
3. 部署后端兼容读写，保持功能 `disabled`；
4. 重建 Worker 和 Beat；
5. 开启 `shadow`，仅生成和比较计划；
6. 检查切片覆盖、重复、成本估计和老路径一致性；
7. 开启 `review`，允许用户查看但不切换全部执行；
8. 小比例 `active`；
9. 逐步放量并观察上述指标；
10. 稳定后默认 active，保留 legacy fallback 兼容窗口。

### 19.2 回滚

- 首选关闭 rollout，后续新任务走 legacy filters；
- 已锁定且正在运行的切片任务可选择安全完成或暂停，不在运行中切换语义；
- 回滚应用镜像不删除 Query Plan/Slice 历史；
- 若旧应用无法容忍 additive 表/字段，再按演练过的上一 revision downgrade；
- 不删除候选、来源命中、成本、事件和审计记录；
- 不生成 fallback 分数或伪造完成状态。

## 20. 后续新增免费数据源的接入方式

完成本方案后，Wikidata、政府注册库、展会目录、协会目录、OSM 和 Common Crawl 仅需：

1. 实现 `DiscoverySourceAdapter`；
2. 声明能力、语义、许可、分页、速率和存储规则；
3. 增加 Credential/配置入口（无需 Credential 的来源也必须可治理启停）；
4. 实现连接/可用性测试；
5. 接入 Source Router 的 disabled/shadow/review/active rollout；
6. 输出统一 `SourceCandidate`；
7. 通过既有切片、调度、去重、来源命中和统计路径。

不得为任何新来源修改核心 Query Slice 语义或在业务服务中写死 URL、请求映射和错误处理。

## 21. 完成定义

本功能只有在以下条件全部满足后才能称为生产可用：

- 普通用户可在三次操作内启动任务；
- 用户可查看和补充切片，但不是强制步骤；
- 锁定切片真实驱动 Provider 搜索；
- 目标数量、候选上限、预算和筛选停止条件正确；
- 重复执行不会默认重抓同一页；
- 多任务公平、可限流、可恢复，不拖垮交互 API；
- 部分结果、排队原因和失败恢复对用户清晰；
- 幂等、取消、权限、迁移和兼容测试通过；
- 后端、静态检查、编译、前端类型检查、构建和迁移演练通过；
- UI 完成桌面与窄屏视觉验证；
- rollout、告警、回滚和运维恢复步骤完成演练。

## 22. 强制设计门检查结果

- 产品：通过。默认路径只保留目标、数量和开始，切片为可选高级能力。
- 架构：通过。计划、策略、调度、Adapter、持久化和 UI 边界明确。
- 数据：通过设计门。采用 additive migration、历史保留、来源追踪和兼容路径。
- 可靠性：通过设计门。小步 Job、幂等、Lease、恢复、预算、取消和公平调度齐全。
- 演进：通过。Vendor 中立切片和标准 Adapter 支持后续免费来源。
- 运维：通过设计门。配置、容量、指标、告警、rollout 和回滚已定义。
- 安全：通过设计门。租户隔离、输入上限、SSRF 边界、脱敏和审计已定义。
- 体验：通过设计门。渐进披露、部分结果、真实状态、SSE 降级和可访问性已定义。
- 验证：待实施证明。本文是可执行方案，不代表代码已经完成或生产验证已经通过。
