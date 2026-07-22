# VSCode Agent 执行 Prompt：严格实施批量精准品牌任务 v1

将以下内容完整交给 VSCode 中的 AI 编程 Agent 执行。

---

你正在修改 BuyerReach 仓库。请完整实现“批量导入公司并执行批量精准品牌全流程”。不要只生成方案、伪代码、空模型、未接线 API 或静态页面；必须交付可运行、可恢复、可验证的生产纵向功能。

## 一、唯一实施依据

开始前必须完整阅读并遵循：

1. `AGENTS.md`
2. `docs/DEVELOPMENT_RULES.md`
3. `docs/pipeline-production-architecture-v1.md`
4. `docs/production-integrity-audit-2026-07-17.md`
5. `docs/batch-exact-brand-production-plan-v1.md`

`docs/batch-exact-brand-production-plan-v1.md` 是本功能的权威方案。实现不得擅自删减、改写核心语义或创建第二套架构。若发现现有代码与方案冲突，先记录冲突、追踪完整调用链，再选择向后兼容的实现；不要用旁路绕过状态机、Pipeline、Vendor Adapter、配置快照或权限模型。

## 二、不可变要求

1. 用户流程必须保持四步：上传文件 → 校验预览 → 统一设置 → 可视化执行结果。
2. 不支持每家公司不同职位。模板中禁止 `target_titles`；职位、Vendor、联系人上限和可靠邮箱策略均为父任务级统一配置。
3. 公司名称和官方域名必须按行绑定，禁止继续使用两个无映射关系的数组表达批量目标。
4. 一个批次对应一个父 `SearchTask`，每个有效输入行对应一个持久化 `ExactBrandTarget`。
5. 每个 Target 独立执行、失败、恢复和重试；失败项重试绝不能重新执行已成功项。
6. Apollo、Hunter 或两者由用户选择；双选时 Vendor 全流程独立执行，不得跨 Vendor 自动回退。
7. 批量层必须复用现有精准品牌 Pipeline、状态机、配置快照、`TaskVendorPlan`、Stage Run、Outbox 和版本化 Vendor Adapter。不得复制 Adapter 或建立批量专用搜索旁路。
8. 上传、解析和预览阶段不得调用任何付费 Vendor。
9. 数据库是状态真相来源。前端轮询或 SSE 只能读取并展示持久化状态。
10. 只有 `authenticity_level=verified` 计入可靠邮箱；不得把 found、valid-looking、probable、risky 或 unverified 显示成可靠邮箱。
11. 必须保持现有品牌发现、单个精准品牌和历史任务兼容。

## 三、执行方式

先检查当前 git 状态。工作区可能包含用户未提交修改，必须保留，不得 reset、checkout、清理或覆盖无关文件。阅读现有 SearchTask、TaskVendorPlan、Pipeline Runner、状态机、Vendor Pipeline、导入预览、任务 UI、EntityTable、权限和测试实现，画出当前调用链后再编辑。

使用小步纵向实现，每一阶段完成后立即执行对应测试。不要一次性写大量未验证代码。

### 阶段 1：导入契约与解析

- 提供版本化模板 `exact-brand-import-v1` 下载。
- 支持 `.xlsx`、UTF-8/UTF-8 BOM `.csv`。
- 核心列只包含 `company_name`、`official_domain`；可选列为 `country`、`external_id`、`notes`。
- 实现文件大小、行数、格式、公式注入、非法域名和资源消耗限制。
- 实现 Unicode NFKC、公司名、URL/域名、IDNA、空白和重复规范化。
- 保留原始行号、原始值、规范化值、警告和错误码。
- Preview 必须为只读，不创建 SearchTask、Target 或 Vendor 请求。
- 提供可下载的错误报告。

### 阶段 2：数据模型和迁移

- 使用 additive Alembic migration 新增 `BatchImport` 和 `ExactBrandTarget`。
- 状态、字段、约束和幂等范围严格按照权威方案。
- 所有记录包含组织隔离和创建者/审计信息。
- 不删除或重命名现有字段，不做 contract migration。
- 新增 schema、序列化契约和未知状态安全处理。

### 阶段 3：API 与确认事务

实现方案定义的模板、preview、import、batch detail、confirm、targets、retry、errors export 和 result export API。

- 所有 API 执行权限与 organization_id 隔离。
- Confirm 必须幂等，并在一个事务内创建父任务、Target、配置快照、TaskVendorPlan 和 Outbox 事件。
- 重复 confirm 返回同一个父任务，不创建第二份数据。
- 输入错误使用稳定、可测试、可操作的错误码和中文用户提示。

### 阶段 4：Target 执行与恢复

- 将现有精准品牌执行入口抽取为可按单个 Target 调用的纵向路径。
- 幂等键必须包含 `task_id + target_id + vendor + stage_name + stage_version + input_hash`。
- 每个 Target 运行公司匹配、联系人搜索、邮箱发现、Vendor 状态证据、真实性判断和持久化。
- Apollo/Hunter 双选分别执行，不互相回退。
- 实现有限并发、Vendor 限流、429 退避、预算检查、取消检查、重试上限和过期 lease 恢复。
- `no_match` 是终止业务结果，不自动当异常重试。
- 单目标失败不得回滚其他目标；父任务按真实聚合进入 completed、partial、failed 或 cancelled。
- 成功 Target 在失败项重试时不得重新调用 Vendor。

### 阶段 5：简化前端和可视化

- 在任务创建中增加“批量精准品牌”。
- 实现四步体验，普通用户不看到内部技术字段。
- 上传后显示总计、可执行、重复、错误；默认自动跳过错误和重复。
- 任务级统一设置 Vendor、目标职位和联系人上限；高级配置折叠。
- 确认前显示有效公司、Vendor 管线数和理论最大联系人处理量，并注明是上限而非精确费用。
- 任务列表展示总数、已完成、执行中、等待、未找到、失败、可靠邮箱和待复核邮箱。
- 详情页展示总览、各阶段真实计数、按导入顺序稳定的公司表、筛选和公司展开详情。
- 实现暂停、继续、取消、仅重试失败项、导出可靠邮箱和高级导出。
- 实时刷新不得改变用户排序、选择或展开状态。
- 覆盖 loading、empty、running、partial、completed、failed、cancelled、offline 和无权限状态。
- 使用现有设计系统；保证键盘、焦点、非颜色状态提示、桌面和窄屏可用。

### 阶段 6：运维、审计和兼容

- 审计上传、确认、开始、暂停、继续、取消、重试和导出。
- 日志和事件不得包含 API Key、Authorization、Token、Credential 或不必要的联系人敏感信息。
- 增加方案要求的批次、Target、Vendor、429、重试、成本和可靠邮箱指标。
- 增加 feature flag，可关闭新建入口但继续读取历史批次。
- 更新必要的开发/部署/用户文档，但不要在其他文件建立第二套架构规则。

## 四、测试要求

测试必须证明行为，而不是只覆盖代码行。至少包括：

- CSV/XLSX 正常、空文件、错误表头、超限、非法域名、URL 规范化、Unicode、重复、冲突和公式注入。
- Preview 零 Vendor 调用。
- 组织权限隔离和越权拒绝。
- Confirm 幂等和并发重复确认。
- 公司与域名逐行绑定，不发生跨行匹配。
- 单 Target 成功、no_match、retryable、failed、cancelled。
- Apollo-only、Hunter-only、Apollo+Hunter 独立执行。
- Worker 中断、过期 running 恢复、重复消息和 Stage 幂等。
- 只重试失败项，成功项 Vendor 调用次数不增加。
- 预算、取消、429 和重试上限。
- 父任务 completed、partial、failed、cancelled 聚合正确。
- Apollo/Hunter 来源证据及可靠邮箱计数正确。
- 老任务、旧 API 和历史数据可读。
- 前端上传、预览、确认、进度、筛选、失败项重试、导出和未知状态。

## 五、必须执行的验证

根据仓库实际环境运行并报告完整结果，至少包括：

```text
Backend:   python -m pytest -q
Static:    python -m ruff check app tests migrations
Compile:   python -m compileall -q app migrations
Frontend:  pnpm exec vue-tsc --noEmit
Build:     pnpm run build
Migration: alembic upgrade head
           alembic downgrade <previous_revision>
           alembic upgrade head
Runtime:   /health, /ready, Worker/Beat 启动和恢复日志
Visual:    桌面宽度与窄屏宽度检查四步创建和任务详情关键路径
```

迁移演练必须同时覆盖全新数据库和现有数据库副本。不得对用户真实数据库直接执行破坏性降级演练；使用隔离数据库或备份副本。

如果某项无法执行，必须明确说明原因、风险和替代证据，不能把未验证描述为通过。

## 六、完成定义

只有满足以下条件才能宣布完成：

1. 用户能下载模板、上传、预览、统一配置并确认任务。
2. 有效行形成持久化 Target，错误行不产生 Vendor 调用。
3. 每个 Target 经真实 Pipeline 执行并持久化结果。
4. 页面刷新和 Worker 重启后进度可恢复。
5. 失败项可单独重试，成功项不重复计费。
6. 可视化显示数据库真实状态和可靠邮箱。
7. 导出可用，权限、审计和组织隔离完整。
8. 全量测试、静态检查、构建、迁移和运行态验证达到仓库发布要求。
9. 无遗留 TODO、假数据、未接线按钮、无调用方模型或重复业务规则。

## 七、交付格式

完成后按以下顺序报告：

1. 用户现在能够完成的完整操作。
2. 实际修改的文件、迁移和关键架构选择。
3. API、数据模型、状态机和兼容性说明。
4. 实际执行的全部验证命令与结果。
5. 桌面和窄屏视觉检查结果。
6. 部署、升级、回滚和 feature flag 操作。
7. 尚未验证或仍存在的真实风险。

不要只回复“已完成”。在所有门禁通过前持续修复并验证，但不得删除或覆盖用户无关改动。

---
