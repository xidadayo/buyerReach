# BuyerReach 自动客户开发与定时任务架构 V1

状态：后续版本核心架构基线
日期：2026-07-21
适用范围：定期发现、数据复查、邮箱重验、邮件活动、自动跟进、回复停止、CRM 转化和品牌监控

## 1. 产品决策

“自动开发客户”定义为 BuyerReach 的核心产品主流程：用户配置目标客户画像、质量要求、允许的触达方式、预算和人工审批级别后，系统持续完成客户发现、验证、筛选、触达、跟进、回复处理和销售协同。

自动化不等于无边界自治。系统必须在用户锁定的策略、合规规则、预算、发送窗口、抑制名单和审批模式内运行。任何退订、投诉、拒绝联系、硬退信或人工禁止都拥有最高优先级，可立即停止后续动作。

统一 Automation Control Plane 是跨版本基础能力。各业务模块只提供可版本化、可幂等执行的动作，不各自建立第二套定时器、重试、审批和状态规则。

## 2. 核心用户旅程

### 2.1 创建自动开发计划

默认只要求用户做业务决定：

1. 选择或描述目标客户；
2. 选择每周期希望新增/开发的客户数量；
3. 选择运行频率；
4. 选择自动化等级；
5. 确认预算和允许的发送时段；
6. 启用。

系统自动生成并展示摘要：

```text
每周寻找最多 100 家欧洲箱包品牌和零售商
官网和邮箱必须经过验证
评分 A/B 的客户进入开发池
首封邮件需要人工批准，后续跟进可自动发送
收到回复、退订、投诉或硬退信时立即停止
每周 Provider/AI/发送预算上限：已配置
```

高级用户可以展开查看查询切片、筛选、模板、序列、账户、限速、重试和通知；普通用户不需要理解 Job、Cron、Lease、Provider 或 Adapter。

### 2.2 持续运行闭环

```text
定时触发
-> 增量发现客户
-> 官网/公司/联系人/邮箱验证
-> 评分和质量门
-> 去重、抑制和频控检查
-> 生成个性化草稿
-> 按审批模式等待或发送
-> 计划后续步骤
-> 接收退信、回复、退订和投诉
-> 停止或创建 CRM 跟进
-> 将业务结果反馈给报表和离线策略评估
```

每个步骤产生部分结果并可独立重试。计划暂停或系统故障后，从数据库状态恢复，不从头重复整条链路。

## 3. 自动化等级

### `draft_only`

- 自动发现、验证、评分和生成邮件草稿；
- 不发送真实邮件；
- 适合首次启用和新策略验证。

### `review_required`（默认推荐）

- 自动完成发现到草稿；
- 首封或所有邮件必须人工批准；
- 用户可以批量批准、修改或拒绝；
- 未及时批准时保持等待，不自动越权发送。

### `guarded_auto`

- 只对满足锁定质量门和合规门的客户自动发送；
- 低置信度、异常、来源冲突或模板风险进入审核；
- 退订、投诉、回复、硬退信和预算边界立即停止；
- 启用需要管理员权限和二次确认。

### `disabled`

- 不再产生新运行；
- 已排队但尚未开始的动作取消；
- 已完成结果和审计保留。

第一版不得提供无保护的 `fully_autonomous` 模式。

## 4. 统一自动化领域模型

### 4.1 `automation_definition`

表示用户可理解的自动化计划：

- organization_id、name、description；
- automation_type；
- active_version_id；
- status：draft/review/active/paused/disabled/archived；
- owner_id、department_id；
- created_by/updated_by；
- created_at/updated_at。

### 4.2 `automation_version`

不可变业务快照：

- definition_id、version；
- trigger_policy；
- customer_profile_version；
- query_plan_template_version；
- quality_policy_version；
- outreach_policy_version；
- approval_policy_version；
- budget_policy；
- stop_policy；
- notification_policy；
- rollout_mode、rollout_percentage；
- created_by、approved_by、approved_at。

发布新版本不改变已创建的 Automation Run。

### 4.3 `automation_trigger`

触发类型：

- schedule：周期/时区；
- domain_event：例如 `task.completed`、`email.verified`、`reply.received`；
- manual：用户立即运行；
- threshold：质量问题或信号达到阈值；
- recovery：恢复过期 Lease，不改变业务版本。

字段至少包含：trigger_type、schedule_spec、timezone、event_filter、next_fire_at、last_fire_at、status。

不允许用户提交任意可执行代码。Cron/周期输入经过严格解析和最小间隔限制。

### 4.4 `automation_run`

一次触发的持久执行实例：

- definition_id、automation_version_id；
- trigger_id、trigger_event_id；
- scheduled_for、started_at、finished_at；
- status；
- configuration_snapshot；
- budget_limit、spent；
- counters；
- stop_reason；
- trace_id；
- idempotency_key。

唯一键至少覆盖 `automation_version_id + trigger_id + scheduled_for/event_id`，防止 Beat 重复扫描和事件重复投递创建两次运行。

### 4.5 `automation_step_run`

每个业务动作的持久执行记录：

- automation_run_id；
- step_key、step_version；
- entity_type、entity_id；
- status；
- input_hash、idempotency_key；
- attempts、next_retry_at；
- lease_owner、lease_expires_at、heartbeat_at；
- provider、vendor_request_id、cost；
- output_ref；
- error_code、error_message；
- started_at、completed_at。

外部/昂贵步骤必须独立记录，不能只保存在 Automation Run 的 JSON 中。

### 4.6 `automation_approval`

- automation_run_id/step_run_id；
- approval_type；
- status：pending/approved/rejected/expired/cancelled；
- requested_from；
- request_snapshot；
- decision、reason、decided_by、decided_at；
- expires_at；
- idempotency_key。

批准只授权快照中的具体动作。内容、收件人、账户或策略变化后必须重新批准。

## 5. 调度架构

### 5.1 单一到期扫描器

Celery Beat 只运行少量固定系统任务：

```text
scan_due_automation_triggers
recover_expired_automation_leases
publish_domain_outbox
```

不得为每个用户计划动态写入 Celery Beat 配置。用户计划、时区、下一次执行时间和状态保存在 PostgreSQL。

`scan_due_automation_triggers`：

1. 使用数据库锁领取到期触发器；
2. 创建幂等 Automation Run；
3. 在同一事务更新 `last_fire_at/next_fire_at` 并写 Outbox；
4. 提交后发送 Celery 唤醒消息；
5. 重复扫描返回现有 Run，不重复创建。

### 5.2 时间语义

- 数据库存 UTC；
- 用户选择 IANA timezone；
- UI 按用户时区显示；
- DST 重复/跳过时间必须有确定性策略；
- 修改日程只影响未来触发，不移动已创建 Run；
- 系统停机后恢复时按 misfire policy 决定补跑一次、跳过或合并，禁止无界补跑。

### 5.3 公平与容量

复用查询切片的 Capacity Lease 和公平调度：

- 系统、租户、Automation、Provider、Credential、发送账户和域名级槽位；
- 组织轮转，再按 Automation 轮转；
- 交互任务优先于后台维护任务；
- 自动发送优先级不能绕过抑制、账户健康、限速和预算；
- 队列达到上限时延迟运行并显示原因，不持续创建更多 StepRun。

## 6. 标准动作契约

各领域注册版本化动作：

```python
class AutomationAction(Protocol):
    key: str
    version: str
    def validate(snapshot, context) -> ValidationResult: ...
    def plan(snapshot, context) -> list[ActionItem]: ...
    def execute(item, idempotency_key) -> ActionResult: ...
    def compensate_or_stop(result, context) -> RecoveryDecision: ...
```

第一批动作：

- discovery.run_incremental；
- verification.refresh_website；
- verification.refresh_contact；
- verification.verify_email；
- scoring.evaluate_customer；
- outreach.render_message；
- outreach.request_approval；
- outreach.schedule_message；
- outreach.send_message；
- outreach.process_event；
- crm.create_or_update_lead；
- crm.create_followup；
- monitoring.capture_target。

动作不得跨越领域直接修改其他模块状态。跨领域推进使用数据库事件和编排决策。

## 7. 自动客户开发状态模型

### 7.1 客户开发对象

为每个自动化计划与品牌/联系人建立 `development_subject`：

- automation_definition_id；
- brand_id、contact_id、email_id；
- current_stage；
- quality_status；
- outreach_status；
- next_action_at；
- stop_reason；
- last_action_at；
- active_campaign_recipient_id；
- version。

建议阶段：

```text
discovered
-> verifying
-> qualified/review/rejected
-> preparing_outreach
-> awaiting_approval/ready_to_send
-> contacted
-> followup_scheduled
-> replied/converted/nurture
-> stopped
```

`replied`、`converted`、`rejected` 和 `stopped` 不是一概永久终态；是否可重新进入 nurture 必须由用户策略和合规规则明确决定。退订、投诉、人工禁止始终阻止发送。

### 7.2 发送前强制检查

每一封邮件在实际发送前重新检查：

1. Automation/Run/活动未暂停或取消；
2. 审批仍对当前消息快照有效；
3. 收件人和品牌不在任何适用 Suppression；
4. 邮箱验证未过期，且状态满足政策；
5. 没有更新的回复、退订、投诉或硬退信事件；
6. 发送账户和域名健康；
7. 当前时间位于收件人时区允许窗口；
8. 账户、域名、品牌和联系人频控允许；
9. 预算和每日额度允许；
10. 幂等键尚未完成。

任何一项失败都不发送，并记录可解释原因。

## 8. 邮箱自动验证与重验

验证策略至少定义：

- 首次发送前最大验证年龄；
- follow-up 前是否重新验证；
- valid/risky/catch-all/unknown 的允许动作；
- Provider 顺序与任务快照；
- 每周期预算；
- 临时错误的退避和最大重试；
- 永久无效、退订、投诉和人工禁止处理。

默认安全策略：

- 没有有效验证或验证过期：进入验证队列，不发送；
- `invalid/do_not_contact/disposable`：停止；
- `catch-all/risky/unknown`：进入审核或根据明确策略跳过，默认不自动发送；
- Provider 不可用：保持等待或审核，不将未知恢复成有效；
- 新验证结果新增历史，不覆盖旧记录。

定时重验使用 Automation Trigger，不在 Celery Beat 中为每个邮箱建立条目。

## 9. 邮件自动发送和跟进

### 9.1 发送调度

- 按收件人时区、允许工作日和发送窗口计算 `scheduled_at`；
- 一封邮件一个 Message 记录和幂等键；
- 发送账户、域名、组织和品牌共享容量 Lease；
- Provider 成功响应和 vendor_message_id 持久化后才确认 Job；
- 远端成功但本地落盘前崩溃的边界必须记录并尽量通过 Provider 原生幂等/查询接口恢复；
- 不支持原生幂等的 Provider 不得宣称 exactly-once。

### 9.2 跟进停止规则

以下事件必须取消尚未发送的后续步骤：

- reply received；
- unsubscribe；
- complaint；
- hard bounce；
- manual stop/do-not-contact；
- brand-level suppression；
- campaign/automation paused or cancelled；
- CRM 转为不应继续触达的阶段。

Out-of-office 可按策略延迟到返回日期后，不得与普通无回复相同处理。Referral 可以停止原联系人并创建人工审核的推荐联系人任务。

### 9.3 个性化

- AI 只生成草稿、切入点和证据引用；
- 内容必须绑定 Prompt、Model、Knowledge、Template 和 Policy 版本；
- 不得编造公司事实、合作历史或产品能力；
- 证据不足时使用安全模板或进入审核；
- guarded_auto 只能使用已批准模板和允许变量；
- 用户修改后的消息保存为新快照，不能被后台重新生成覆盖。

## 10. 事件驱动推进

关键事件：

- automation.triggered；
- automation.run_started/paused/resumed/completed/partial/failed；
- automation.step_queued/started/completed/retryable/failed；
- approval.requested/approved/rejected/expired；
- customer.qualified/review/rejected；
- email.verification_due/verified；
- message.scheduled/sent/failed/skipped；
- email.delivered/bounced/replied/unsubscribed/complained；
- development_subject.stage_changed；
- crm.lead_created/followup_created。

事件与业务变化同事务写 Outbox。消费者按 event_id 和业务幂等键去重，容忍乱序和未知字段。

## 11. 失败、恢复和部分成功

- 单个客户失败不能使整个 Automation Run 失败；
- Run 允许 partial，并列出可用结果、失败原因和下一步；
- 认证失败、配置错误和额度耗尽不盲目自动重试；
- 网络、429 和临时 5xx 使用有上限的指数退避和 jitter；
- Worker/Redis 重启由过期 Lease 扫描恢复；
- 自动化暂停后不创建新 StepRun，运行中的外部调用结束后不继续下游；
- 取消保留历史、结果、消息、事件和审计；
- 任何恢复动作重新检查当前 suppression 和取消状态；
- 失败通知合并去重，避免每个客户产生一条告警风暴。

## 12. 用户体验

### 12.1 自动化中心

卡片显示：

- 计划名称和自动化等级；
- 下一次执行时间和时区；
- 当前运行、等待审批、异常和已暂停数量；
- 本周期发现、合格、已联系、回复、停止数量；
- 实际成本和预算余额；
- 最近进展和停止原因；
- 暂停、继续、立即运行、编辑新版本和查看详情。

### 12.2 今日待办

用户只需处理业务决策：

- 待批准的客户和邮件；
- 低置信度候选；
- 积极/转介/未知回复；
- 配置、账户健康或额度异常；
- 自动化建议的范围扩展。

技术重试、分页、Lease 和 Provider fallback 默认自动处理并放在诊断详情。

### 12.3 明显感知

- 启用后立即确认并显示下一次运行；
- 后台运行时全局任务中心可见；
- 部分结果实时可用；
- 自动发送前显示审核/自动模式和安全门状态；
- 暂停后明确哪些动作已停止、哪些外部调用可能已发生；
- 不显示未经测量的精确完成时间或成功率。

## 13. 权限与安全

多用户、组织隔离、数据范围、后台服务身份和审批职责分离，以 `docs/multi-user-access-control-architecture-v1.md` 为权威架构。本节只声明自动开发客户领域需要的敏感权限，不另建一套角色判断。

敏感权限分离：

- 创建草稿自动化；
- 发布/启用自动化；
- 启用 guarded_auto；
- 管理发送账户和域名；
- 批量批准邮件；
- 修改 suppression；
- 查看完整邮箱和回复正文；
- 导出；
- 查看成本和审计。

guarded_auto、降低质量门、扩大国家/客户范围、提高预算和解除 suppression 都必须二次确认并审计。解除投诉、退订或人工禁止默认不允许普通管理员直接操作，应使用受控申诉/纠错流程。

定时任务使用组织级 Automation Principal 执行，不长期模拟创建者个人 Token。发布、真实发送、Credential 使用、导出和解除 suppression 等高风险 Step 在实际执行前必须重新检查实时授权；权限撤销后进入 `blocked_authorization`，不得继续调用 Provider 或无限重试。

## 14. 可观测性

至少记录：

- due trigger lag、misfire、Run 创建率；
- Step queue/run duration、retry、lease expiry；
- active/queued Run by tenant；
- discovery、verification、qualified、approval、send 和 reply 漏斗；
- time to first qualified customer、time to first send、time to reply；
- Provider/AI/发送成本；
- suppression block count；
- stale verification prevented sends；
- send idempotency conflict；
- bounce、unsubscribe、complaint 和 positive reply；
- account/domain health 和 queue oldest age；
- API、数据库池、Outbox 和 SSE 状态。

自动化成功不能只按“Job completed”计算，必须按用户结果区分发现、合格、批准、发送、回复和转化。

## 15. 发布顺序

### Automation Foundation 1

消费者：定期增量发现 + 数据质量复查/邮箱重验。

- Definition/Version/Trigger/Run/StepRun；
- 数据库到期扫描；
- Lease、幂等、暂停、恢复、预算和任务中心；
- 不发送邮件。

### Outreach Automation 1

- draft_only；
- 模板渲染、验证和 approval；
- 只向内部测试地址发送；
- shadow 记录本应发送的动作。

### Outreach Automation 2

- review_required；
- 小规模真实客户人工批准发送；
- Webhook、回复、退订、投诉和停止规则；
- 明确中止阈值。

### Customer Development Autopilot

- guarded_auto；
- 只对稳定画像、批准模板、健康账户和低风险客户开放；
- 按组织和计划百分比 rollout；
- 持续抽检和异常自动降级到 review_required。

### Monitoring and CRM Automation

- 变化/信号触发；
- 创建审核机会和 CRM 跟进；
- 不自动改变高风险商机阶段或绕过审批。

## 16. 验收场景

1. 同一时间触发两次，只创建一个 Automation Run；
2. 系统停机跨过计划时间，按 misfire policy 只补跑一次或明确跳过；
3. 多组织自动化公平运行，一个大计划不占满 Worker；
4. 暂停后不产生新发现、验证和发送；
5. Worker 在 Step 中崩溃可恢复且不重复已完成外部动作；
6. 邮箱验证过期阻止发送并自动创建重验；
7. 收到回复后取消所有待发 follow-up；
8. 退订/投诉与事件同事务进入 suppression 并阻止后续发送；
9. Webhook 重复和乱序不会重复改变状态；
10. 修改模板后旧批准失效，需要重新批准；
11. 账户 unhealthy、额度不足、超预算或不在发送窗口时不发送；
12. AI 不可用时保留安全模板或进入审核，不生成虚构个性化；
13. 计划升级版本不改变已经创建的 Run；
14. Redis 重启后数据库状态可恢复；
15. 用户能明确看到运行、排队、等待审批、暂停、部分成功和失败原因；
16. rollback 到 disabled/review_required 不删除任何历史消息、事件、回复和审计。

## 17. 近期实施建议

1. 完成查询切片 Phase A-E，复用其公平调度和 Capacity Lease；
2. 在 V1.5B 实现统一 Automation Foundation，但只接两个真实消费者：定期增量发现和邮箱/官网复查；
3. 验证到期扫描、时区、misfire、暂停恢复和任务中心；
4. V2.0A 完成 suppression、账户、域名、模板和审批后，再注册 Outreach Action；
5. 先上线 draft_only，再 review_required，最后 guarded_auto；
6. guarded_auto 必须有真实低风险样本、Webhooks、停止规则和回滚演练后才可启用。

## 18. 设计门

- 产品：通过。自动开发客户成为核心旅程，普通用户只配置业务目标和安全等级。
- 架构：通过。统一控制面负责调度，领域模块负责可幂等动作。
- 数据：通过设计门。Definition/Version/Run/StepRun/Approval 均持久化且历史不可变。
- 可靠性：通过设计门。数据库到期扫描、Lease、幂等、恢复、预算和部分成功齐全。
- 演进：通过。新动作、策略、模板和触发器独立版本化。
- 运维：通过设计门。容量、指标、告警、misfire、暂停和回滚已定义。
- 安全：通过设计门。抑制最高优先级，发送前重新验证，guarded_auto 受控启用。
- 体验：通过设计门。渐进披露、今日待办、明显状态和部分结果降低用户操作。
- 验证：待实施证明。本文不代表自动发送或自动开发已经上线。
