# Query Slicing Implementation Decisions

本文件只记录偏离 `docs/query-slicing-production-implementation-plan.md` 或需要在多个合理方案间作出的重要实施决定。不得用它建立第二套架构规则。

每项决定使用以下格式：

```markdown
## DEC-YYYYMMDD-NNN：标题

- status: proposed|accepted|superseded
- phase: A|B|C|D|E
- context: 当前实现和约束
- decision: 实际选择
- alternatives: 考虑过的替代方案
- compatibility: 对旧任务、API、数据和 rollout 的影响
- migration_and_rollback: 迁移及回滚方式
- verification: 证明该决定安全的测试或检查
- author: Agent/平台
- date: ISO-8601
```

当前没有已接受的偏离决定。
