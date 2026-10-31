"""终止无法通过只读策略或有限修正的 SQL。"""

from langgraph.runtime import Runtime

from app.agent.context import DataAgentContext
from app.agent.state import DataAgentState


async def reject_sql(state: DataAgentState, runtime: Runtime[DataAgentContext]):
    writer = runtime.stream_writer
    step = "拒绝SQL"
    writer({"type": "progress", "step": step, "status": "error"})
    writer(
        {
            "type": "error",
            "message": state.get("error") or "SQL 未通过只读安全校验，系统拒绝执行。",
        }
    )
    return {}
