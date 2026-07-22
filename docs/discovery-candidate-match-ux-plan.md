# 品牌候选池"关键词匹配度"展示与操作改进方案

日期：2026-07-20
状态：待评审（未实施）
依据：`docs/DEVELOPMENT_RULES.md`、`docs/pipeline-production-architecture-v1.md`

## 1. 问题定义

用户核心诉求：候选池列表要回答"这条候选和我搜索的关键词匹配吗？"，只有高匹配候选才值得点"精准丰富"。

当前实现（`frontend/src/views/DiscoveryCandidatesView.vue`）有三个根因导致回答不了这个问题：

1. **指标混淆**：候选未完成评估时，"相关度"列显示的是前端本地估算的"证据完整度"（`evidenceCompleteness()`，按官网/行业/产品证据有无加权），橙色大数字视觉权重最高。证据完整度 ≠ 关键词匹配度（典型反例：博物馆官网证据齐全可得 94%，但与搜索词毫不相关）。这违反规则 §9"不得把估算值显示为测量结果"的意图，现有小字标注不足以防止误读。
2. **真实匹配数据已存在但未展示**：concept matching v2（`backend/app/pipeline/matching.py::evaluate_matches`、`backend/app/pipeline/policy.py::RelevancePolicyV2`）已为每个完成评估的候选产出 `target_relevance_score`、`relevance_rating`（A/B/C/D）、`matched_concepts`（命中概念、关系、置信度、证据级别）、`conflicting_concepts`、`dimension_scores`（六维明细）、`penalties`、`reason_codes`，且列表 API 通过 `to_dict` 已全量返回。前端只用了总分。
3. **匹配分覆盖率低且无法按分工作**：评估必须逐条手动点"补充行业"触发；列表默认按 `last_seen_at` 排序（`services.py::list_discovery_candidates`），无评级筛选、无批量精准丰富。

## 2. 改动清单

### 改动 1（前端）：相关度列重构

文件：`frontend/src/views/DiscoveryCandidatesView.vue`

- `target_relevance_score != null`：显示评级徽章（A=success / B=primary / C=warning / D=info）+ 百分比分数；评分规则 popover 补充六维权重说明。
- `evaluation_status == 'pending'|'running'`：灰色"待评估"徽章，**不再显示证据完整度大数字**。
- `evaluation_status == 'insufficient_data'`：橙色"证据不足"徽章，附"补充行业"引导。
- 删除单元格内的 `evidenceCompleteness()` 大数字展示；该函数保留，降级为改动 2 详情行中的"证据完整度"进度条（明示其为证据覆盖指标，非匹配分）。
- 单元格 title/popover 明确标注："相关度 = 与搜索目标概念的确定性匹配分（policy relevance-2.0.0）"。

### 改动 2（前端）：匹配详情展开行

文件：`frontend/src/views/DiscoveryCandidatesView.vue`（必要时新增 `components/CandidateMatchDetail.vue`）

`el-table` 增加 expand 行，内容全部来自现有 API 数据：

- **搜索上下文**：该候选来源任务的目标概念列表。候选行有 `last_task_id`，通过现有 `GET /search-tasks/{task_id}`（`router.py:174`）取 `search_intent.target_concepts[].source_text / normalized_label`，前端按 task_id 缓存避免重复请求。
- **命中概念表**：`match_evaluation.matched_concepts[]`，列：公司概念（`company_concept`）↔ 目标概念、关系（`relationship`：exact/synonym/child/descendant…）、置信度、证据级别（`evidence_level`，官网证据标注"官网"）、`evidence_refs` 链接。
- **冲突概念**：`match_evaluation.conflicting_concepts[]`，红色提示。
- **六维得分条**：`dimension_scores`，按 cap 渲染 product_fit/40、industry_fit/20、business_type_fit/15、country_fit/10、evidence_quality/10、category_coverage/5；`penalties` 单列扣分项。
- **判定原因**：`reason_codes` 映射为人类可读文案（未知 code 原样显示，不得崩溃，规则 §9）。
- **证据完整度**：原估算函数结果以进度条展示，标注"证据覆盖度（非匹配分）"。

兼容性：`match_evaluation` 为空或字段缺失时显示"尚未评估，点击补充行业后生成"占位；未知枚举值安全降级（复用 `api/compat.ts` 的模式）。

### 改动 3（前端）：失败重试入口

- `industry_enrichment_status == 'failed'` 时，操作列"补充行业"按钮文案变为"重试补充"，失败原因（`industry_enrichment_error`）在状态列 tooltip 完整展示。现有接口已支持重试，仅展示层改动。

### 改动 4（后端 API）：排序与筛选

文件：`backend/app/api/v1/router.py::list_discovery_candidates`、`backend/app/modules/services.py::list_discovery_candidates`

- 新增可选查询参数：
  - `sort`：`last_seen`（默认，保持现有行为）| `target_relevance` | `emails_count`
  - `rating`：`A|B|C|D|none`（none = 未评估）
  - `evaluation`：`pending|running|insufficient_data|completed|failed`
- `sort=target_relevance` 使用 `desc().nullslast()`（未评估排最后），次级排序 `last_seen_at.desc()`。
- 向后兼容：全部为可选参数，旧客户端行为不变（规则 §5）；非法值由 FastAPI `Literal` 校验返回 422。
- 不需要数据库迁移。`evaluation_status` 已有索引；`target_relevance_score` 暂不加索引，候选量增长后如需索引按 expand 迁移单独提出。

### 改动 5（后端 + 前端）：批量精准丰富

文件：`backend/app/api/v1/router.py`（新端点）、`frontend/src/views/DiscoveryCandidatesView.vue`

- 新端点 `POST /discovery-candidates/bulk-approve`，权限 `brands:write`，body：`{ids: list[UUID], target_titles?: list[str], contacts_limit_per_brand?: int}`。
- 逐条调用现有 `services.approve_discovery_candidate`（自带状态守卫、`transition_candidate` 幂等键、audit），**每条独立事务**：成功即 commit + `queue_search_task` + `execute_search_task_job.delay`；`ValueError`（状态不符/缺域名）记入 skipped 并继续，不因单条失败回滚其他。
- 返回 `{queued: int, skipped: [{id, reason}]}`。
- 幂等性：approve 的 transition 幂等键含 `task.id`；重复提交时候选状态已非 `pending`，被守卫安全拒绝。
- 前端：工具栏加"批量精准丰富（n）"按钮，仅当选中包含 `pending`/`enrichment_failed` 行时可用；职位沿用审批弹窗默认值（与单条一致），弹一个简化确认框（可编辑职位、每品牌联系人数）。
- 不做"按评级自动全选"等隐式批量，避免误触发计费调用（规则 §4）。

### 改动 6（后续独立提案，不在本次范围）

候选入库后自动排队行业富化+评估，以提升匹配分覆盖率。涉及任务配置开关、rollout、预算守卫（规则 §4），需单独设计与评审。

## 3. 明确不做的事

- 不修改评分策略本身：`RelevancePolicyV2` 版本不变，不引入新分数、不调整权重。
- 不用搜索筛选条件（品类、国家）生成"虚拟匹配分"；未评估一律显示"待评估"（规则 §6）。
- 不删除证据完整度逻辑，仅降级展示位置。
- 不做 SSE 切换，沿用现有轮询。

## 4. 涉及文件汇总

| 文件 | 改动 |
|---|---|
| `frontend/src/views/DiscoveryCandidatesView.vue` | 改动 1/2/3/5（相关度列、expand 详情、重试入口、批量按钮、排序筛选 UI） |
| `frontend/src/components/CandidateMatchDetail.vue` | 改动 2（新增，可选拆分） |
| `backend/app/api/v1/router.py` | 改动 4（list 参数）、改动 5（bulk-approve 端点） |
| `backend/app/modules/services.py` | 改动 4（list 排序/筛选） |
| `backend/app/modules/schemas.py` | 改动 5（BulkApprove 请求模型） |
| `backend/tests/` | 新增 list 排序筛选、bulk-approve 成功/部分失败/状态守卫测试 |
| `docs/USER_MANUAL.md` | 候选池章节更新 |

## 5. 验证计划（规则 §10）

```text
Backend:  python -m pytest -q（含新增用例）
Static:   python -m ruff check app tests migrations
Compile:  python -m compileall -q app migrations
Frontend: pnpm exec vue-tsc --noEmit
Build:    pnpm run build
Runtime:  桌面 + 窄屏各检查一次候选池关键路径（§9）：列表加载、详情展开、
          排序筛选、单条/批量精准丰富、失败重试、未知字段降级
Migration: 本次无迁移
```

兼容性验证：旧前端对新 API 参数无感知（不传即默认）；新前端对缺少 `match_evaluation` 字段的旧数据安全降级。

## 6. 风险与边界

- expand 行内对 `GET /search-tasks/{id}` 的额外请求按 task_id 缓存；任务被删除时显示"来源任务已删除"。
- 批量 approve 会真实触发计费 Vendor 调用，前端确认框必须明示将创建 n 个精准品牌任务；不在列表加载路径上引入任何新 Vendor 调用。
- `target_relevance` 排序为数据库内排序，大候选量下如变慢，先加索引（expand 迁移）再上线该排序为默认。
