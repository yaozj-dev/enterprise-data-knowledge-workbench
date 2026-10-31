"""Top-level LangGraph used to create and validate a Supervisor plan.

Specialist execution stays in the workbench service because it has to stream
events and handle per-agent timeouts.  This graph owns the part that benefits
from LangGraph's state model: planning, validation and a bounded fallback.
"""

from __future__ import annotations

from typing import Any, TypedDict

from langgraph.constants import END, START
from langgraph.graph import StateGraph

from app.agent.contracts import ExecutionPlan
from app.agent.supervisor import RoutingDecision, build_execution_plan


class SupervisorGraphState(TypedDict, total=False):
    query: str
    mode: str
    decision: dict[str, Any]
    plan: dict[str, Any]
    error: str | None


def create_plan(state: SupervisorGraphState) -> dict[str, Any]:
    decision, plan = build_execution_plan(
        state.get("query", ""),
        state.get("mode", "auto"),
    )
    return {
        "decision": {
            "intent": decision.intent,
            "confidence": decision.confidence,
            "strategy": decision.strategy,
            "policy_version": decision.policy_version,
        },
        "plan": plan.model_dump(mode="json"),
        "error": None,
    }


def validate_plan(state: SupervisorGraphState) -> dict[str, Any]:
    try:
        plan = ExecutionPlan.model_validate(state.get("plan", {}))
    except Exception as exc:
        return {"error": f"invalid supervisor plan: {type(exc).__name__}"}
    return {"plan": plan.model_dump(mode="json"), "error": None}


graph_builder = StateGraph(SupervisorGraphState)
graph_builder.add_node("create_plan", create_plan)
graph_builder.add_node("validate_plan", validate_plan)
graph_builder.add_edge(START, "create_plan")
graph_builder.add_edge("create_plan", "validate_plan")
graph_builder.add_edge("validate_plan", END)
supervisor_graph = graph_builder.compile()


async def build_supervisor_plan(
    query: str,
    mode: str = "auto",
) -> tuple[RoutingDecision, ExecutionPlan]:
    """Run the top-level graph and return only validated public contracts."""

    state = await supervisor_graph.ainvoke({"query": query, "mode": mode})
    if state.get("error"):
        # The deterministic builder is the bounded fallback.  It is kept
        # server-owned and cannot receive model-controlled identity fields.
        decision, plan = build_execution_plan(query, mode)
        return decision, plan
    decision_payload = state["decision"]
    decision = RoutingDecision(
        decision_payload["intent"],
        float(decision_payload["confidence"]),
        decision_payload["strategy"],
        decision_payload["policy_version"],
    )
    return decision, ExecutionPlan.model_validate(state["plan"])
