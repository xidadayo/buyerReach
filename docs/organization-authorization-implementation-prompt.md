# BuyerReach 组织架构与操作级权限实施 Prompt

> 将本文完整交给 VSCode 中的 Codex、Claude Code、Cursor Agent 或其他具备终端与文件编辑能力的编程 Agent。不得只生成建议或伪代码，必须在本地仓库完成实现和验证。

你正在维护 BuyerReach，仓库路径为 `D:\buyer reach`。

## 一、最终目标

严格按照以下唯一需求文档，实现完整的组织架构和操作级权限系统：

- `docs/organization-authorization-production-plan-v1.md`

该文档是本任务的产品、架构、数据模型、权限目录、迁移、前端体验、测试和完成标准的唯一权威来源。不得自行删减权限项、弱化数据范围、跳过迁移兼容或用前端显隐代替后端鉴权。

本任务只允许在本地完成。**禁止连接、登录、上传、修改或部署 NAS，禁止操作 NAS 上的 BuyerReach 和 `smu-kno-test`。** 完成本地实现后等待用户另行明确授权部署。

## 二、开始前必须执行

1. 阅读并遵守：
   - `AGENTS.md`
   - `docs/DEVELOPMENT_RULES.md`
   - `docs/pipeline-production-architecture-v1.md`
   - `docs/production-integrity-audit-2026-07-17.md`
   - `docs/organization-authorization-production-plan-v1.md`
2. 执行 `git status --short --branch`，保留所有现有修改和未跟踪文件，不得 reset、checkout、覆盖或清理用户工作。
3. 审计当前完整链路：
   - Organization、Role、User、OwnershipMixin 和 AuditLog 模型；
   - 登录、`/auth/me`、`require_permission`、`require_task_access`；
   - 所有 `/api/v1` 非公开路由；
   - Service 中所有列表、详情、写入、批量、导入、导出和任务控制查询；
   - Worker、Celery Beat、Pipeline、Task 快照和异步结果归属；
   - 前端路由、菜单、页面、按钮、批量操作、设置页和 auth store；
   - 现有权限测试和最新 Alembic head。
4. 先建立实施计划和 API 权限覆盖清单，再修改代码。计划必须覆盖后端、迁移、前端、测试和本地运行验收。

## 三、不可妥协的架构规则

### 1. 四层授权模型

必须实现：

1. `Organization`：租户安全边界，不表示部门。
2. `OrganizationUnit`：公司内部组织树。
3. `Role.permissions`：逐操作功能权限。
4. `Role.data_scopes`：按资源设置 `self | unit | unit_and_children | organization | all`。

不得把组织层级硬编码到角色名称，不得让“主管”身份自动获得权限，不得让一个 `write` 永久代表创建、修改、删除和批量操作。

### 2. 后端为最终边界

- 所有非公开 API 必须显式声明权限。
- 所有列表和详情查询必须同时执行 Organization 与 DataScope 过滤。
- 前端按钮隐藏不能代替 API 校验。
- 指定实体越过租户或数据范围时返回 404；缺少通用操作权限时返回 403。
- 批量请求只要包含一个越权 ID，整批拒绝，不得静默跳过。
- 导出、删除、批量操作、密码重置、角色分配、Provider 测试必须单独授权。

### 3. 集中授权策略

按方案创建 `backend/app/authz/`，至少包含：

- `catalog.py`
- `context.py`
- `policy.py`
- `scope.py`
- `hierarchy.py`
- `errors.py`

统一提供授权上下文、权限判断、实体加载和 SQLAlchemy Scope 编译。完成后不得继续新增散落的 `user.organization_id == entity.organization_id` 或页面级临时权限规则。对遗留比较逐步替换，确保所有业务路由均进入统一策略。

### 4. 数据归属

- 新建任务、品牌、公司、联系人、邮箱和批次必须写入 `organization_id`、组织单元 ID 和 `owner_id`。
- Pipeline 派生数据继承父任务归属。
- 历史任务归属不能因用户调岗而改变。
- Worker 使用任务冻结归属继续执行；启动、暂停、恢复、取消、重试等控制操作使用当前权限。

### 5. 权限目录

完整实现方案第 4 节的 v1 权限目录。新角色界面和新 API 只写 v1 权限。旧 `write`、`tasks:execute`、`import:execute`、`export:execute` 和 `emails:verify` 按方案兼容映射，不得导致升级后现有用户突然失去权限。

## 四、数据库与迁移

创建下一条 Alembic 增量迁移，revision 名称遵循仓库当前序列，预计为 `20260722_0036`，但必须先确认实际 head，禁止产生分叉 head。

迁移必须：

- 新增 `organization_unit` 及约束和索引；
- 扩展 Role、User、AuditLog；
- 为每个 Organization 幂等创建根节点“总部”；
- 回填现有用户和业务数据归属；
- 转换旧权限并写入兼容 DataScope；
- 输出可核验的回填计数；
- 保持旧应用能忽略新增表和字段；
- 支持 upgrade、downgrade 一版、再次 upgrade；
- 不删除旧字段，不做 contract 迁移。

禁止只修改 SQLAlchemy 模型而没有真实 Alembic 迁移。

## 五、后端实现要求

1. 新增组织树 CRUD、移动、停用、删除、主管分配 API。
2. 新增权限目录 API。
3. 扩展 Role Create/Update 和 User Create/Update。
4. 扩展 `/auth/me`，返回组织单元、权限版本和数据范围。
5. 实现角色权限与数据范围不得超过操作者的校验。
6. 实现组织树循环、跨租户父子、并发移动和停用节点校验。
7. 节点有关联用户、子节点或业务数据时禁止删除并返回 409 和阻塞计数。
8. 将任务、品牌、联系人、邮箱、批量导入、去重、黑名单、标签、自定义字段、Provider、设置、用户、角色、审计和导出全部映射到具体操作权限。
9. 所有敏感操作写审计；Credential、Token、密码和 API Key 不得进入日志或审计内容。
10. 增加路由权限覆盖测试或自动检查，防止以后新增非公开路由漏鉴权。

## 六、前端实现要求

1. 在系统配置中加入清晰的“组织与权限”管理体验。
2. 组织管理使用左树右详情；窄屏使用列表进入详情，不压缩成不可用树。
3. 角色编辑按模块显示全部具体操作，并为业务资源单独选择数据范围。
4. 提供只读人员、业务人员、部门主管、组织管理员模板；模板保存后形成普通角色快照。
5. 新增用户表单只保留姓名、邮箱、密码、组织单元、角色、状态；不要增加每用户不同职位配置。
6. 建立统一 `PermissionGate` 或 `v-permission`，覆盖菜单、路由、标签页、单项按钮和批量按钮。
7. 有页面权限但当前 DataScope 无数据时显示“当前范围暂无数据”；完全无查看权限时页面不显示。
8. 所有危险操作显示影响、二次确认、成功和失败状态。
9. 保持键盘可用、焦点可见、语义标签、非颜色状态提示和响应式布局。
10. 必须在真实运行页面检查桌面和窄屏，不能仅以 build 通过代替视觉验收。

## 七、测试必须先于完成声明

建立参数化权限矩阵，至少覆盖：

- 两个 Organization；
- 根、部门、团队三级树；
- 本人、同部门、下级部门、同级部门、其他租户；
- 五种 DataScope；
- 有/无具体操作权限；
- 单条和批量读写删、导入、导出、验证、任务控制；
- 高低权限用户和角色分配；
- 调岗前后历史与新数据归属；
- 停用用户、停用节点、移动节点和删除阻塞；
- Worker 和 Pipeline 归属继承；
- 403/404 信息泄露边界；
- 前端菜单、路由、按钮和空状态。

测试必须断言低权限用户不能通过直接调用 API 绕过前端限制。

## 八、强制验证命令

根据实际环境使用仓库已有虚拟环境和包管理器，至少执行并记录：

```text
git diff --check
python -m pytest -q
python -m ruff check app tests migrations
python -m compileall -q app migrations
pnpm exec vue-tsc --noEmit
pnpm run build
alembic upgrade head
alembic downgrade <改造前 revision>
alembic upgrade head
```

运行环境还必须验证：

- `/health`、`/ready`；
- Alembic 当前 revision；
- Backend、普通 Worker、Enrichment Worker、Beat、Frontend 状态；
- Worker/Beat 无 Traceback、Critical、连接错误；
- Outbox 无异常积压；
- 至少使用 admin、部门主管、普通员工、只读用户执行权限冒烟测试；
- 桌面和窄屏视觉检查。

若某项无法执行，必须明确说明原因和剩余风险，不能标记为通过。

## 九、执行纪律

- 使用 `apply_patch` 修改文件。
- 优先使用 `rg` 搜索。
- 不得使用 `git reset --hard`、`git checkout --` 或删除用户改动。
- 不得写入或提交 `.env`、密码、密钥、数据库 dump 或真实联系人数据。
- 不得顺手重构无关模块。
- 不得保留未接入真实路径的模型、API、组件或 TODO 占位。
- 每完成一个纵向阶段先运行针对性测试，再进入下一阶段。
- 发现方案与代码冲突时，以 `docs/DEVELOPMENT_RULES.md` 和最终方案为准；若确实无法兼容，停止并报告，不得暗自改变业务语义。
- 不得更新 NAS，不得连接 NAS，不得修改 NAS Docker 代理。

## 十、完成与交付

只有满足最终方案第 13 节的全部完成定义，才能报告“本地权限系统完成”。交付报告必须包含：

1. 用户可见结果。
2. 关键架构决策。
3. 修改文件和迁移 revision。
4. 权限目录与 API 覆盖证据。
5. 数据回填和兼容结果。
6. 实际执行的每条验证命令及结果。
7. 桌面与窄屏检查结果。
8. 已知限制和剩余风险。
9. 本地回滚步骤。
10. 明确声明“未更新 NAS”。

完成后不要自行提交或推送 Git，除非用户另行明确要求；保留工作区供用户审核。
