# 评测与验收

本项目将“功能能运行”和“效果已经被验证”分开记录。固定评测集用于发现回归，不能直接代表真实企业数据、真实用户规模或线上稳定性。

## 1. 路由基准

`evals/router_cases.jsonl` 包含数据、文档、通用和澄清四类固定问题。它验证混合路由在规则覆盖范围内没有回归：

```powershell
uv run python -m app.scripts.evaluate_router
```

如果管理员审核并导出了脱敏反馈样本，可以一起运行：

```powershell
uv run python -m app.scripts.export_regression_cases
uv run python -m app.scripts.evaluate_router --regression-path "logs\feedback_regression_cases.jsonl"
```

## 2. 文档检索基准

`evals/document_corpus.jsonl` 和 `evals/document_retrieval_cases.jsonl` 是隔离的合成语料。脚本会写入临时 Qdrant collection 与临时 Elasticsearch index，并在退出时删除它们；不会读写用户上传的文档。

```powershell
uv run python -m app.scripts.evaluate_document_retrieval
```

报告会分别输出四种策略：

- `vector`：Qdrant 向量召回。
- `bm25`：Elasticsearch 关键词召回。
- `rrf`：两路 Top-20 使用指定的 `k` 融合后取 Top-3，默认 `k=60`。
- `rrf_reranker`：在配置 BGE Reranker 时，对 RRF Top-10 精排后取 Top-3。

每条策略报告 `Top-K` 命中率、MRR、Top-1 引用准确率、Top-1 无关片段率，以及延迟的 P50/P95。未配置或无法访问 Reranker 时，`rrf_reranker.available` 会是 `false`，这不是失败结果，也不能将 RRF 指标说成 Reranker 指标。

启用可选 Reranker 后再运行比较：

```powershell
docker compose --env-file .env -f "docker\docker-compose.yaml" --profile ranking up -d reranker
$env:RERANKER_URL = "http://127.0.0.1:8082/rerank"
uv run python -m app.scripts.evaluate_document_retrieval --reranker-url $env:RERANKER_URL --json-output "logs\rag-eval.json"
```

对比多个 RRF 参数时，评测脚本会对同一隔离语料分别执行每个值，并在 JSON 中按参数保存结果：

```powershell
uv run python -m app.scripts.evaluate_document_retrieval --rrf-k 40 60 80 --json-output "logs\rag-eval-rrf-k.json"
```

2026-10-26 的本地首轮实验中，`k=40/60/80` 在 10 条合成语料、30 条问题上均为 `30/30` Top-3 命中、MRR `1.0`、引用准确率 `100%`。这说明本数据集无法区分三个参数，不足以证明任何一个参数普适最优；当前仍保留 `k=60` 作为默认工程值。

首次启用前，需要按 README 下载 `BAAI/bge-reranker-v2-m3` 到 `docker\reranker\bge-reranker-v2-m3`。模型文件和 `logs` 都不应提交到 Git。当前 Compose 使用 TEI `cpu-1.9`，CPU 适配器逐条调用候选并默认超时 30 秒；超时、协议错误或服务进程异常时报告 `available=false` 并回退 RRF。当前本机 30 条合成问题的 RRF+Reranker 结果为 Top-3 命中率 `100%`、引用准确率 `100%`，P50/P95 延迟约 `4.33s/4.78s`；该结果不代表真实企业文档效果，生产部署应结合硬件重新评估。

## 3. 多智能体复合问题

`evals/composite_cases.jsonl` 包含至少 20 条同时需要经营数据与企业制度证据的问题。默认命令不调用模型或线上服务，只验证 Supervisor 是否生成带有 Data、Knowledge 和 Report Agent 的合法执行计划、依赖和工具边界：

```powershell
uv run python -m app.scripts.evaluate_composite_workbench
```

这项结果只能说明“计划和边界正确”，不能说成模型综合回答准确率。

要进行真实端到端运行，先登录工作台、创建一个已上传评测文档的测试会话，再将访问令牌和会话 ID 仅写入当前 PowerShell 进程：

```powershell
$env:EVAL_ACCESS_TOKEN = "登录后得到的 JWT"
$env:EVAL_CONVERSATION_ID = "测试会话 ID"
uv run python -m app.scripts.evaluate_composite_workbench --live --json-output "logs\composite-eval.json"
```

也可以让脚本自动创建一次性普通用户、上传合成制度、创建会话，并在结束时删除该用户及其文档索引。该模式只适合本地隔离环境，管理员账号只从项目 `.env` 读取，不会打印到报告中。建议先小批量冒烟，再运行完整 20 题：

```powershell
uv run python -m app.scripts.evaluate_composite_workbench --provision-live --limit 3
uv run python -m app.scripts.evaluate_composite_workbench --provision-live --json-output "logs\composite-eval.json"
```

复合评测支持 `--offset` 和 `--limit`，便于只重跑失败区间，避免重复消耗模型调用。

在线报告区分以下指标：

- `orchestration_contract_rate`：是否启动 Data、Knowledge 和 Report 三个 Agent。
- `evidence_backed_rate`：是否同时返回 SQL 结果和文档引用。
- `fully_completed_rate`：最终状态是否为 `completed`，不会把 `partial` 当作完整成功。
- `time_to_first_event` 与 `total_latency`：用于观察 SSE 首事件和完整响应的 P50/P95。

在线运行会消耗模型额度，且应仅在隔离的测试账号与合成文档下进行。将失败样本交给管理员审核后再进入回归集，不能自动修改 Prompt、工具权限或安全策略。

## 4. SQL、安全与发布门禁

```powershell
uv run pytest -q
uv run ruff check app tests main.py
uv run python -m app.scripts.evaluate_sql_results --report-path "logs\sql-eval.json"
uv run python -m app.scripts.verify_readonly_dw_access
cd frontend
pnpm run build
```

发布前应至少检查：SQL 写操作和跨用户文档/记忆被拒绝、MCP 未授权工具被拒绝、MCP 工具审计只保存低敏元数据、Reranker/Redis/MySQL 记忆/MCP/Verifier 故障有可见降级、固定回归集未退化。通过本地合成集不等于完成生产发布验收。

## 5. 参数、预算与并发实验

当前默认的 RRF `k=60`、最多 3 轮和最多 6 次工具调用是工程初始值，不应直接表述为已经由生产数据证明的最优参数。下一阶段使用同一语料、同一问题集和同一用户过滤条件做对照实验：

- RRF：比较 `k=40`、`k=60`、`k=80` 的 Recall@3、Recall@5、MRR、引用准确率、无关片段率和 P50/P95。
- Agent 预算：记录每次任务的实际轮数、工具调用数、重试次数、预算触顶率、partial 比例和完整成功率。
- 性能：记录 TTFE、检索、LLM、SQL、Verifier 和总响应的 P50/P95，并区分外部模型延迟与本地处理延迟。
- 并发：先使用 `asyncio` 固定并发脚本，必要时再使用 Locust；测试使用合成账号和数据，不做生产容量承诺。

只有在实验报告生成并经过人工复核后，才能修改默认参数或公开参数选择的效果。目标值（例如“95% 任务在 2 轮/4 次调用内完成”）只能作为验收目标，不能提前当作测量结果。

## 6. 故障注入验收

应使用 Mock 或停止单个 Docker 服务验证以下路径：Redis、MCP、Reranker、Elasticsearch、Qdrant、MySQL 记忆库、Verifier、Data Agent 和 Knowledge Agent。每个场景都要检查：

1. SSE 是否产生明确的 `degraded` 或 `partial_result` 事件。
2. 最终状态是否正确区分 `completed`、`partial`、`clarified` 和 `failed`。
3. 是否保留仍然有效的另一类证据。
4. 是否没有输出无来源结论、跨用户数据或内部错误细节。

故障降级证明的是容错行为，不等于已经完成多节点高可用、容量压测或线上 SLO 验收。
