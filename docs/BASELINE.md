# 可复现基线

本文档记录仓库当前实现的本地验证口径。数据集为仓库内的合成数仓和合成制度文档，结果用于发现回归，不代表真实企业数据、线上用户规模或生产 SLA。

## 验证范围

| 项目 | 数据或范围 | 已记录结果 | 边界 |
| --- | --- | --- | --- |
| 路由 | 40 条固定中文问题 | 40/40 | 不能覆盖真实用户长尾表达 |
| SQL 结果与安全 | 30 条演示数仓问题 | 30/30，全部通过安全 SQL 检查 | 不代表真实业务数据准确率 |
| 文档检索与引用 | 10 篇合成制度、30 条问题 | Top-3 命中 30/30，Top-1 引用匹配 30/30 | 不代表真实企业文档效果 |
| Supervisor 合同 | 计划校验、循环依赖、工具预算和未授权调用 | 测试覆盖 | 不代表线上多租户压测 |
| 复合流程 | 固定复合问题集 | 以评测脚本输出为准 | 需要同时满足数据、文档和模型服务条件 |

## 复现命令

在项目根目录准备好 .env、Docker 服务和 Embedding 模型后执行：

~~~powershell
uv run pytest -q
uv run ruff check app tests main.py
docker compose --env-file .env -f docker/docker-compose.yaml --profile full --profile state config --quiet
uv run python -m app.scripts.evaluate_router --cases evals/router_cases.jsonl
uv run python -m app.scripts.evaluate_sql_results --cases evals/sql_result_cases.jsonl
uv run python -m app.scripts.evaluate_document_retrieval --cases evals/document_retrieval_cases.jsonl
uv run python -m app.scripts.evaluate_composite_workbench --cases evals/composite_cases.jsonl
~~~

需要在线调用模型的评测只使用隔离账号和合成文档；API Key、JWT、访问令牌和完整敏感内容不得写入报告或提交到 Git。

## 故障与降级验收

| 故障 | 预期行为 | 可观察结果 |
| --- | --- | --- |
| Embedding 尚未 ready | backend 和 MCP 等待健康检查 | Compose 不在模型未就绪时接收业务请求 |
| Elasticsearch 不可用 | 降级到 Qdrant 向量检索 | SSE 和 Trace 标记 vector_only |
| Qdrant 不可用 | 降级到 Elasticsearch BM25 | SSE 和 Trace 标记 keyword_only |
| Reranker 不可用 | 保留 RRF 排名 | 记录 reranker_unavailable |
| MCP 不可用 | 使用本地工具适配器 | ToolResult 结构保持一致 |
| Redis 不可用 | 使用有界进程内状态 | 记录 degraded，不阻断当前回答 |
| Verifier 超时或低置信度 | 返回可核验证据并标记 partial | 不伪装为 completed |

## 安全验收

- 危险 SQL、多语句 SQL、未知表和超出结果上限的查询必须在执行前拒绝。
- 普通用户不能访问管理员接口、其他用户的会话、文档、记忆或运行轨迹。
- Agent 只能调用服务端白名单工具，不能通过请求体指定 user_id、数据库账号或权限。
- 文档回答只能引用本次按 user_id 过滤召回的有效片段。
- Redis 运行状态、Trace、日志和评测报告不保存 API Key、密码、JWT 或完整敏感 Prompt。

## 结果解释

固定评测集的通过率只能说明当前代码在有限数据和指定配置下没有出现已知回归。模型供应商延迟、Docker 资源、文档质量、并发量和业务口径变化都可能影响实际结果；修改 Prompt、路由规则、检索参数或工具权限后应重新运行完整基线。
