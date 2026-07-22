# BuyerReach 开发与架构规则

本文件是 BuyerReach 唯一权威开发规则（Single Source of Truth）。适用于人工开发、Codex、Claude Code、Cursor、GitHub Copilot 及其他编辑器或 AI 工具。编辑器适配文件只能引用本文件，不得复制或另建冲突规则。

## 1. 工作方式

1. 修改前阅读本文件、相关架构文档、迁移、测试和现有实现，追踪完整调用链。
2. 保留用户已有改动，不清理或覆盖无关文件。
3. 实现必须形成可运行的纵向切片，禁止只增加未接入真实运行路径的模型、接口或文档。
4. 每次修改后必须验证。不得把“代码存在”“能够编译”描述为功能已经生产可用。
5. 改动本规则必须单独说明原因、兼容影响和验证方式，并同步检查各编辑器入口仍指向本文件。

## 2. 架构边界

- 数据库是任务、候选、Stage、事件、成本和审计的最终真实来源。
- API 负责鉴权、输入输出和事务边界；不得承载整条业务流程。
- Pipeline orchestration 负责阶段编排；业务步骤必须是独立 Stage，不得继续扩大巨型 service 函数。
- 状态策略、评分策略、Prompt、Vendor 协议、持久化和 UI 展示必须分离。
- Vendor URL、认证协议、调用步骤、请求与响应映射、错误映射只能位于版本化 Workflow Adapter。
- 第三方密钥、启用状态、Vendor 选择、模型和 rollout 只能通过“系统配置”维护。
- 新增 Vendor 必须同时提供 Credential 配置入口、配置校验、免费连接测试、失败提示、配额检查和完整 Adapter。

## 3. 版本和配置锁定

- Pipeline 必须分别记录 `pipeline_version`、`scoring_policy_version`、`prompt_version`、`adapter_version`、`evidence_schema_version`、`result_schema_version`。
- 创建任务时立即保存 `configuration_snapshot`、`configuration_version` 和 `TaskVendorPlan`。
- 任务运行期间不得因系统配置变化而改变已锁定的 Vendor、模型、Prompt、评分策略、阈值、rollout 或预算。
- 快照不得保存 API Key、Authorization、Token、密码或明文 Credential，只保存受治理的 Credential ID 引用。
- Prompt、Policy、Adapter 和 Schema 的新语义必须新增版本，不覆盖历史版本。

## 4. 状态、异步任务和计费

- 所有候选状态变化必须调用统一 `transition_candidate`；API、Worker 和业务服务不得直接赋值。
- 所有任务状态变化必须调用统一 `transition_task`。
- 每个外部或昂贵 Stage 必须有数据库执行记录和唯一幂等键：`task_id + candidate_id + stage_name + stage_version + input_hash`。
- 发起计费调用前必须检查任务取消、预算、重试次数和 rollout。
- Vendor 成功结果必须持久化后才能调度下游步骤；优先使用 Vendor 原生幂等键。
- Worker 必须能够恢复 queued、超时 running、retryable 和过期 lease；Redis/Celery 只负责唤醒，不是最终状态来源。
- 数据变化和事件必须在同一事务写入 Outbox；通知失败保留待发布状态并可安全重试。

## 5. 数据库和兼容性

- 迁移采用 `expand → migrate/backfill → switch → contract`。
- 当前迁移不得直接删除旧字段、历史评分或旧客户端仍读取的数据。
- 回填任务必须可暂停、恢复、观察、限速并可重复执行。
- API 和事件只做向后兼容的增量变更；未知字段必须被忽略，未知状态和评级必须安全显示。
- 发布前至少验证：现有数据库升级、旧数据读取、降一版、再次升级；全新数据库必须可初始化。
- 破坏性 contract 迁移必须在独立版本和明确兼容窗口后进行。

## 6. AI、评分与重算

- AI Adapter 只输出维度结果、解析状态和证据判断，不负责最终业务评级。
- 确定性 `RelevancePolicy` 负责权重、封顶、硬规则和 A/B/C/D。
- 禁止在普通 service 中写死不可追踪的大段 Prompt。
- AI 不可用或被关闭时显示“待评估”，不得恢复虚构的统一分数。
- `policy_only` 必须复用历史 AI 维度结果且不调用 AI Vendor。
- 重算必须新增评分历史，不覆盖旧记录；支持批次、预算、暂停、恢复和新旧对比。
- shadow 结果不得改变正式业务决策。

## 7. 安全和隐私

- 所有 API 必须执行权限检查和组织/租户隔离；查询不得无条件跨组织返回数据。
- 外部 URL 输入必须防 SSRF；限制协议、目标地址、重定向、响应大小和超时。
- 日志、异常、事件、审计和快照不得包含 API Key、Authorization Header、Token、密码、明文 Credential 或不必要的联系人敏感信息。
- 不得提交 `.env`、备份、数据库 dump、生成的密钥或真实联系人数据。
- 生产环境不得使用默认 JWT、加密密钥或默认管理员密码。

## 8. 可观测性和运行

- 跨服务记录 `trace_id`、`task_id`、`candidate_id`、`stage_run_id` 和 `vendor_request_id`。
- 指标至少覆盖首个候选/AI 评分时间、Stage 排队/处理耗时、Vendor 成功率、AI 解析失败率、重试率、成本、评级分布、人工推翻率和自动开发成功率。
- rollout 使用 `disabled`、`shadow`、`review`、`active` 和 `rollout_percentage`。
- 回滚不得删除历史记录或制造 fallback 分数；必须支持停止 AI、恢复旧 Prompt/Adapter/Policy 和关闭自动联系人开发。
- 关键故障必须有恢复步骤、告警信号和终止 rollout 的阈值。

## 9. 前端与用户体验

- 长任务必须立即确认，并显示真实阶段、可用的部分结果、失败原因、重试、取消和恢复状态。
- 不得把估算值显示为测量结果，不得把未评分候选显示为已评分。
- 未知状态、评级和事件字段不得导致页面崩溃。
- 保持键盘可用、焦点可见、语义标签、非颜色状态提示、合理对比度和响应式布局。
- UI 修改除了类型检查和构建，还必须在桌面和窄屏至少各检查一次关键路径。

## 10. 完成定义

根据改动范围执行并记录：

```text
Backend:  python -m pytest -q
Static:   python -m ruff check app tests migrations
Compile:  python -m compileall -q app migrations
Frontend: pnpm exec vue-tsc --noEmit
Build:    pnpm run build
Go:       go vet ./... && go test ./...
Migration: alembic upgrade head; downgrade one revision; upgrade head
Runtime:  /health, /ready, Worker/Beat logs, database revision, Outbox backlog
```

若某项无法执行，必须明确说明原因和风险。存在数据完整性、重复计费、密钥泄露、迁移失败或无法回滚风险时，不得宣称生产可用。

## 11. 规则优先级

1. 法律、安全和用户明确指令。
2. 本文件。
3. 模块内更具体的 `AGENTS.md`（只能增加局部约束，不能削弱本文件）。
4. 编辑器适配文件。
5. 工具默认行为。

发现冲突时停止采用较低优先级规则，在交付说明中记录冲突和处理结果。
