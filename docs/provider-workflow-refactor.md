# Provider Workflow 重构实施清单

更新时间：2026-07-16 (本次审计)

## 状态定义

- `pending`：尚未开始。
- `in_progress`：正在实施，未通过全部验证。
- `completed`：代码、数据库和测试均已验证。
- `blocked`：存在无法在当前环境解决的外部阻塞。

## 开始前审计（2026-07-16 重新验证）

| 项目 | 状态 | 核验结果 |
| --- | --- | --- |
| 读取项目规则和 automation memory | completed | AGENTS.md 已更新为新规则；automation memory 不存在，已创建 |
| 检查模型、迁移、API、Adapter、配置页和测试 | completed | 四张新表 + ProviderConfig 共 30 张表；Alembic head=20260715_0020 |
| 检查 Docker、数据库迁移和 API | completed | 全部容器运行正常；前后端 HTTP 200；Celery Worker/Beat 在线 |
| 检查未提交修改 | completed | 非 Git 仓库，无冲突风险 |

## 实施阶段

| 阶段 | 状态 | 验收依据 |
| --- | --- | --- |
| 1. 完成度审计和持久化清单 | completed | 本文档 + 自动化 memory |
| 2. Vendor Adapter 接口和标准模型 | completed | `VendorWorkflowAdapter` 数据类 + `ProviderResult` + 代码注册表 `WORKFLOW_ADAPTERS` |
| 3. Apollo 全流程 Adapter | completed | Organization Search → People Search → Bulk Enrichment → 邮件/姓名合并；19 个测试覆盖 |
| 4. Hunter 全流程 Adapter | completed | Discover/Domain Finder → Domain Search → Email Finder → Email Verifier；全部端点路径由代码配置 |
| 5. Prospeo 全流程 Adapter | completed | Search Company → Search Person → Enrich Person；请求体和字段映射全部内聚 |
| 6. ZeroBounce 验证 Adapter | completed | 仅邮箱验证 + 账户额度检查；`verification_only=True` 标记 |
| 7. Vendor Credential 和 Strategy | completed | 加密存储、掩码返回("********")、空值保留、连接测试、主备策略 API |
| 8. 任务计划快照和阶段 checkpoint | completed | `TaskVendorPlan` 不可变快照 + `TaskStageCheckpoint` 带唯一约束；幂等复用 |
| 9. 失败切换和断点续跑 | completed | `execute_provider_waterfall` 按 task vendor plan 顺序尝试；失败 stage 记录 error_code/error_message 后切换 |
| 10. 迁移现有加密 API Key | completed | Migration 0019 从 ProviderConfig 提取去重，4 个 Vendor 各一条 Key |
| 11. 替换系统配置页面 | completed | SettingsView.vue Provider Tab 仅显示 API Key / 启用 / 连接测试 / 主备策略 |
| 12. 双读验证新旧结果 | in_progress | 新路径已启用；**尚未执行消耗额度的真实结果对比** |
| 13. 切换生产执行路径 | completed | `execute_search_task` → `execute_provider_waterfall` + `_enrich_contacts_with_apollo` checkpoint |
| 14. 删除旧 Provider 配置 | blocked | 需用户先触发真实任务验证结果，再删除旧表/API/隐藏表单/Catalog 兼容代码 |
| 15. 完整测试、构建、部署 | completed | **117 测试全部通过**；前端生产构建正常；Alembic head；Docker 健康检查全部通过 |

## 本轮验证结果（2026-07-16）

### 代码验证

- `backend/app/providers/workflows.py`：4 个 VendorWorkflowAdapter 实例（APOLLO, HUNTER, PROSPEO, ZEROBOUNCE），接口地址/认证/映射全部由代码配置
- `backend/app/providers/vendors.py`：execute_vendor_provider 分发 apollo/hunter/zerobounce + 通用 Catalog；check_vendor_provider_quota 支持所有 adapter
- `backend/app/modules/models.py`：VendorCredential, VendorStrategy, TaskVendorPlan, TaskStageCheckpoint 四张新表 + ProviderConfig 保留
- `backend/app/modules/services.py`：execute_provider_waterfall, ensure_task_vendor_plan, _stage_checkpoint, _start_checkpoint, _complete_checkpoint, _fail_checkpoint 均已实现
- `backend/app/core/crypto.py`：encrypt_secret/decrypt_secret 支持 VendorCredential 独立加密
- `backend/app/api/v1/router.py`：vendor-credentials CRUD + test, vendor-strategy GET/PUT, task checkpoints GET
- `frontend/src/views/SettingsView.vue`：Provider 页面简化为 API Key + 启用 + 连接测试 + 主备策略；旧 ProviderConfig 编辑对话框保留但 `v-if="false"` 隐藏

### 数据库验证

- 30 张表全部存在，包括 vendor_credential (4 rows), vendor_strategy (1 row), task_vendor_plan, task_stage_checkpoint
- Alembic 版本：20260715_0020 (head)
- Migration 0019：创建四张新表 + 从 ProviderConfig 迁移 API Key
- Migration 0020：清理旧 ProviderConfig 中的本地额度/熔断字段

### 测试验证

- **117 tests passed** in 11.58s（Python 3.14.5, pytest 9.1.1）
- 覆盖：API Key 加解密、空值保留、task vendor plan 不可变、checkpoint 幂等复用、失败阶段切换 fallback、Apollo/Hunter/Prospeo/ZeroBounce 各 adapter 请求响应、quota check、waterfall

### 服务验证

- Postgres: healthy（端口 15432）
- Redis: healthy（端口 16379）
- Backend: HTTP 200（端口 8000）
- Frontend: HTTP 200（端口 5173）
- Celery Worker: 1 node online, ping OK
- Celery Beat: running

### 未覆盖的测试要求（需要真实第三方调用）

以下测试项因涉及消耗付费额度，尚未执行：
- 主平台无额度后切换备用平台（真实额度接口）
- 403 后切换平台（真实请求）
- 429 和 Retry-After 处理（真实限流响应）
- TLS 和网络超时切换（模拟已有）
- 联系人阶段失败后从联系人阶段继续
- 邮箱阶段失败后从邮箱阶段继续
- Worker 重启后断点续跑
- 历史 API Key 自动迁移（已在 migration 0019 中覆盖，但未在真实 DB 验证）

## 不变约束

- 保持现有公司、品牌校验、官网解析、联系人、邮箱、验证、去重入库和任务状态流程。
- API Key 只从加密凭据读取；前端留空保存保留旧密钥。
- 接口地址、认证协议、分页、映射和 Vendor 多步骤调用全部由代码 Adapter 维护。
- 403、429、额度不可用、网络/TLS、异常响应和无结果均允许从当前失败阶段切换。
- 恢复时间只接受第三方额度接口或响应 Header；不设置系统固定熔断时间，不显示本地推算额度。
- 未经用户明确触发，不执行消耗第三方额度的真实搜索。

## 下一步

1. **用户明确启动一个真实精准品牌任务**，核对新路径结果与历史结果（阶段 12 双读验证）。
2. 确认 checkpoint 恢复和平台切换的生产记录后，**删除旧 ProviderConfig、旧 API、隐藏高级表单和通用 Catalog 兼容实现**（阶段 14）。
