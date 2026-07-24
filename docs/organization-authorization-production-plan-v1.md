# BuyerReach 组织架构与操作级权限最终技术方案 v1

状态：实施基线（当前仅完成方案，不包含代码修改或 NAS 发布）
适用范围：BuyerReach Web、API、Worker、数据库、管理后台和审计
兼容基线：数据库迁移 `20260722_0035`，Git `9c68286`

## 1. 目标与不可破坏约束

本次改造将现有“Organization 租户隔离 + Role 粗粒度权限”升级为：

1. `Organization`：公司/租户安全边界。
2. `OrganizationUnit`：公司内部可无限分级的组织单元树。
3. `Role`：授予具体业务操作，不承载组织关系。
4. `DataScope`：定义角色在每个业务资源上可访问的数据范围。
5. `AuthorizationPolicy`：后端唯一授权决策入口。

必须满足：

- 跨 Organization 永远不可见，超级管理员也只能通过明确的平台管理入口访问。
- 前端显隐只是体验优化，所有 API、Worker 管理动作和导出必须在后端重新鉴权。
- “能看页面”“能看数据”“能执行动作”彼此独立。
- 拒绝跨边界访问时返回 404，避免暴露记录是否存在；权限不足但资源未指定时返回 403。
- 角色不能授予超过操作者自身的操作权限或数据范围。
- 任务及其异步结果继承任务创建时的组织、组织单元和负责人，不随用户后续调岗而漂移。
- Provider/API Key、平台设置和审计属于组织级受控资源，不使用普通业务数据范围。

## 2. 用户体验

系统配置新增“组织与权限”区域，按以下顺序操作：

1. 管理员建立组织单元树，并指定部门主管。
2. 创建角色，勾选具体操作，按资源选择数据范围。
3. 创建用户时只需选择组织单元、角色和状态；职位不是权限输入。
4. 用户登录后仅显示至少拥有一个可用操作的菜单、标签页和按钮。
5. 列表为空时区分“当前范围暂无数据”和“没有查看权限”，不显示通用 403 弹窗。

组织页面采用左侧树、右侧详情：部门详情展示主管、直属用户、子部门和状态。移动、停用、删除为高风险操作，必须说明影响并二次确认。

## 3. 数据模型

### 3.1 Organization

继续作为租户根，不表示部门。保留现有主键和名称，新增可选字段：

- `code`：租户内稳定代码。
- `timezone`：审计和任务展示时区。
- `status`：`active | disabled`。

### 3.2 OrganizationUnit

新增表 `organization_unit`：

| 字段 | 约束 | 用途 |
|---|---|---|
| `id` | UUID PK | 组织单元 ID |
| `organization_id` | FK, not null | 租户边界 |
| `parent_id` | self FK, nullable | 根节点为空 |
| `name` | not null | 显示名称 |
| `code` | not null | 租户内稳定编码 |
| `unit_type` | enum | `company/division/department/team` |
| `manager_user_id` | FK, nullable | 主管，仅用于组织信息，不自动授予权限 |
| `path` | string, indexed | 物化路径，例如 `/root/sales/team-a/` |
| `depth` | integer | 层级 |
| `sort_order` | integer | 同级排序 |
| `status` | enum | `active/disabled` |
| `version` | integer | 乐观锁，防止并发移动覆盖 |

唯一约束：`(organization_id, parent_id, normalized_name)`、`(organization_id, code)`。禁止跨租户父子关系、循环引用、把节点移动到自身子树。

### 3.3 User

- 新增 `organization_unit_id`，FK 到 `organization_unit.id`。
- 兼容期保留 `department_id`，双读但只写 `organization_unit_id`。
- 一个用户只有一个主组织单元；跨部门协作通过角色授权，不通过多重部门归属。
- 用户调岗只改变后续新建数据的默认归属，不改写历史任务和历史数据。

### 3.4 Role 与数据范围

Role 新增：

- `organization_id`：系统角色可为空，组织自定义角色必须属于一个租户。
- `is_system`：系统角色不可删除或改名，只允许受控升级权限定义。
- `status`：`active/disabled`。
- `permission_version`：当前为 `1`。
- `permissions`：操作权限集合。
- `data_scopes`：按资源保存数据范围。

数据范围枚举：

- `self`：`owner_id == current_user.id`。
- `unit`：当前组织单元。
- `unit_and_children`：当前单元及全部有效子单元。
- `organization`：当前租户全部数据。
- `all`：平台管理专用，不提供给普通组织角色。

`data_scopes` 示例：

```json
{
  "tasks": "unit_and_children",
  "brands": "unit_and_children",
  "contacts": "unit",
  "emails": "self",
  "audit": "organization"
}
```

未配置资源范围时默认 `self`，而不是扩大到 organization。

### 3.5 业务数据归属

所有需要隔离的实体统一使用现有 `organization_id / department_id / owner_id`：

- 任务：创建者、创建时组织单元、租户。
- 公司、品牌、联系人、邮箱：首次创建时确定归属；由任务产生时继承任务归属。
- 批量导入和精准品牌 Target：继承批次创建者和组织单元。
- TaskItem、StageRun、Evidence 等子记录通过父任务授权，不单独开放直接范围查询。
- AuditLog 新增 `organization_id`、`organization_unit_id`、`actor_id` 和 `request_id` 索引。

## 4. 权限目录 v1

权限键统一为 `resource:action`，`admin:*` 仅保留给平台超级管理员。`write` 进入兼容期，不再作为新角色界面选项。

| 资源 | 操作 |
|---|---|
| `tasks` | `read/create/update/start/pause/resume/cancel/retry/delete/export` |
| `brands` | `read/create/update/review/archive/delete/export/promote` |
| `contacts` | `read/create/update/delete/bulk_delete/export` |
| `emails` | `read/create/update/verify/bulk_verify/delete/export` |
| `imports` | `read/preview/execute/retry/cancel` |
| `dedup` | `read/execute/merge` |
| `blacklist` | `read/create/update/delete` |
| `tags` | `read/create/update/delete/assign` |
| `custom_fields` | `read/create/update/delete/assign` |
| `providers` | `read/create/update/enable/disable/test/delete/read_usage` |
| `settings` | `read/update` |
| `organizations` | `read/update` |
| `organization_units` | `read/create/update/move/disable/delete/assign_manager` |
| `users` | `read/create/update/enable/disable/reset_password/move_unit/assign_role` |
| `roles` | `read/create/update/clone/delete/assign` |
| `audit` | `read/export` |
| `exports` | `execute` |

规则：删除、批量操作、导出、密钥测试、角色分配和密码重置必须单独授权，不能由 `update` 隐式获得。

### 4.1 兼容映射

迁移期间旧权限按以下方式展开，仅用于读取旧角色：

- `*:read -> read`
- `tasks:write -> tasks:create + tasks:update`
- `tasks:execute -> start + pause + resume + cancel + retry`
- 其他 `resource:write -> create + update + delete`
- `import:execute -> imports:read + preview + execute`
- `export:execute -> exports:execute`
- `emails:verify -> emails:verify + emails:bulk_verify`

新写入只保存 v1 权限，完成兼容窗口后再移除旧映射。

## 5. 后端授权架构

新增 `app/authz/`：

- `catalog.py`：权限目录、版本和兼容映射。
- `context.py`：`AuthorizationContext(user, organization, unit, permissions, scopes)`。
- `policy.py`：`authorize(operation, resource)` 和资源级访问决策。
- `scope.py`：把 DataScope 编译为 SQLAlchemy 条件。
- `hierarchy.py`：组织树祖先/后代查询、移动校验。
- `errors.py`：统一 401/403/404 错误语义。

禁止在 Router 和 Service 中继续散落字符串比较 `user.organization_id == entity.organization_id`。统一调用：

```python
context = authorization_context(db, user)
authorize(context, "tasks:start")
task = load_scoped_entity(db, SearchTask, task_id, context, resource="tasks")
```

列表查询调用：

```python
statement = apply_scope(statement, SearchTask, context, resource="tasks")
```

批量操作先获取作用域内 ID，再执行更新；请求中混入越权 ID 时整批拒绝，不允许静默跳过。

### 5.1 Worker 和异步任务

Worker 不使用当前登录用户重新判断数据范围，而使用任务中已冻结的：

- `organization_id`
- `department_id`
- `owner_id`
- `authorization_snapshot_version`

用户后续被停用不会中断已运行任务；但启动、恢复、重试等新控制操作必须使用当前权限。管理员取消任务仍写入实际 actor。

### 5.2 缓存

首版不缓存权限决策，数据库为真相。后续若缓存，缓存键必须包含 `user_id + role.updated_at + unit.version + permission_version`，权限变更后立即失效。

## 6. API 合同

新增：

- `GET /organization-units/tree`
- `POST /organization-units`
- `PATCH /organization-units/{id}`
- `POST /organization-units/{id}/move`
- `POST /organization-units/{id}/disable`
- `DELETE /organization-units/{id}`
- `GET /permissions/catalog`
- `GET /auth/me` 增加 `organization_unit`、`permissions_version`、`data_scopes`

Role Create/Update 接收 `permissions` 和 `data_scopes`。User Create/Update 使用 `organization_unit_id`。API 只做增量字段变更，旧客户端字段在兼容期继续可读。

组织单元删除条件：无有效子节点、无用户、无业务数据；否则返回 409 并给出阻塞数量。停用节点后禁止新增用户和创建新数据，但历史数据仍可读。

## 7. 前端方案

### 7.1 组织管理

- 左侧可搜索组织树；右侧详情、直属成员和子部门。
- 只有拥有相应操作权限时显示新增、移动、停用、删除、设置主管。
- 移动时禁止选择自身和后代；展示将受影响的用户数量。
- 窄屏切换为组织列表进入详情，不横向压缩树。

### 7.2 角色编辑

- 按业务模块分组，每项展示具体操作复选框。
- 每个包含业务数据的资源单独选择数据范围。
- 提供“只读人员、业务人员、部门主管、组织管理员”模板，但保存后是普通角色快照。
- 不允许选择超过当前用户的权限；不可选项显示原因。

### 7.3 用户管理

- 新增用户只填写姓名、邮箱、密码、组织单元、角色和状态。
- 不需要按用户配置不同职位规则。
- 角色变化或调岗前显示影响摘要。
- 低权限用户看不到高权限用户；用户详情也必须后端 404。

### 7.4 全局操作显隐

建立统一 `v-permission` 指令或 `PermissionGate` 组件，替换页面内重复判断。路由只判断进入模块的最小 read 权限；每个按钮判断对应操作权限。服务端返回 403 时使用中文业务提示，跨范围 404 不暴露实体存在性。

## 8. 审计、安全和隐私

以下行为必须记录 actor、租户、组织单元、目标、前后值、请求 ID 和时间：

- 组织节点创建、移动、停用、删除、主管变化。
- 角色权限或数据范围变化。
- 用户创建、调岗、角色变化、启停、密码重置。
- Provider 启停、测试、删除和密钥更新（只记录“已更新”，不记录密钥）。
- 批量删除、导出、合并、任务控制。

权限拒绝日志只记录权限键、资源类型、目标 ID 哈希和原因，不记录联系人敏感数据或 Credential。

## 9. 数据迁移

采用 expand → backfill → switch → contract：

1. `0036`：新增 organization_unit、Role 扩展字段、User.organization_unit_id、AuditLog 归属字段和索引；全部新增字段先可空。
2. 为每个 Organization 幂等创建根节点“总部”。
3. 将现有 User.department_id 映射到可识别节点；无法识别的一律回填根节点并记录迁移计数。
4. 将现有业务实体缺失的 department_id/owner_id 按任务或创建者回填；无法确定的归根节点，owner 可空。
5. 将旧角色权限转换成 v1 操作权限并写入默认 DataScope：admin=organization、operator=organization、viewer=organization，保持当前可见性不突然缩小。
6. 部署双读代码，观察无空归属后切换只读新字段。
7. 后续独立 contract 版本才移除旧 department_id 语义和 write 映射。

迁移必须可重复执行，并输出每张表的总数、成功数、回退根节点数和失败数。

## 10. 实施阶段

### 阶段 A：权限内核与迁移

- 数据模型、迁移、目录、Scope 编译器、组织树算法。
- Auth Profile 增量字段。
- 单元测试覆盖租户、本人、部门、子树和全组织。

### 阶段 B：管理纵向切片

- 组织单元、角色、用户 API 和界面。
- 权限升级限制、组织移动限制、审计。

### 阶段 C：业务 API 全覆盖

- 逐个替换任务、品牌、联系人、邮箱、导入导出、审核与配置接口。
- 建立“API 路由—权限键—数据范围—前端入口”清单，CI 检查所有非公开路由均声明权限。

### 阶段 D：前端全覆盖与兼容收口

- PermissionGate、路由、菜单、按钮和空状态。
- 移除新界面对 write 的使用，保留后端旧客户端映射。

### 阶段 E：本地验收

- 仅本地迁移、测试、视觉检查和运行验证。
- 未经用户再次明确授权，不提交 NAS 更新。

## 11. 测试与验收

必须建立权限矩阵参数化测试，至少覆盖：

- 两个租户、两级以上部门树、同级部门、上下级部门、本人和他人。
- 角色有权限/无权限、范围不足、目标不存在、已停用用户/组织单元。
- 单条与批量查看、更新、删除、导出、验证和任务控制。
- 主管不能创建超权角色，不能给用户分配超权角色或更大 DataScope。
- 调岗前历史任务归属不变，调岗后新任务使用新组织单元。
- Worker 继续完成已授权并启动的任务，未授权用户不能恢复或重试。
- 所有越权查询不返回数量、名称或其他侧信道信息。
- 前端无权页面不显示，无权按钮不渲染，直接构造请求仍被后端拒绝。

执行验证：

```text
Backend:  python -m pytest -q
Static:   python -m ruff check app tests migrations
Compile:  python -m compileall -q app migrations
Frontend: pnpm exec vue-tsc --noEmit
Build:    pnpm run build
Migration: upgrade head -> downgrade 0035 -> upgrade head
Runtime:  /health, /ready, Worker/Beat logs, migration revision, Outbox backlog
Visual:   桌面与窄屏检查组织树、角色矩阵、用户表单和无权限状态
```

## 12. 本地发布门槛、回滚与 NAS 边界

本地完成标准：测试与构建全部通过；现有数据库升级成功；旧用户可登录；权限矩阵没有跨组织或跨范围泄露；工作区无密钥和 dump。

回滚：应用可回滚到上一提交；由于 `0036` 为纯增量迁移，旧应用忽略新增表和字段。只有确认没有新应用写入依赖后才允许 downgrade；不得自动删除组织关系或审计数据。

本方案阶段明确禁止 NAS 操作。后续只有在用户再次明确要求更新 NAS 后，才执行：生产加密备份、镜像传输、迁移、应用替换、健康检查和权限冒烟测试。现有 `smu-kno-test` 始终保持隔离。

## 13. 完成定义

只有同时满足以下条件才能称为“权限系统完成”：

- 权限目录覆盖所有非公开 API 和所有前端操作入口。
- 每个业务查询均执行租户与 DataScope 过滤。
- 组织、用户、角色和数据归属均有数据库约束与审计。
- 迁移、兼容、回滚和旧数据读取通过验证。
- 前端完成桌面、窄屏、键盘和权限状态检查。
- 未发现跨租户、跨部门、批量操作绕过或高权限角色越权分配。
