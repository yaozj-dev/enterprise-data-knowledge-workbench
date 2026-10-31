"""Generate a deterministic quality-operations report without changing production policy."""

import argparse
import json
from collections import Counter
from pathlib import Path

from app.storage.app_db import get_quality_overview, init_db, list_quality_feedback

PROJECT_ROOT = Path(__file__).parents[2]
DEFAULT_OUTPUT_PATH = PROJECT_ROOT / "logs" / "quality-loop-report.json"


def build_recommendations(overview: dict, feedback_items: list[dict]) -> list[dict]:
    """Turn observable symptoms into reviewable engineering actions, not auto-mutations."""

    recommendations: list[dict] = []
    summary = overview["summary"]
    if summary["pending_feedback_count"]:
        recommendations.append(
            {
                "priority": "high",
                "signal": "pending_feedback",
                "action": "审核待处理负反馈，补充期望路由与脱敏回归问题后再修改策略。",
            }
        )
    p95 = summary["latency"]["p95_ms"]
    if p95 is not None and p95 > 8_000:
        recommendations.append(
            {
                "priority": "high",
                "signal": "slow_p95",
                "action": "优先查看慢节点，控制 RAG 上下文长度、减少不必要的模型调用或拆分慢 SQL。",
            }
        )
    issue_counts = Counter(item["issue_type"] or "unclassified" for item in feedback_items if item["rating"] == -1)
    for issue_type, count in issue_counts.most_common(3):
        action = {
            "wrong_route": "扩充路由回归集，比较规则与结构化 LLM 路由的混淆样本。",
            "retrieval_miss": "检查分块、Embedding、相似度阈值和文档覆盖度，再运行引用评测。",
            "wrong_answer": "检查事实依据、引用校验和 Prompt 约束，避免仅根据历史对话作答。",
            "slow_response": "根据 Trace 的 P95 分解检索、模型和 SQL 三段耗时后定位瓶颈。",
            "unsafe_or_unexpected_sql": "回放 SQL 样本并检查白名单、校验节点和只读权限策略。",
        }.get(issue_type, "人工归类样本后，形成可复现的评测用例。")
        recommendations.append(
            {
                "priority": "medium",
                "signal": f"negative_feedback:{issue_type}",
                "count": count,
                "action": action,
            }
        )
    if not recommendations:
        recommendations.append(
            {
                "priority": "info",
                "signal": "insufficient_signal",
                "action": "样本量不足，继续收集经过用户授权的反馈，不根据单次结果自动改写生产策略。",
            }
        )
    return recommendations


def build_report(window_days: int) -> dict:
    init_db()
    overview = get_quality_overview(window_days)
    feedback = list_quality_feedback(limit=100, offset=0)["items"]
    budget = overview.get("tool_budget", {})
    if budget.get("observed_runs", 0) >= 20:
        exhausted = budget.get("exhausted_count", 0)
        if exhausted:
            recommendations_build = [
                {
                    "priority": "medium",
                    "signal": "tool_budget_exhausted",
                    "count": exhausted,
                    "action": "人工查看预算触顶样本：先确认是澄清不足、工具失败还是任务复杂度，再用回归集验证是否调整上限。",
                }
            ]
        else:
            recommendations_build = []
    else:
        recommendations_build = []
    return {
        "window_days": window_days,
        "overview": overview,
        "recommendations": recommendations_build + build_recommendations(overview, feedback),
        "guardrail": "本报告只生成建议；路由规则、Prompt、阈值和模型版本必须经人工评审、离线评测和回归测试后发布。",
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Generate a quality operations report")
    parser.add_argument("--window-days", type=int, default=14)
    parser.add_argument("--output-path", type=Path, default=DEFAULT_OUTPUT_PATH)
    args = parser.parse_args()
    report = build_report(args.window_days)
    args.output_path.parent.mkdir(parents=True, exist_ok=True)
    args.output_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"quality_report={args.output_path} recommendations={len(report['recommendations'])}")


if __name__ == "__main__":
    main()
