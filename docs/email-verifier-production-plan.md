# BuyerReach 本地邮箱验证生产方案

## 1. 目标与边界

在不改变 BuyerReach 现有 FastAPI、Celery、Redis、PostgreSQL、Vue 和 Vendor Workflow Adapter 总体架构的前提下，引入基于 `AfterShip/email-verifier` 的本地验证服务。

目标：

- 本地完成语法、一次性邮箱、角色邮箱、DNS/MX、SMTP 和 Catch-all 检测。
- 本地明确结果直接进入现有真实性评分流程。
- `unknown`、Catch-all、临时错误和高价值邮箱继续由 ZeroBounce/Hunter 兜底。
- 第三方付费调用量降低 50% 以上，同时不提高最终硬退信率。
- 本地服务可独立扩缩容、熔断和回滚，不阻塞现有搜索任务。

非目标：

- 不替换现有 `email_address`、`email_verification`、邮箱池和真实性评分模型。
- 不把 Go 库嵌入 Python 进程。
- 不改变 Hunter、ZeroBounce 现有 Workflow Adapter。
- 不允许业务代码写死服务地址、认证密钥或 Vendor 选择。
- 第一版不建设邮件发送或退信接收系统。

## 2. 生产架构

```text
Frontend / API
      |
BuyerReach FastAPI
      |
email verifier waterfall
      |
      +-- aftership-local-v1 Workflow Adapter
      |        |
      |        +-- email-verifier-api (Go, stateless, N replicas)
      |                 +-- syntax / disposable / role
      |                 +-- DNS / MX
      |                 +-- SMTP / catch-all
      |                 +-- Redis cache / rate limit
      |                 +-- optional SOCKS5 proxy pool
      |
      +-- ZeroBounce Workflow Adapter
      |
      +-- Hunter Workflow Adapter
      |
existing authenticity assessment and email pools
```

运行职责：

- FastAPI：业务入口、Provider 编排、结果落库、真实性评分和审计。
- Celery：批量任务、重试和并发调度。
- Go 验证服务：无业务状态的单邮箱技术验证。
- Redis：MX/域名结果缓存、域名级限速、并发令牌和短期熔断。
- PostgreSQL：保留现有邮箱和验证历史，原始结果记录 Adapter 版本。
- ZeroBounce/Hunter：只处理本地无法明确判断的结果。

## 3. 服务边界与 API

新增目录：

```text
services/email-verifier/
  cmd/server/main.go
  internal/api/
  internal/config/
  internal/verifier/
  internal/mapper/
  internal/cache/
  internal/ratelimit/
  internal/observability/
  Dockerfile
  go.mod
  go.sum
  README.md
```

只提供 Docker 内网服务，不映射公网端口。

### `POST /v1/verify`

请求：

```json
{"email":"buyer@example.com","smtp":true}
```

响应：

```json
{
  "provider": "aftership-local",
  "adapter_version": "v1",
  "result": "valid",
  "score": 90,
  "is_catch_all": false,
  "is_disposable": false,
  "is_role_account": false,
  "is_free_provider": false,
  "domain_deliverable": true,
  "mailbox_exists": true,
  "smtp_check": true,
  "reason": "smtp_recipient_accepted",
  "raw_status": "safe",
  "duration_ms": 1840,
  "cached": false
}
```

辅助端点：

- `GET /health`：进程存活，不访问 SMTP。
- `GET /ready`：验证配置、Redis 和 DNS，不消耗付费额度。
- `GET /metrics`：Prometheus 指标，只允许内部监控访问。

禁止使用包含邮箱地址的 GET URL，避免邮箱进入代理和访问日志。

## 4. 结果映射

| 技术结果 | BuyerReach 结果 | 规则 |
|---|---|---|
| 语法错误、一次性邮箱、无有效 MX | `invalid` / `disposable` | 本地终止 |
| SMTP 明确永久不存在 | `invalid` | 本地终止 |
| SMTP safe 且非 Catch-all | `valid` | 本地终止；高价值邮箱可按比例抽检 |
| Catch-all | `risky` | 必须进入第三方或人工审核 |
| SMTP 4xx、超时、限速、拒绝探测 | `unknown` | 延迟重试一次，再进入第三方 |
| 无法建立 SMTP 但 MX 正常 | `unknown` | 不得判无效 |

硬规则：

- `unknown` 不得进入无效池。
- Catch-all 不得进入有效池。
- 网络异常不得转换为业务无效。
- 只有明确永久错误才能跳过第三方验证。
- 本地结果仍需经过现有 `assess_email_authenticity`，不得绕过品牌域名、联系人身份和官网证据评分。

## 5. Vendor Workflow 集成

新增 Vendor：

```text
vendor = aftership_local
type = email_verifier
adapter_version = v1
```

必须同时交付：

- 系统配置中的启用入口和内部认证密钥。
- 配置校验。
- 免费连接测试。
- 明确失败提示。
- 完整版本化 Workflow Adapter。
- 迁移中默认禁用的 Provider 配置。

维护边界：

- 服务地址、请求协议、调用步骤和响应映射放在 Adapter 代码中。
- Token、启用状态、超时、并发和 Vendor 选择放在系统配置中并加密。
- `services.py` 只调用通用 Provider 瀑布，不写 AfterShip 专用 HTTP 代码。

生产瀑布：

```text
aftership_local
  -> unknown/catch-all/temporary_failure
ZeroBounce
  -> unavailable/quota_exhausted/request_failure
Hunter
  -> unknown/manual_review
```

任务启动时继续使用现有 Vendor 策略快照，保证执行期间顺序不漂移。

## 6. 分布式执行策略

### 两级并发

- Celery 控制业务任务并发，第一版每批 20 条。
- Go 服务每副本总并发默认 10。
- 同一注册域名并发默认 1，测试稳定后最多 2。
- 同一 MX 服务商设置独立速率限制，避免集中探测 Google/Microsoft。

### 分布式锁与缓存

Redis Key 建议：

```text
emailverify:result:<sha256(normalized_email)>
emailverify:domain:<domain>
emailverify:mx:<mx_host>
emailverify:lock:<sha256(normalized_email)>
emailverify:limit:domain:<domain>
emailverify:breaker:proxy:<proxy_id>
```

建议 TTL：

- MX 正常：24 小时。
- 无 MX：1 小时。
- 明确 valid：14～30 天。
- 明确 invalid：7～14 天。
- Catch-all 域名：7 天。
- 4xx/超时：5～30 分钟。
- 进程错误或代理错误：不缓存邮箱业务结果。

相同邮箱同时验证时只有一个请求执行 SMTP，其余请求等待并复用结果。

### 扩缩容

- Go 服务保持无状态，可运行多个副本。
- 扩容必须同时受 Redis 全局域名/MX 限速控制，不能只依赖进程内信号量。
- 验证节点使用独立出口 IP，不与营销邮件发送 IP 共用。
- SMTP 代理不可用时自动熔断并返回 `unknown`，不得阻塞 Celery。

## 7. 配置与安全

系统配置增加：

- `aftership_local.enabled`
- 加密的内部 Bearer Token
- SMTP 启用状态
- 加密的 SOCKS5 代理凭据
- 总并发、域名并发、请求超时
- 缓存 TTL
- 本地明确结果抽检比例

安全要求：

- Go 服务仅监听 Docker 内网。
- Token 使用常量时间比较。
- 日志只记录邮箱 SHA-256、域名、结果、耗时和错误码，不记录完整邮箱或代理密码。
- `/metrics` 不包含原始邮箱。
- SMTP 会话停在 `RCPT TO`，不发送 `DATA`。
- 设置请求体大小、请求超时、并发上限和优雅关停。
- 固定 AfterShip 依赖版本并生成依赖清单；禁止生产构建使用无版本的 `latest`。

## 8. 可观测性

指标：

- 请求总量和各结果比例。
- SMTP、DNS、缓存命中耗时。
- Unknown、Catch-all、超时比例。
- 每域名/MX 请求量。
- 第三方兜底比例和节省的验证次数。
- 代理节点错误率和熔断状态。
- 本地结果抽检与第三方不一致率。
- 最终发送硬退信率。

告警：

- 5 分钟 Unknown 率超过 60%。
- SMTP 超时率超过 30%。
- 单个 MX 探测速率异常。
- ZeroBounce 兜底率突然升高。
- 本地 valid 与第三方抽检不一致率超过 2%。
- 最终硬退信率高于上线前基线。

## 9. 分阶段上线

### 阶段 A：本地 PoC

- 建立 Go 服务、Dockerfile、健康检查和单条验证 API。
- 仅在开发环境执行，不接入生产瀑布。
- 验证端口 25 或 SOCKS5 代理可用。

退出条件：接口、鉴权、超时、DNS、SMTP 和 Catch-all 测试通过。

### 阶段 B：Adapter 影子模式

- 接入 `aftership_local_v1`，但不改变邮箱最终状态。
- 现有 ZeroBounce/Hunter 正常执行。
- 保存本地结果用于对比，不计入最终评分。

退出条件：至少 5,000 条样本；明确无效误杀率低于 0.5%；服务稳定运行 7 天。

### 阶段 C：小流量主验证

- 系统配置中开启 10% 流量。
- 本地明确结果直接使用；Unknown/Catch-all 继续兜底。
- 对本地 valid 随机抽检 10%。

退出条件：第三方一致率、硬退信率和任务耗时均不劣于基线。

### 阶段 D：逐步放量

- 10% -> 30% -> 60% -> 100%，每档至少观察 3 天。
- 根据 Unknown 率和 IP 状态调整域名并发。
- 逐步降低 valid 抽检比例，但不得低于 1%。

### 阶段 E：稳定生产

- 本地服务作为第一验证 Vendor。
- ZeroBounce、Hunter 保持可随时接管。
- 每月使用真实退信数据复核映射阈值。

## 10. 回滚

回滚必须只需系统配置操作：

1. 禁用 `aftership_local`。
2. 新任务恢复 ZeroBounce/Hunter 原顺序。
3. 运行中的任务按既有策略快照完成或由现有重试机制恢复。
4. 不删除本地验证历史。
5. 不回滚数据库字段，因为第一版复用现有结构。

本地服务故障、超时或熔断时，Adapter 返回标准 Provider 错误，现有瀑布自动使用下一 Vendor。

## 11. 验收标准

- 后端现有测试全量通过。
- 前端类型检查和生产构建通过。
- Go 单元测试和竞态检测通过。
- Docker 健康检查、优雅关停和重启恢复通过。
- 未配置 Token、错误 Token、服务不可达、25 端口受阻、代理不可用均有明确提示。
- `unknown` 不进入无效池，Catch-all 不进入有效池。
- 本地服务停机时现有验证流程可以继续。
- 第三方验证调用减少至少 50%。
- 硬退信率不高于上线前基线。
- API、应用和代理日志中没有完整邮箱及密钥泄露。

