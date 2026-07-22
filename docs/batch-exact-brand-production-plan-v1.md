# BuyerReach 批量精准品牌任务生产方案 v1

## 1. 文档地位与目标

本文定义 BuyerReach“批量导入公司并执行精准品牌全流程”的产品、数据、API、执行和验收方案。实现必须同时遵循：

- `docs/DEVELOPMENT_RULES.md`
- `docs/pipeline-production-architecture-v1.md`
- `docs/production-integrity-audit-2026-07-17.md`

目标是让用户只完成三件事：上传公司文件、选择统一任务参数、点击开始执行。系统负责规范化、校验、去重、任务拆分、独立 Vendor 全流程、恢复、验证和结果归类。

## 2. 不可变产品约束

1. 操作流程固定为“上传文件 → 校验预览 → 统一设置 → 可视化执行结果”。
2. 导入文件不支持每家公司设置不同职位。目标职位、Vendor、联系人上限和可靠邮箱策略全部属于任务级配置。
3. 公司名称与官方域名必须保持逐行绑定；禁止分别写入两个无对应关系的数组。
4. Apollo、Hunter 或两者由用户在任务级选择。双选时两条 Vendor 管线独立执行，不得互相回退。
5. 每家公司是独立执行目标，可独立成功、失败、取消和重试。
6. 失败项重试不得重新执行已成功目标，也不得造成重复计费。
7. 只有最终 `authenticity_level=verified` 的邮箱计入“可靠邮箱”。
8. 上传和预览阶段不得调用付费 Vendor。
9. 数据库是批次、目标、阶段、结果和进度的最终真实来源；Celery 只负责唤醒。
10. 必须兼容现有品牌发现、单个精准品牌任务和历史数据。

## 3. 最简用户旅程

### 3.1 上传

新建任务增加“批量精准品牌”模式。用户可下载标准模板并上传 `.xlsx` 或 `.csv`。

模板核心字段：

| 字段 | 必填 | 示例 | 说明 |
|---|---:|---|---|
| `company_name` | 是 | MANGO | 公司或品牌名称 |
| `official_domain` | 是 | mango.com | 官方域名，精准匹配主依据 |
| `country` | 否 | Spain | 总部或注册国家 |
| `external_id` | 否 | C-0001 | 用户侧编号 |
| `notes` | 否 | Key account | 备注，不参与自动匹配 |

明确禁止在模板中加入 `target_titles`、Vendor、联系人上限或邮箱策略字段。

### 3.2 校验预览

系统自动规范化并显示：总计、可执行、重复、错误。默认跳过空行、合并完全重复数据、拒绝错误行。用户可查看问题数据并下载带原始行号的错误报告。

预览阶段只有一个主操作：“下一步：设置任务”。

### 3.3 统一设置

任务级必填项：

- 数据来源：仅 Apollo、仅 Hunter、Apollo + Hunter
- 目标职位：统一应用于所有公司
- 每家公司联系人上限

默认开启“仅将可靠邮箱计入有效结果”。高级设置折叠展示，包括跳过已有品牌、预算上限和并发限制。

确认前显示真实规模：有效公司数、Vendor 管线数、每家公司联系人上限和理论最大联系人处理量。不得把估算显示为精确费用。

### 3.4 可视化执行

任务列表显示真实聚合计数：总数、已完成、执行中、等待、未找到、失败、可靠邮箱、待复核邮箱。

任务详情包含：

1. 顶部总览卡片。
2. 按阶段的真实完成数量。
3. 按导入顺序稳定展示的公司明细表。
4. 状态筛选：全部、已完成、有可靠邮箱、未找到、失败、执行中。
5. 操作：暂停、继续、取消、仅重试失败项、导出可靠邮箱、导出全部结果。

实时刷新不得改变用户正在查看的排序或选择。

## 4. 文件与规范化规则

- 支持 `.xlsx`、UTF-8/UTF-8 BOM `.csv`。
- Excel 只读取第一个工作表，不接受合并单元格、公式结果作为业务输入或隐藏业务字段。
- 默认上限 5,000 行、10 MB；限制必须可通过系统配置调整。
- 公司名称执行 Unicode NFKC、首尾空白清理和长度校验。
- 域名从 URL 中提取，转小写、去 `www.`、路径、查询参数和末尾点，并进行 IDNA 规范化。
- 拒绝邮箱、IP、localhost、非法协议、非法域名和公式注入前缀。
- 同批次以 `normalized_domain` 为主键去重。
- 同域名不同公司名标记冲突；同公司多个域名作为独立目标并显示警告。
- 历史已存在品牌不自动删除。用户可在高级设置选择跳过或重新富化。
- 每行保留 `row_number`、原始输入、规范化输入、校验结果和错误码。

## 5. 数据模型

使用 additive migration 新增，不破坏现有表。

### 5.1 `BatchImport`

字段至少包括：

```text
id, organization_id, created_by, filename, template_version, file_hash,
status, total_rows, valid_rows, warning_rows, invalid_rows, duplicate_rows,
created_at, confirmed_at, error_summary
```

状态：

```text
uploaded → parsing → ready → confirmed → executing → completed|partial|failed|cancelled
                    ↘ invalid
```

### 5.2 `ExactBrandTarget`

字段至少包括：

```text
id, batch_import_id, search_task_id, row_number, external_id,
company_name, normalized_company_name, official_domain, normalized_domain,
country, notes, raw_input, validation_status, validation_errors,
execution_status, current_stage, error_code, error_message,
brand_id, contact_count, reliable_email_count, review_email_count,
created_at, updated_at
```

执行状态：

```text
pending, queued, running, completed, no_match, partial, retryable, failed, cancelled
```

约束：

```text
unique(batch_import_id, row_number)
unique(batch_import_id, normalized_domain)
```

每个付费 Stage 的幂等范围必须包含：

```text
task_id + target_id + vendor + stage_name + stage_version + input_hash
```

## 6. 任务与执行模型

一个批次确认后创建一个父 `SearchTask`，每个有效输入行创建一个 `ExactBrandTarget`。父任务保存不可变配置快照和 `TaskVendorPlan`。

每个 Target 复用现有精准品牌纵向管线：

```text
精准公司匹配 → 联系人搜索 → 邮箱获取 → Vendor 邮箱状态取证
→ 邮箱真实性判断 → 结果落库
```

不得复制现有 Vendor Adapter 或另建批量专用业务旁路。批量层只负责目标调度、聚合进度和恢复；Vendor 协议继续由版本化 Adapter 所有。

Apollo + Hunter 模式下，每个 Target 分别运行 Apollo 和 Hunter 全流程。结果层按组织、联系人和邮箱的既有归一化键去重，保留来源证据。

调度要求：

- 默认有限并发，按 Vendor 独立限流。
- 429 指数退避；付费调用前检查取消、预算和重试上限。
- 每个 Target 成功持久化后才调度下游 Stage。
- Worker 重启后从数据库恢复 queued、retryable 和过期 running。
- 单目标失败不回滚其他目标；父任务按真实结果成为 completed、partial、failed 或 cancelled。
- `no_match` 是有效业务结果，默认不自动重试。

## 7. API 契约

建议新增：

```http
GET  /api/v1/batch-exact-brand/template
POST /api/v1/batch-exact-brand/preview
POST /api/v1/batch-exact-brand/imports
GET  /api/v1/batch-exact-brand/imports/{batch_id}
POST /api/v1/batch-exact-brand/imports/{batch_id}/confirm
GET  /api/v1/search-tasks/{task_id}/targets
POST /api/v1/search-tasks/{task_id}/targets/retry
GET  /api/v1/search-tasks/{task_id}/targets/errors.csv
GET  /api/v1/search-tasks/{task_id}/targets/export.csv
```

所有接口必须执行权限和组织隔离。Preview 返回行级错误但不创建 Vendor 调用。Confirm 必须是幂等操作，同一批次重复确认不得创建第二个父任务。

API 和事件只做向后兼容的增量字段。未知状态在旧前端安全显示为“未知”，不得导致页面崩溃。

## 8. 可靠邮箱规则

- Apollo `email_status=verified` 或 Hunter 明确验证状态作为 Vendor 来源证据。
- Vendor 状态、品牌域名匹配、联系人身份和来源证据共同进入现有真实性策略。
- 只有 `authenticity_level=verified` 计入可靠邮箱。
- `probable`、`risky`、`unverified` 单独显示为待复核。
- `invalid`、`do_not_contact`、黑名单、一次性邮箱进入无效/禁止联系。
- 导出默认仅导出可靠邮箱；高级导出可选可靠+待复核或全部结果。

## 9. 可视化和交互要求

任务列表使用真实计数，不显示虚假阶段或估算成功率。详情页按导入顺序稳定展示，并支持分页与筛选。每家公司可展开查看 Vendor、各 Stage、联系人、邮箱分类和可操作错误原因。

必须覆盖 loading、empty、ready、running、partial、completed、failed、cancelled、offline、permission denied 状态。状态不能只用颜色表达；键盘焦点、语义标签、窄屏横向滚动和错误提示必须可用。

前端可沿用当前轮询，读取数据库聚合结果；若使用 SSE，只能作为传输优化，不能成为状态真相来源。

## 10. 安全、审计与运维

- 文件名、单元格内容和导出内容防公式注入；解析过程限制内存、行数和耗时。
- 不保存 API Key、Authorization、Token 或明文 Credential 到批次、Target、快照、事件或日志。
- 审计上传、确认、开始、暂停、继续、取消、重试和导出。
- 指标至少包括解析失败率、有效行率、Target 队列/执行耗时、Vendor 成功率、429、重试率、可靠邮箱率和任务成本。
- 配置 feature flag，支持关闭批量任务入口而保留历史读取。
- 回滚应用版本不得删除批次、目标、Stage 或结果；数据库 contract 删除只能在后续独立版本执行。

## 11. 分阶段实施

1. 模板下载、文件解析、规范化、预览和错误报告。
2. `BatchImport`、`ExactBrandTarget` additive migration、权限和 API。
3. 将现有精准品牌 Pipeline 按 Target 调度，加入幂等、恢复、预算和聚合状态。
4. 前端四步创建体验、任务可视化、失败项重试和导出。
5. 运行迁移演练、全量测试、构建、桌面/窄屏视觉检查和生产运行态验证。

## 12. 验收标准

- 用户只需上传文件、设置统一参数和开始执行。
- 模板不存在逐公司职位字段。
- 公司与域名逐行绑定，无跨行误配。
- 无效文件和预览不产生 Vendor 调用。
- 重复确认不产生重复任务，失败项重试不重复调用成功项。
- Apollo/Hunter 独立全流程和配置快照保持有效。
- 单目标失败不影响其他目标，父任务正确显示 partial。
- 刷新页面和 Worker 重启后恢复真实进度。
- 可靠邮箱数量严格来源于 `authenticity_level=verified`。
- 任务级和公司级进度、错误、Vendor 来源及结果可审计和导出。
- 现有单个精准品牌、品牌发现及历史任务行为不回归。
