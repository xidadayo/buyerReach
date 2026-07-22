# BuyerReach 多用户、组织与权限架构 V1

状态：后续版本核心架构基线
日期：2026-07-21
适用范围：商家发现、验证、自动化任务、邮件触达、CRM、监控、配置、凭证、导入导出和审计

## 1. 目标与原则

BuyerReach 必须支持同一组织内多人协作，并确保不同组织的数据、配置、凭证、预算和任务严格隔离。权限设计不能只决定“页面是否显示按钮”，还必须在 API、数据库查询、后台 Worker、定时任务、导出和事件消费处统一执行。

核心原则：

1. 默认拒绝，未明确授予的操作不可执行；
2. 先验证组织边界，再判断数据范围和动作权限；
3. 高风险操作采用职责分离和二次审批；
4. 后台自动任务代表组织执行，不继承已失效用户的无限权限；
5. 权限、预算、凭证和审批政策在 Run 创建时形成快照，但发送前仍重新检查实时安全禁令；
6. 用户停用、退出组织或权限撤销后，访问立即失效，历史责任关系和审计记录保留；
7. 前端权限提示用于改善体验，后端鉴权才是安全边界；
8. 不向无权用户泄露对象是否存在，跨组织访问统一返回安全的 `404` 或策略化 `403`。

## 2. 当前基础与能力缺口

当前系统已有：

- `Organization`、`User`、`Role` 基础模型；
- `organization_id` 组织隔离字段；
- `require_permission("resource:action")` API 依赖；
- `admin`、`operator`、`viewer` 预置角色；
- 用户停用检查和部分任务组织归属校验；
- 用户、角色和权限 JSON 管理页面。

后续版本不得推倒现有 RBAC。采用兼容扩展：

- 从“用户只有一个角色”演进到“用户可有多个组织成员身份和角色绑定”；
- 从组织级全量可见演进到本人、团队、指定对象、组织四级数据范围；
- 从同步 API 鉴权扩展到自动化 Run、审批、发送、凭证和导出的持续授权；
- 从自由编辑权限 JSON 演进到权限目录、角色模板和高级自定义界面；
- 保留旧 `User.role_id` 兼容读取，迁移完成后再停止写入。

## 3. 用户与组织模型

### 3.1 实体关系

```text
User
  -> OrganizationMembership
       -> Organization
       -> MembershipRole -> Role -> RolePermission -> Permission
       -> TeamMembership -> Team

Business Object
  -> organization_id
  -> owner_user_id
  -> owner_team_id
  -> optional ObjectGrant
```

### 3.2 账户和成员状态

`User.status`：

- `invited`：已邀请，尚未完成激活；
- `active`：可登录；
- `locked`：因安全策略临时锁定；
- `disabled`：平台级停用；
- `deleted`：软删除，禁止登录，审计保留。

`OrganizationMembership.status`：

- `invited`；
- `active`；
- `suspended`；
- `revoked`。

用户可属于多个组织，但每个访问 Token 必须包含一个当前 `organization_id`。切换组织时签发新 Token，不接受客户端通过请求参数任意覆盖组织。

### 3.3 团队

Team 用于销售组、市场组、数据组或地区组的数据范围和任务分配。V1 只支持单层团队，不引入无限组织树。业务对象可指定负责人和负责团队；一个用户可属于多个团队。

## 4. 授权模型：RBAC + 数据范围 + 对象授权

最终决策由以下条件共同决定：

```text
允许 = 身份有效
   AND 组织成员有效
   AND 拥有动作权限
   AND 满足数据范围
   AND 满足对象状态/业务政策
   AND 未命中实时安全禁令
```

### 4.1 动作权限

权限使用稳定的 `resource:action` 标识，不把角色名写入业务代码。例如：

- `automations:read/create/update/publish/pause/run/delete`；
- `automations:enable_guarded_auto`；
- `approvals:read/approve/reject/bulk_approve`；
- `outreach:read/draft/schedule/send/cancel`；
- `sending_accounts:read/use/manage`；
- `credentials:read_metadata/use/manage/rotate`；
- `customers:read/create/update/assign/export`；
- `contacts:read/read_sensitive/update/export`；
- `suppressions:read/create/request_release/approve_release`；
- `budgets:read/manage`；
- `users:read/invite/update/disable`；
- `roles:read/manage`；
- `audit:read/export`。

权限目录由代码和版本化迁移维护。自定义角色只能选择已登记权限，禁止保存未知或拼写错误的权限字符串。

### 4.2 数据范围

角色绑定同时携带 `data_scope`：

| 范围 | 可访问数据 |
|---|---|
| `self` | 本人创建或本人负责 |
| `team` | 本人所在团队负责 |
| `assigned` | 通过对象授权明确共享 |
| `organization` | 当前组织全部数据 |

多个角色的动作权限取并集；数据范围按资源计算并集，但永远不能跨组织。敏感字段权限不随普通 `read` 自动获得。

### 4.3 对象授权

`ObjectGrant` 只用于确有共享需求的自动化、客户列表、活动和 CRM 对象，字段至少包括：

- `organization_id`；
- `object_type`、`object_id`；
- `principal_type`：`user` 或 `team`；
- `principal_id`；
- `access_level`：`view`、`edit`、`operate`；
- `expires_at`；
- `granted_by`、`reason`。

不为每条品牌、联系人和邮箱建立 ACL。它们默认继承所属任务、客户列表或活动的数据范围，避免权限表无限膨胀。

### 4.4 字段级保护

以下数据必须独立授权和脱敏展示：

- 完整邮箱、电话和回复正文；
- Provider Credential、发送账户认证信息；
- 成本、预算和账单信息；
- 审计详情中的敏感载荷；
- 大批量导出文件。

`contacts:read` 只能查看脱敏联系方式；`contacts:read_sensitive` 才能查看完整值。Credential 读取接口永远不返回明文，只返回名称、用途、状态、所有者和尾部指纹。

## 5. 推荐角色模板

角色模板是初始建议，不是写死的业务判断：

| 角色 | 主要能力 | 默认数据范围 |
|---|---|---|
| Organization Owner | 组织、安全、角色、预算和全部业务管理 | organization |
| Organization Admin | 用户、团队、配置和业务管理；不能转移所有权 | organization |
| Automation Manager | 创建、发布、暂停自动化，查看运行与成本 | organization/team |
| Data Researcher | 商家发现、切片、验证和数据维护 | team |
| Outreach Manager | 活动、模板、审批和发送策略管理 | organization/team |
| Sales Operator | 处理分配客户、回复和 CRM 跟进 | self/team |
| Approver | 审批指定范围内的客户和邮件 | assigned/team |
| Analyst | 只读报表和脱敏数据 | organization/team |
| Auditor | 只读审计、政策和执行证据 | organization |
| Viewer | 只读被授权对象 | self/assigned |

必须至少保留一个有效 Organization Owner。最后一个 Owner 不得退出、停用或降级，除非先完成所有权转移。

## 6. 自动化任务的授权

### 6.1 创建、发布和运行

- 创建草稿需要 `automations:create`；
- 修改草稿需要 `automations:update` 和对象编辑范围；
- 发布新版本需要 `automations:publish`；
- 启用 `guarded_auto` 另需 `automations:enable_guarded_auto`；
- 立即运行需要 `automations:run`；
- 暂停和恢复需要 `automations:pause`；
- 提高预算、扩大国家/客户范围、降低质量门属于高风险变更，必须重新审批。

Automation Version 保存创建者、发布者、审批政策、允许动作、数据范围、预算上限、Credential 引用和授权快照版本。

### 6.2 服务身份

定时任务不能永久模拟创建者个人 Token。每个组织建立受治理的 `AutomationPrincipal` 服务身份：

- 只能执行 Automation Version 明确允许的动作；
- 只能使用已授权的 Credential、发送账户、客户范围和预算；
- 不允许管理用户、角色或解除 suppression；
- 组织、自动化或服务身份停用后禁止创建新 Run；
- 运行中每个高风险 Step 开始前重新检查服务身份和组织状态。

创建者离职不应让全部低风险数据复查任务无故失败，但组织可配置：转交 Owner、暂停等待认领或继续由服务身份运行。默认策略为暂停包含真实发送的自动化，数据发现与复查可继续。

### 6.3 权限变更对运行中任务的影响

- 普通只读或结果查看权限变化不修改已创建 Run；
- 发送、导出、提高预算、Credential 使用、解除 suppression 等动作执行前必须实时再授权；
- 自动化发布者失去发布权，不自动撤回历史版本，但禁止发布新版本；
- Automation Principal、发送账户或 Credential 被禁用时，相关 Step 转为 `blocked_authorization`，不得无限重试；
- 恢复授权后由有权用户确认继续，保留原幂等键。

## 7. 审批与职责分离

### 7.1 审批策略

`ApprovalPolicy` 至少包含：

- 适用动作和对象范围；
- 审批人数：一人或双人；
- 审批者角色/团队；
- 是否禁止申请人自批；
- 金额、收件人数、风险等级阈值；
- 超时、转交和失效策略；
- 策略版本。

### 7.2 必须分离的操作

默认要求申请人与审批人不同：

- 首次启用 `guarded_auto`；
- 大批量真实邮件发送；
- 提高组织或活动发送额度；
- 解除人工禁止、投诉或退订 suppression；
- 导出大量完整联系人信息；
- 新增或轮换生产 Credential；
- 修改角色、审批政策或组织安全配置。

Approval 必须绑定不可变对象版本和摘要。对象内容、收件人范围、模板、预算或发送账户发生实质变化后，旧审批自动失效。

### 7.3 防止权限自我提升

- 角色管理员不能授予自己当前不拥有的权限；
- 非 Owner 不能创建等价于 Owner 的角色；
- 用户不能批准自己的提权请求；
- 修改角色后撤销相关用户会话，强制重新获取权限；
- 权限缓存必须带 `authorization_version`，版本变化立即失效。

## 8. 数据模型

新增或扩展以下表：

### `organization_membership`

- `id`、`organization_id`、`user_id`；
- `status`、`joined_at`、`revoked_at`；
- `authorization_version`；
- 唯一键：`organization_id + user_id`。

### `team` / `team_membership`

- Team：`organization_id`、`name`、`status`；
- Membership：`team_id`、`membership_id`；
- 全部外键必须验证属于同一组织。

### `permission` / `role_permission`

- Permission：稳定 `code`、资源、动作、敏感等级、状态；
- RolePermission：角色和权限映射；
- Role 增加 `organization_id`、`is_system_template`、`version`。

### `membership_role`

- `membership_id`、`role_id`、`data_scope`；
- 可选 `resource_scope`；
- `valid_from`、`valid_until`。

### `object_grant`

按 4.3 定义，并建立组织、对象和 Principal 组合索引。

### `automation_principal`

- `organization_id`、`automation_id`；
- `status`、`permission_snapshot`、`authorization_version`；
- `created_by`、`disabled_by`、时间戳。

### `authorization_audit`

- actor、organization、action、resource、object；
- decision：`allowed`、`denied`；
- reason_code、policy_version、request_id、trace_id；
- 高风险操作保存 before/after 的脱敏摘要；
- 禁止记录 Token、密码、API Key 或完整 Credential。

所有业务核心表必须具有不可为空的 `organization_id`。历史空值通过可恢复回填任务修复；完成前 API 默认拒绝访问无法确定组织的数据。

## 9. API 与 Worker 执行规范

### 9.1 统一授权上下文

API 和 Worker 共用 `AuthorizationContext`：

```text
actor_type, actor_id
organization_id, membership_id
role_ids, permission_codes
team_ids, data_scopes
authorization_version
request_id / trace_id
```

统一 `authorize(context, action, resource, object)` 返回允许/拒绝和稳定原因码。路由不得只比较角色名，服务不得自行拼接另一套权限判断。

### 9.2 查询约束

- Repository/Query Service 强制接收 `organization_id`；
- 列表查询在数据库层加入数据范围过滤；
- 单对象查询同时匹配 ID 和 organization_id；
- 更新和删除采用带组织条件的原子 SQL，防止检查后对象被替换；
- 搜索、统计、报表、SSE、导出、对象存储下载和 Outbox 消费同样执行组织隔离；
- 禁止先读取全量数据再在 Python 内过滤。

### 9.3 错误码

- `AUTHENTICATION_REQUIRED`；
- `MEMBERSHIP_INACTIVE`；
- `PERMISSION_DENIED`；
- `DATA_SCOPE_DENIED`；
- `APPROVAL_REQUIRED`；
- `SEPARATION_OF_DUTIES`；
- `AUTHORIZATION_CHANGED`；
- `SERVICE_PRINCIPAL_DISABLED`。

前端将错误码翻译为明确下一步，不显示内部策略表达式。

## 10. 配额、成本和凭证权限

权限不等于无限额度。执行外部调用还必须同时满足：

- 组织总预算；
- 团队预算；
- 自动化/活动预算；
- 用户人工操作额度；
- Provider、Credential、发送账户和域名限制。

Credential 使用采用授权引用：业务对象只保存 `credential_id`，用户需要 `credentials:use` 且 Credential 必须允许对应 Provider、组织、动作和环境。前端永不读取明文。所有测试、轮换、禁用和使用均审计。

## 11. 前端体验

### 11.1 最少操作

- 新成员选择“岗位模板 + 数据范围”即可完成授权；
- 高级自定义权限折叠展示，不要求普通管理员编辑 JSON；
- 创建自动化时自动推荐最小所需权限和审批模式；
- 若用户无权完成下一步，页面直接显示负责人或“申请权限/提交审批”；
- 批量操作前显示可操作、需审批和无权限数量。

### 11.2 权限状态

页面必须覆盖：

- 无页面权限；
- 有页面权限但无某对象数据范围；
- 可查看但敏感字段已脱敏；
- 可编辑但不可发布/发送；
- 等待审批；
- 权限在操作期间被撤销；
- 自动化因授权、Credential 或预算被阻塞；
- 用户或团队已停用但历史负责人仍可识别。

不能只隐藏按钮。禁用状态应说明原因和获得权限的方法；安全敏感对象可完全隐藏。

### 11.3 多人并发

- Role、Automation Version、Approval Policy 和预算配置使用 `version`/ETag 乐观锁；
- 冲突时展示最新修改人和时间，允许重新载入，不静默覆盖；
- 审批使用原子状态转换，重复点击只产生一个结果；
- 客户负责人变更、批量分配和权限变更写入 Outbox；
- 用户正在查看的数据被撤权后，下一次请求/SSE 重连立即停止返回数据。

## 12. 审计、隐私与运维

必须审计：

- 登录、失败登录、组织切换和会话撤销；
- 邀请、停用、角色和团队变化；
- 权限允许/拒绝的高风险决策；
- 自动化发布、启停、模式变化和人工接管；
- 审批、发送、导出、Credential 使用和 suppression 变化；
- 预算和安全政策变化。

指标至少包括：

- 按组织的 401/403/数据范围拒绝率；
- 角色和 Owner 异常变化；
- 高风险权限使用次数；
- 审批等待和超时；
- 因授权变化被阻塞的 Automation Step；
- 跨组织访问尝试；
- 敏感数据导出量。

审计保留期由组织政策管理，高风险安全审计默认长期保留。用户删除采用软删除/匿名化策略，不破坏历史业务和责任链。

## 13. 迁移与兼容

采用 `expand -> backfill -> dual-read -> switch -> contract`：

1. 新增 Membership、Team、Permission、Role Binding、Grant 和服务身份表；
2. 为每个现有 User 建立当前 Organization Membership；
3. 将现有 `role_id` 转为 MembershipRole，保留旧字段读取；
4. 将现有角色权限 JSON 映射到 Permission 目录，未知权限进入迁移报告，不自动扩大权限；
5. 为历史业务对象回填 organization_id、owner_user_id 和默认数据范围；
6. API 进入 dual-read，比较旧/新授权决策但以旧规则执行；
7. shadow 指标稳定后切到新授权器；
8. 兼容窗口结束后停止写旧 role_id，后续独立版本再删除旧字段。

回滚只切回旧授权读取路径，不删除新表、绑定或审计。任何回滚都不能放宽 suppression、Credential 和跨组织边界。

## 14. 分阶段开发

### Access Foundation 1

- Permission Catalog、Membership、统一 AuthorizationContext；
- 兼容现有 admin/operator/viewer；
- 所有新自动化 API 强制组织隔离；
- 权限决策审计和跨组织测试。

### Collaboration 1

- Team、数据范围、负责人和对象授权；
- 岗位角色模板；
- 前端权限矩阵和冲突提示。

### Automation Authorization 1

- Automation Principal；
- Run 授权快照和高风险 Step 实时复核；
- 权限撤销、创建者离职、认领和转交流程。

### Approval and Sensitive Data 1

- 版本化 Approval Policy、双人审批和禁止自批；
- 字段脱敏、Credential 使用授权、受控导出；
- guarded_auto 权限门。

### Enterprise Access 1

- SSO/MFA/SCIM 等企业身份能力；
- 临时权限和定期权限复核；
- 更细的审计留存和安全告警。

SSO/SCIM 不作为自动化第一期前置条件，但数据模型不得阻止后续接入。

## 15. 验收场景

1. A 组织用户无法通过列表、ID 猜测、SSE、导出或对象存储链接读取 B 组织数据；
2. Viewer 看不到完整邮箱，获得 `contacts:read_sensitive` 后才显示；
3. Team 范围用户只能访问本人团队对象；
4. 自定义角色不能保存未知权限，也不能授予创建者自身没有的权限；
5. 最后一个 Owner 不能被停用或降级；
6. 发布自动化和启用 guarded_auto 分别执行独立权限检查；
7. 申请人不能审批自己的高风险自动发送请求；
8. 审批后的模板或收件人发生变化，旧审批自动失效；
9. 用户停用后现有 Token 立即不能访问，新自动化 Run 不再以该用户启动；
10. 创建者离职后，数据复查任务按组织政策继续或等待认领，真实发送默认暂停；
11. Automation Principal 被禁用时 Step 进入 `blocked_authorization`，不调用 Provider；
12. Credential 权限撤销后，即使 Run 有旧快照也不能发起新外部调用；
13. suppression、退订和投诉不能被普通管理员绕过；
14. 两位管理员同时编辑角色时，后提交者收到版本冲突而非覆盖；
15. 两位审批者同时处理同一请求只产生一次合法转换；
16. 数据范围变更后列表、详情、搜索、统计和导出结果一致；
17. 权限拒绝有稳定原因码，前端给出可操作提示且不泄露对象信息；
18. 旧用户和角色经迁移后权限不扩大，旧数据可正常访问；
19. downgrade 应用后旧授权路径可恢复，新审计数据不丢失；
20. 权限相关日志、事件和审计中不存在 Token、密码、API Key 或完整 Credential。

## 16. 设计门结论

- 产品：通过。岗位模板、数据范围和审批待办减少用户配置负担。
- 架构：通过。Identity & Access 统一授权，业务模块只声明资源、动作和对象。
- 数据：通过。组织归属、责任关系、审计和兼容迁移已定义。
- 可靠性：通过。后台服务身份、授权版本、阻塞恢复和并发冲突已定义。
- 演进：通过。权限目录、角色模板、政策和接口可版本化扩展。
- 运维：通过。高风险指标、审计、回滚和异常检测已定义。
- 安全：通过设计门。默认拒绝、租户隔离、职责分离、字段保护和防提权已覆盖。
- 体验：通过设计门。安全默认、权限原因、申请入口和多人冲突提示已覆盖。
- 验证：待实现证明。本文是开发基线，不代表多组织高级权限已经上线。

