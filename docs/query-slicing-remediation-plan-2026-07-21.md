# BuyerReach 查询切片修复实施计划

状态：待执行修复基线
日期：2026-07-21
适用范围：Query Slicing Phase A-E 当前实现

权威依据：

- `docs/DEVELOPMENT_RULES.md`
- `docs/query-slicing-production-implementation-plan.md`
- `docs/query-slicing-production-implementation-prompt.md`
- `docs/query-slicing-implementation-status.md`
- `docs/query-slicing-implementation-decisions.md`

本文记录 2026-07-21 完成度审计发现的缺陷、修复顺序和验收门槛。本文不是第二套架构；若与上述权威文件冲突，以权威文件为准，并将确需偏离的决定写入 `docs/query-slicing-implementation-decisions.md`。

## 1. 修复目标

恢复以下真实用户闭环：

```text
输入目标与合格商家数量
-> 服务端生成并验证 Query Plan
-> 用户可选调整切片
-> 同一事务锁定 Plan、配置快照和 TaskVendorPlan
-> 按切片 × 指定来源 × 单页执行
-> 持久化候选、来源命中、成本、游标和事件
-> 根据目标量、预算、调用上限、暂停和来源耗尽停止
-> 用户实时看到可信进度、部分结果和恢复动作
```

修复后必须保证：

1. 任意组织不能读取或操作其他组织的任务、计划、切片、运行记录或事件；
2. 外部调用只使用任务创建/锁定时冻结的 Vendor Plan、Credential 引用和业务配置；
3. 每个“切片 × 来源 × 页”最多产生一次成功的计费调用；
4. PostgreSQL 是计划、Run、游标、候选、来源命中、成本和事件的最终真实来源；
5. 任务不足目标时不得自动降低国家、品类、商家类型或相关性要求；
6. 旧任务没有 Query Plan 时继续安全使用 legacy 路径；
7. 在迁移、并发、故障恢复和视觉检查完成前，不得声明生产可用。

## 2. 当前完成度纠正

| Phase | 原状态 | 审计状态 | 主要原因 |
|---|---|---|---|
| A | complete | partial | 租户隔离、锁定事务、版本生命周期、并发约束和迁移验证不完整 |
| B | complete | partial | 快速创建未生成/锁定计划，缺编辑、冲突处理和视觉验收 |
| C | complete | blocked | 主执行链必然失败，Provider 路由、分页、幂等、预算和恢复不成立 |
| D | partial | partial | 只有容量表结构，缺公平调度、恢复、SSE 和任务中心 |
| E | partial | partial | 缺游标继承、漏斗聚合、rollout、指标、告警和文档闭环 |

开始修复时应先把 `docs/query-slicing-implementation-status.md` 更新为事实状态，并将 Next Exact Action 指向本文第 4 节 R0。

## 3. 发布阻断项

### P0-SEC-01：跨组织读取

受影响路径至少包括：

- `GET /search-tasks/{task_id}`；
- `GET /search-tasks/{task_id}/checkpoints`；
- `GET /search-tasks/{task_id}/events`；
- `GET /search-tasks/{task_id}/query-slice-runs`。

修复要求：

- 使用一个统一任务授权查询函数，通过 `AuthorizationContext`、`organization_id` 和数据范围加载任务；
- 不得只用 `db.get(SearchTask, task_id)` 判断存在；
- 不得仅依赖 UUID 难以猜测；
- 不存在和无权访问统一返回 404，避免对象枚举；
- SSE 建立连接和补读时均重新检查权限；
- 增加两个组织、同权限用户的列表、详情、运行记录、checkpoint 和 SSE 隔离测试。

验收：其他组织的任务 UUID 在全部路径返回 404，且没有事件、数量或存在性泄露。

### P0-RUN-01：切片路径必然进入 Provider 不可用分支

当前切片路径将 `company_provider` 设置为 `None`，之后公共分支把它视为全部 Provider 失败。

修复要求：

- 不再让切片路径伪装成 legacy waterfall 返回值；
- 将切片执行结果定义为明确的领域结果，例如 `SliceExecutionSummary`；
- 切片路径与 legacy 路径在 Provider 执行后汇合到候选 Pipeline，而不是共享不兼容的 `company_provider is None` 判断；
- 删除或重构任何为绕过异常而伪造 ProviderConfig 的方案。

验收：锁定计划的任务可以完成至少一个切片页并进入候选验证/评分阶段；Provider 全失败时才进入可恢复或部分失败状态。

### P0-RUN-02：Provider 路由和计费记录不可信

当前 Adapter 接收指定 Provider 后重新运行完整 waterfall，未传任务，且使用独立未提交 Session。

修复要求：

- 给现有 Provider 执行层增加“执行已冻结 Vendor Plan 中一个明确 Provider”的受控入口；
- Adapter 只能调用指定 Provider，不能重新选择当前系统首选 Provider；
- 传入任务、配置快照、Credential 引用、StageRun 和幂等键；
- 外部成功结果、ApiUsage、成本、checkpoint、fallback 决定和 SliceRun 必须按现有事务/Outbox 规则持久化；
- 禁止 Adapter 自行创建独立 Session；Session 和事务由编排层拥有；
- SliceRun.provider、vendor_request_id 和实际调用 Vendor 必须一致；
- 不支持原生幂等的 Vendor 明确保留远端成功、本地落盘前崩溃的边界，不宣称 exactly-once。

验收：指定 Apollo 时不会调用 Hunter；成功和失败调用的 Provider、成本、Usage、checkpoint 可核对且重启后仍存在。

### P0-RUN-03：Lease 类型和恢复链错误

修复要求：

- `lease_expires_at` 写入 `datetime.now(UTC) + timedelta(seconds=lease_duration)`；
- Lease 领取使用数据库条件更新或带锁查询，禁止无条件覆盖；
- 为 queued、leased、running、retryable、completed、exhausted、failed、cancelled 定义合法恢复路径；
- 重复投递遇到 completed/exhausted 返回持久结果；
- 遇到有效 leased/running 返回“已由其他 Worker 处理”，不得重复调用；
- 过期 leased/running 由恢复扫描转 retryable，再由受控领取转 leased；
- 唯一约束冲突时回读已有 Run，而不是向用户暴露 IntegrityError。

验收：两个 Worker 同时领取只产生一次外部调用；Worker 在调用前、调用中、落盘后退出均有确定结果。

## 4. 强制修复顺序

### R0：安全止血和状态纠正

目标：在执行闭环修复完成前阻止不安全放量。

任务：

1. 将 Query Slicing rollout 保持 `disabled`，若配置入口尚不存在则确保新任务不会进入不完整 active 路径；
2. 修复 P0-SEC-01；
3. 更新实施状态，禁止保留 Phase A/B/C complete 的错误声明；
4. 为本文发现但不属于原设计的实现偏差补充 Decision 记录；
5. 增加最小安全回归测试。

退出门：跨租户测试通过，active 路径默认关闭，状态文档与仓库事实一致。

### R1：完成 Phase A 数据、权限和锁定语义

#### R1.1 统一授权边界

- 所有 Plan、Slice、SliceRun API 先通过任务授权加载器；
- 组织字段由任务继承，不能信任客户端；
- 查询同时约束 task_id、organization_id 和父对象关系；
- SSE、导出和事件补读遵循相同规则。

#### R1.2 锁定事务

锁定 Plan 必须在一个数据库事务内：

1. 原子检查 revision/updated_at；
2. 校验状态、至少一个启用切片、重复 hash 和输入上限；
3. 冻结 Query Plan 版本；
4. 保存无密钥 `configuration_snapshot` 和版本；
5. 创建或锁定 `TaskVendorPlan`；
6. 冻结 rollout 决策、预算、筛选和 Provider 路由；
7. 更新 `active_query_plan_id`；
8. 写 `query_plan.locked` Outbox 事件；
9. 提交后再唤醒 Worker。

任何一步失败必须整体回滚。

#### R1.3 版本和并发

- 新草稿版本使用 `max(version) + 1`，不得固定为 1；
- 每个任务最多一个 active/locked Plan，使用数据库约束或原子 supersede 事务保证；
- 不再把时间戳截断到秒；优先使用明确整数 revision；
- Plan 和 Slice 的更新、禁用、删除、排序都带 revision；
- 冲突返回结构化 409，包含安全的当前 revision 和恢复提示；
- 更新 Slice hash 后再次执行重复检测，并处理并发唯一约束冲突；
- 状态字段变化经过统一 transition 函数，避免 status/enabled 双重真相。

#### R1.4 Phase A 测试

新增或补全：

- `tests/test_query_plan_api.py`；
- `tests/test_query_plan_service.py`；
- PostgreSQL 约束和锁定事务集成测试；
- 两浏览器并发编辑/锁定；
- 重复切片并发创建；
- 跨租户 API/SSE；
- 新版本创建和旧任务 legacy 回归；
- AI 失败/local fallback；
- 锁定事务任一步失败的整体回滚。

退出门：Phase A 的模型、API、权限、迁移、并发和旧任务验收全部有行为测试，不以模型字段测试代替。

### R2：重建 Phase C 单页执行闭环

#### R2.1 单页执行契约

最小执行单元保持：

```text
task_id + plan_version + slice_id + provider + operation + adapter_version + input_hash + cursor_key
```

流程：

1. 加载并验证锁定任务快照；
2. 检查取消、暂停、预算、调用上限、候选上限、rollout 和授权；
3. 原子领取 SliceRun 和容量 Lease；
4. 由指定 Provider Adapter 执行一页；
5. 在同一受控事务中写标准结果、候选、全部来源命中、成本、Usage、checkpoint 和 Outbox；
6. 保存 `next_cursor` 或 exhausted；
7. 提交后调度下一个公平执行单元；
8. 每页后重新检查停止条件。

#### R2.2 分页、去重和来源命中

- 不得固定 `page=1`；
- 每个 Provider/operation 使用独立游标；
- 同一候选跨切片、页和 Provider 只保留一个候选实体；
- 每次来源命中都必须保存，不能因已有 task/candidate hit 而丢失第二个切片或 Provider 的来源证据；
- 区分本页重复、跨切片重复和历史重复；
- `all_candidates` 等进程内列表不得充当任务汇总真相；
- 后续筛选使用锁定 Plan/Policy，不重新让 legacy `task.filters` 和 `brand_limit` 改变新路径语义。

#### R2.3 停止与失败

每页后按权威计划顺序检查：

1. paused/cancelled；
2. qualified target reached；
3. candidate fetch limit；
4. provider call limit；
5. budget exhausted；
6. all sources exhausted；
7. no retryable work。

单切片失败不得删除其他切片结果。429/临时 5xx 使用有上限退避；认证失败、配置错误和额度耗尽不得盲目重试。停止必须写版本化 `stop_reason`。

#### R2.4 Phase C 测试

必须覆盖：

- `execute_slice_page` 真实数据库测试；
- 指定 Provider 不发生重新路由；
- 每页最多一次成功外部调用；
- 同时投递和唯一约束冲突；
- completed 复用、有效 lease 拒绝、过期 lease 恢复；
- 多页游标推进和 exhausted；
- 达量、候选上限、调用上限和预算；
- 跨来源去重但保留全部命中；
- 429、5xx、额度不足和冻结 fallback；
- 暂停、取消、恢复及三个 Worker 崩溃时点；
- ApiUsage、成本、checkpoint 和 Outbox 持久化；
- legacy 任务回归。

退出门：纵向测试证明锁定切片真实驱动指定 Provider 并进入既有 Pipeline V2；不得只用 mock 验证函数被调用。

### R3：完成 Phase B 主用户旅程

#### R3.1 三步内启动

推荐默认旅程：

1. 用户输入目标和合格商家数量；
2. 前端调用 preview，显示系统理解与必要警告；
3. 用户点击“开始搜索”，服务端在一个用例中重新验证、创建任务、保存并锁定计划、排队执行。

不得信任 preview 返回的隐藏字段。最终创建时服务端基于用户输入和受治理配置重新生成/校验业务语义。

#### R3.2 Query Plan Drawer

补齐：

- 编辑、复制、新增、禁用、删除、优先级；
- Plan 目标量、高级限制和 repeat mode；
- revision 冲突后的刷新、差异提示和重新应用；
- 锁定后的“修改条件”创建新版本；
- “锁定”与“锁定并启动”文案、行为保持一致；
- 删除确认和成本/影响说明；
- loading、empty、slow、partial、error、permission 和 offline 状态；
- API 错误使用用户可理解的中文，不直接显示内部异常。

#### R3.3 响应式和可访问性

- Drawer/Dialog 使用视口宽度上限，验证 1440px、1024px、390px；
- 按钮组在窄屏换行；
- 图标按钮提供 `aria-label`；
- 键盘可完成新增、编辑、删除确认和锁定；
- 焦点可见，Dialog 关闭后焦点返回触发控件；
- 状态不只用透明度或颜色表达。

退出门：普通用户最多三次操作启动真实切片任务；桌面与窄屏均完成截图/人工检查，不能用 build 代替视觉验收。

### R4：完成 Phase D 公平调度和实时体验

#### R4.1 公平调度

- PostgreSQL Capacity Lease 实现真实领取、心跳、释放和过期回收；
- 按组织轮转，再按任务轮转，每任务每轮最多一个切片页；
- 交互任务优先于后台刷新；
- system、tenant、task、Provider、Credential、website fetch 和 AI 容量分别治理；
- 队列上限返回可操作错误，不无限创建 Run；
- 配置进入现有系统配置入口，业务限制冻结，纯容量允许动态调整。

#### R4.2 恢复扫描

扩展 `recover_durable_work`：

- queued SliceRun；
- 过期 leased/running；
- retryable 且到期；
- 过期 Capacity Lease；
- 父任务进度重新聚合；
- 重复 Celery 唤醒安全。

#### R4.3 SSE 和任务中心

- 使用现有认证机制实现可携带 Authorization 的 fetch stream，或设计受控短期票据/同站 Cookie；
- 支持 Last Event ID、event_id 去重、指数退避和轮询降级；
- 初次加载使用数据库快照；
- 展示 queued/running/needs_action/completed、真实阶段、切片进度、候选漏斗、queue_reason、stop_reason 和部分结果；
- 页面断开、离线和权限撤销时显示明确状态；
- 新事件不得在用户选择期间自动打乱列表。

退出门：一个大任务不阻塞多个小任务，多组织公平，同一 Credential 不超配；Redis/Worker 重启后从数据库恢复；SSE 重连不丢失或重复计数。

### R5：完成 Phase E 重复执行、统计和发布治理

任务：

- `new_only` 继承语义兼容切片的安全游标并排除历史候选；
- `refresh_stale` 分开统计新增发现和旧数据刷新；
- `re_evaluate` 不调用发现 Provider，只新增评分历史；
- 确定性兼容函数决定游标继承并写审计；
- 聚合 raw、normalized、cross-slice duplicate、historical duplicate、hard filtered、evaluated、qualified、review、rejected；
- SliceRun 汇总与任务总计可核对；
- 实现 disabled/shadow/review/active 和稳定百分比 rollout；
- 任务创建时冻结 rollout 决定；
- 增加指标、告警、运行手册、用户手册和部署/回滚文档。

退出门：重复执行、语义变化、漏斗、shadow 不影响正式结果、百分比稳定和 legacy 回滚全部通过。

## 5. 数据库迁移与兼容

优先判断现有 `20260721_0029` 是否尚未进入任何共享环境：

- 若从未部署，可在保持 revision 身份和团队约定允许的前提下修正明显建表缺陷；
- 若已进入共享或生产环境，不得重写历史迁移，新增 additive 修复 revision；
- 是否重写必须记录到 Decisions 文件。

迁移至少补足：

- 每任务 active/locked Plan 的数据库级一致性；
- revision/乐观并发字段；
- Lease、状态、slots、计数和预算的 CheckConstraint；
- 必需组织字段及索引；
- 来源命中能够保存多切片、多 Provider、多页证据的唯一键；
- 不删除 `brand_limit`、历史候选、评分、事件、成本或审计。

必须在隔离 PostgreSQL 执行：

```text
alembic upgrade head
alembic downgrade <previous_revision>
alembic upgrade head
```

还需验证：空数据库、现有数据库克隆、旧数据读取、旧任务 legacy 执行、降一版后旧应用可启动。当前环境无法解析 `postgres` 主机，不能把 migration 标记为已通过。

## 6. 文件边界建议

- `app/query_planning/service.py`：Plan/Slice 用例、锁定事务和版本管理；
- `app/query_planning/scheduler.py`：准入、领取、下一执行单元和恢复，不承载 Vendor 协议；
- `app/providers/discovery.py`：Vendor 中立契约与指定 Provider Adapter；
- `app/modules/services.py`：保留既有 Pipeline 编排入口，逐步移除 Query Slicing 内联巨型分支；
- `app/tasks/celery_app.py`：固定唤醒、恢复和 Outbox 发布；
- `app/api/v1/router.py`：鉴权、输入输出和事务入口，不实现调度策略；
- `frontend/src/views/TasksView.vue`：主旅程和任务中心；
- `frontend/src/components/QueryPlanDrawer.vue`：计划编辑体验；
- 新前端 composable/store：SSE、去重、重连和轮询降级；
- 测试按 service、API、scheduler、Provider bridge、migration、UI/E2E 分层。

禁止继续把公平调度、分页循环或 Provider 协议堆入 `modules/services.py`。

## 7. 强制验证矩阵

### 每个修复批次

```text
cd backend
python -m pytest <targeted tests> -q
python -m ruff check app tests migrations
python -m compileall -q app migrations
```

涉及前端时：

```text
cd frontend
pnpm exec vue-tsc --noEmit
pnpm run build
```

### 全部修复完成

```text
cd backend
python -m pytest -q
python -m ruff check app tests migrations
python -m compileall -q app migrations

cd ../frontend
pnpm exec vue-tsc --noEmit
pnpm run build
```

另外必须实际执行并保存证据：

- PostgreSQL upgrade/downgrade/upgrade；
- `/health`、`/ready`；
- Worker、Beat、Outbox 和 recovery 日志；
- 20 切片大任务与多个 1 切片任务；
- 多组织和共享 Credential；
- Worker 三个崩溃时点；
- Redis 重启和 PostgreSQL 短暂不可用；
- Provider 429/5xx/额度不足；
- SSE 重连、重复事件和轮询降级；
- 队列达到上限；
- 桌面和 390px 窄屏视觉检查；
- 密钥、Authorization、Token、密码、调试输出和硬编码 Vendor URL 扫描。

现有 `219 passed` 只能作为回归基线，不能替代上述 Query Slicing 行为验证。

## 8. 发布与回滚

### 发布顺序

1. 备份 PostgreSQL；
2. 部署 additive migration；
3. 部署兼容后端，保持 `disabled`；
4. 重建 API、Worker 和 Beat；
5. 验证健康、迁移 revision、Outbox、recovery 和成本记录；
6. 开启 shadow，只生成/比较计划；
7. 开启 review，只允许查看和明确锁定；
8. 小比例 active；
9. 观察任务停滞、重复调用、429、成本、Lease 过期、Outbox 积压、API 延迟和新旧结果差异；
10. 稳定后逐步放量。

### 中止信号

- 跨组织访问成功；
- 同一幂等键出现多个成功外部调用；
- Provider/Usage/成本无法核对；
- SliceRun 长时间无进展或 Lease 过期异常增长；
- Outbox/队列持续积压；
- 429、fallback 或成本异常；
- 数据库池或交互 API 延迟显著恶化；
- 新旧路径产生无法解释的数据完整性差异。

### 回滚

- 首选把 rollout 切回 disabled/review；
- 已运行任务安全暂停，不在运行中切换业务语义；
- 新任务回到 legacy 路径；
- 回滚应用镜像，不删除 Query Plan、SliceRun、来源命中、候选、成本、事件和审计；
- 只有旧应用不能容忍 additive schema 时，才执行已经演练过的 downgrade。

## 9. 完成定义

只有以下条件全部满足，才可把 Query Slicing 标为 complete：

- P0/P1 缺陷全部关闭且有回归测试；
- Phase A-E 各自验收通过；
- 快速创建真实生成、锁定并执行 Query Plan；
- 指定 Provider、分页、幂等、预算、停止和恢复闭环成立；
- 租户隔离覆盖 API、SSE、运行记录和事件；
- 公平调度、容量治理和 Redis/Worker 恢复通过；
- 重复执行和 rollout 可审计、可回滚；
- 后端、静态检查、编译、前端类型和构建通过；
- PostgreSQL 迁移演练通过；
- 桌面/窄屏、并发、故障和 SSE 验证完成；
- 状态文件、Decisions、用户手册、部署和恢复文档与事实一致。

## 10. Next Exact Action

先执行 R0：保持 Query Slicing active 路径关闭，修复所有任务详情、checkpoint、SSE 和 SliceRun API 的统一组织授权，并新增两个组织之间的 404 隔离测试；验证通过后更新 `docs/query-slicing-implementation-status.md`，再进入 R1。
