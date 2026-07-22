# 可直接执行的 Codex Prompt

将下面 Prompt 原样交给 Codex，在 BuyerReach 工作区根目录执行。

---

你正在 `D:\buyer reach` BuyerReach 项目中工作。请完整实现 `docs/email-verifier-production-plan.md` 描述的本地邮箱验证第一版，但必须保持现有 FastAPI、Celery、Redis、PostgreSQL、Vue 和 Vendor Workflow Adapter 总体架构不变。

## 总目标

新增基于 `github.com/AfterShip/email-verifier` 的独立 Go 验证服务，并通过版本化 `aftership_local_v1` Workflow Adapter 接入现有 `email_verifier` Provider 瀑布。本地明确结果可结束验证；`unknown`、Catch-all、临时网络错误继续降级到现有 ZeroBounce/Hunter。实现必须可通过系统配置一键禁用并无损回滚。

## 开始前必须执行

1. 完整阅读根目录 `AGENTS.md`、`docs/email-verifier-production-plan.md`、`docs/provider-workflow-refactor.md`、`docs/architecture-v1.md`。
2. 检查工作树；保留所有用户现有改动，不覆盖无关文件。
3. 梳理并记录现有 Provider 配置、Vendor Workflow、连接测试、加密配置、任务策略快照、邮箱验证瀑布和前端系统配置实现位置。
4. 先运行基线测试：后端完整 pytest 和前端生产构建。记录结果。
5. 制定分阶段计划并逐步执行；每一阶段完成后必须验证，失败时先修复再进入下一阶段。

## 强制架构约束

- Go 验证器必须是独立 Docker 内网服务，不嵌入 Python 进程。
- 不改变现有邮箱表、验证历史、真实性评分和邮箱池总体模型；没有必要不得新增数据库字段。
- 第三方密钥、内部 Token、启用状态和 Vendor 选择必须通过“系统配置”维护并使用现有加密机制。
- 服务地址、请求协议、调用步骤和响应映射必须由版本化 Workflow Adapter 代码维护；业务代码不得写死地址或绕过 Provider 配置。
- 新增 Vendor 必须同时提供 API Key/内部 Token 配置入口、配置校验、免费连接测试、失败提示和完整 Workflow Adapter。
- 不修改或破坏 Hunter、ZeroBounce 当前行为和测试。
- `unknown` 不得当作 `invalid`；Catch-all 不得进入有效池；网络错误不得转换为业务无效。
- SMTP 只执行到 `RCPT TO`，不发送邮件正文。
- 不允许用包含邮箱地址的 GET URL；使用 `POST /v1/verify`。
- 不允许在日志中输出完整邮箱、Token、代理账号或密码。
- 不使用浮动 `latest` 依赖；固定 Go 模块和容器基础镜像版本。

## 阶段 1：建立 Go 服务

在 `services/email-verifier` 建立可独立构建的 Go 服务：

- 固定兼容版本的 `github.com/AfterShip/email-verifier`。
- `POST /v1/verify`，请求为 `{"email":"...","smtp":true}`。
- `GET /health`、`GET /ready`、`GET /metrics`。
- Bearer Token 鉴权，健康检查除外。
- 支持 SMTP 开关、SOCKS5/SOCKS4/SOCKS4a 代理、请求超时、总并发和单域名并发。
- 启用 SMTP 检查、Catch-all 检查、域名拼写建议和一次性域名更新。
- 将 AfterShip 原始结果映射成稳定的内部 DTO：`valid/risky/invalid/disposable/unknown`。
- 添加结构化脱敏日志、优雅关停、请求体大小限制和 Prometheus 指标。
- 如 Redis 已可复用，实现邮箱结果缓存、域名/MX缓存、分布式锁和全局域名限速；如果第一版无法安全完成，至少抽象接口并实现可靠的 Redis 版本，不能只靠进程内锁来支持多副本。
- 编写 Go 单元测试，覆盖语法错误、无MX、safe、invalid、unknown、Catch-all、一次性邮箱、超时、鉴权和结果映射。SMTP外部依赖必须可注入和模拟，测试不得依赖公网邮箱服务器。
- 添加多阶段 Dockerfile、非 root 用户、健康检查和 README。

阶段验证：

- `go test ./...`
- `go test -race ./...`
- `go vet ./...`
- 构建 Docker 镜像并启动，验证 health、错误 Token、正确 Token、超时和关停。

## 阶段 2：Docker Compose 集成

- 在现有 Compose 文件中增加 `email-verifier`，复用现有 Redis 和内部网络。
- 不向宿主机或公网暴露生产端口，只使用 `expose`。
- 增加 `.env.example` 示例变量，但不得提交真实密钥。
- 确保原有服务在验证器未启动时仍能启动。
- SMTP出口节点必须与营销发送IP解耦；在 README 中明确端口25和代理要求。

阶段验证：

- Compose 配置解析通过。
- 服务健康检查通过。
- 停止验证器后 BuyerReach 后端仍正常，验证流程可降级。

## 阶段 3：版本化 Vendor Workflow Adapter

- 新增 Vendor `aftership_local`，类型仅为 `email_verifier`，Adapter版本 `v1`。
- 按项目现有模式实现配置生成、配置校验、HTTP调用、认证、超时、响应映射、错误规范化和连接测试。
- Adapter 必须输出当前邮箱真实性评估所需字段：`result`、`score`、`is_catch_all`、`is_disposable`、`domain_deliverable`、`mailbox_exists`、`smtp_check`。
- `/health` 或 `/ready` 连接测试不得调用付费服务，不得执行真实邮箱SMTP验证。
- 为服务不可达、Token错误、DNS不可用、端口25受阻、代理不可用、超时和无结论结果提供中文可理解的失败提示。
- 新增迁移，创建默认禁用的 Provider/Vendor配置；迁移必须可升级和降级，不覆盖用户现有策略。
- 将 `aftership_local` 加入验证瀑布候选，但只有启用后才参与。
- 保持任务策略快照语义；任务开始后 Vendor顺序不漂移。

结果规则：

- 明确 syntax invalid、disposable、无MX、SMTP永久不存在可以终止本地流程。
- safe 且非 Catch-all 映射为 valid。
- Catch-all 映射 risky 并继续第三方兜底。
- 4xx、timeout、connection refused、blocked、unknown 映射 unknown 并继续第三方兜底。
- 不得因为本地 Provider 故障而让整个验证任务失败。

阶段验证：

- 为 Adapter、配置校验、连接测试、所有响应映射和瀑布降级添加测试。
- 单独运行相关测试，然后运行后端完整 pytest。

## 阶段 4：系统配置前端

- 在现有系统配置中增加 AfterShip本地验证 Vendor。
- 提供内部 Token 保存、启用开关、免费连接测试、最后测试状态和明确失败提示。
- 提供验证顺序或使其正确出现在现有验证 Vendor策略中。
- 根据现有交互方式支持保留已保存密钥，前端不得回显密钥。
- 不让用户编辑 Adapter维护的接口协议和响应映射。
- UI文案明确：本地服务、SMTP端口25/代理要求、Unknown会自动使用第三方兜底。

阶段验证：

- Vue TypeScript检查和生产构建通过。
- 验证保存、留空保留密钥、连接成功、鉴权失败、服务不可达和禁用流程。

## 阶段 5：影子模式与灰度开关

- 实现系统配置控制的运行模式：`disabled`、`shadow`、`active`。
- `shadow` 模式调用本地服务并记录对比结果，但不改变最终邮箱状态，现有付费验证继续作为最终结果。
- `active` 模式仅让本地明确结果生效；Unknown/Catch-all/临时错误继续第三方。
- 实现可配置流量比例，默认0；支持10%、30%、60%、100%灰度。
- 实现本地 valid 抽检比例，默认10%，稳定后允许降低但不得低于1%。
- 审计记录 Adapter版本、运行模式、是否缓存、是否抽检、第三方是否兜底和不一致情况。
- 避免重复付费：相同邮箱的同时请求必须通过分布式锁合并。

阶段验证：

- 测试 disabled 不调用本地服务。
- 测试 shadow 不改变最终结果。
- 测试 active 明确结果终止瀑布。
- 测试 Unknown/Catch-all正常进入ZeroBounce/Hunter。
- 测试本地服务停机时自动降级。
- 测试并发相同邮箱只触发一次实际本地验证。

## 阶段 6：文档、运维和最终验收

- 更新部署文档、用户手册、架构文档、环境变量示例和故障排查。
- 提供端口25测试、SOCKS代理测试、健康检查、指标和回滚说明。
- 提供上线顺序：开发PoC -> 影子5000条/7天 -> 10% -> 30% -> 60% -> 100%。
- 提供一键回滚步骤：系统配置禁用本地Vendor即可恢复原ZeroBounce/Hunter流程。
- 不执行真实生产放量，不使用真实第三方密钥，不向真实邮箱发送邮件。

最终必须执行并报告：

1. Go：格式化、`go vet ./...`、`go test ./...`、`go test -race ./...`。
2. 后端：完整 pytest。
3. 前端：TypeScript检查和生产构建。
4. Docker：镜像构建、Compose解析、健康检查、鉴权、停机降级。
5. 数据库：迁移升级和降级验证。
6. 安全检查：搜索日志、配置和产物，确认无真实密钥、完整测试邮箱或代理密码泄露。
7. 检查所有用户指令是否实现成功，并列出未完成项、风险和人工部署前提。

## 交付要求

最终回复必须包含：

- 已实现的生产能力。
- 关键文件的绝对路径链接。
- 数据库迁移说明。
- 系统配置和启用步骤。
- 验证命令及真实结果。
- 已知限制，尤其是端口25、SMTP代理、Gmail/Microsoft和Catch-all。
- 灰度上线与一键回滚方法。
- 不得声称未经实际样本测试的准确率或成本节省已经实现。

如果某一步被外部环境阻塞，先完成所有不依赖该条件的实现和模拟测试，再明确报告阻塞；不要通过删除测试、降低断言或绕过系统配置来获得通过。

---
