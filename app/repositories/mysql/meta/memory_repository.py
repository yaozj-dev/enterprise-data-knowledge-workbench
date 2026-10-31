"""User-confirmed long-term memory repository in the meta database."""

from __future__ import annotations

import asyncio
import secrets
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

MEMORY_TABLE_DDL = (
    """
    CREATE TABLE IF NOT EXISTS agent_memory_items (
        id VARCHAR(64) PRIMARY KEY,
        user_id BIGINT NOT NULL,
        conversation_id VARCHAR(128),
        kind VARCHAR(40) NOT NULL,
        content TEXT NOT NULL,
        source_run_id VARCHAR(128),
        status VARCHAR(20) NOT NULL DEFAULT 'pending',
        created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
        confirmed_at TIMESTAMP NULL,
        INDEX idx_memory_user_status (user_id, status)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
    """,
    """
    CREATE TABLE IF NOT EXISTS conversation_summaries (
        id VARCHAR(64) PRIMARY KEY,
        user_id BIGINT NOT NULL,
        conversation_id VARCHAR(128) NOT NULL,
        summary TEXT NOT NULL,
        source_run_id VARCHAR(128),
        created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
        updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
        UNIQUE KEY uq_summary_conversation (user_id, conversation_id)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
    """,
    """
    CREATE TABLE IF NOT EXISTS memory_audit_logs (
        id BIGINT PRIMARY KEY AUTO_INCREMENT,
        memory_id VARCHAR(64) NOT NULL,
        user_id BIGINT NOT NULL,
        action VARCHAR(30) NOT NULL,
        detail VARCHAR(1000),
        created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
        INDEX idx_memory_audit_user (user_id, created_at)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
    """,
)

_memory_schema_ready = False
_memory_schema_lock = asyncio.Lock()


class MemoryRepository:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def ensure_tables(self) -> None:
        global _memory_schema_ready

        if _memory_schema_ready:
            return
        async with _memory_schema_lock:
            if _memory_schema_ready:
                return
            for ddl in MEMORY_TABLE_DDL:
                await self.session.execute(text(ddl))
            await self.session.commit()
            _memory_schema_ready = True

    async def propose(
        self,
        user_id: int,
        content: str,
        *,
        kind: str = "preference",
        conversation_id: str | None = None,
        source_run_id: str | None = None,
    ) -> dict[str, Any]:
        memory_id = secrets.token_urlsafe(18)
        await self.session.execute(
            text(
                "INSERT INTO agent_memory_items "
                "(id, user_id, conversation_id, kind, content, source_run_id, status) "
                "VALUES (:id, :user_id, :conversation_id, :kind, :content, :source_run_id, 'pending')"
            ),
            {
                "id": memory_id,
                "user_id": user_id,
                "conversation_id": conversation_id,
                "kind": kind[:40],
                "content": content[:2_000],
                "source_run_id": source_run_id,
            },
        )
        await self.session.execute(
            text(
                "INSERT INTO memory_audit_logs(memory_id, user_id, action, detail) "
                "VALUES (:memory_id, :user_id, 'propose', :detail)"
            ),
            {"memory_id": memory_id, "user_id": user_id, "detail": "candidate created"},
        )
        await self.session.commit()
        return {
            "id": memory_id,
            "user_id": user_id,
            "conversation_id": conversation_id,
            "kind": kind,
            "content": content,
            "source_run_id": source_run_id,
            "status": "pending",
        }

    async def list_for_user(self, user_id: int, status: str | None = None) -> list[dict[str, Any]]:
        condition = "AND status = :status" if status else ""
        params: dict[str, Any] = {"user_id": user_id}
        if status:
            params["status"] = status
        result = await self.session.execute(
            text(
                "SELECT id, user_id, conversation_id, kind, content, source_run_id, status, "
                "created_at, confirmed_at FROM agent_memory_items "
                f"WHERE user_id = :user_id {condition} ORDER BY created_at DESC LIMIT 100"
            ),
            params,
        )
        return [dict(row) for row in result.mappings().all()]

    async def search_confirmed(self, user_id: int, query: str) -> list[dict[str, Any]]:
        """Search only user-confirmed memories by a bounded text fragment."""

        result = await self.session.execute(
            text(
                "SELECT id, user_id, conversation_id, kind, content, source_run_id, "
                "status, created_at, confirmed_at FROM agent_memory_items "
                "WHERE user_id = :user_id AND status = 'confirmed' "
                "AND content LIKE :query ORDER BY confirmed_at DESC LIMIT 20"
            ),
            {"user_id": user_id, "query": f"%{query[:100]}%"},
        )
        return [dict(row) for row in result.mappings().all()]

    async def confirm(self, user_id: int, memory_id: str) -> dict[str, Any] | None:
        result = await self.session.execute(
            text(
                "UPDATE agent_memory_items SET status = 'confirmed', confirmed_at = CURRENT_TIMESTAMP "
                "WHERE id = :id AND user_id = :user_id AND status = 'pending'"
            ),
            {"id": memory_id, "user_id": user_id},
        )
        if result.rowcount != 1:
            await self.session.rollback()
            return None
        await self.session.execute(
            text(
                "INSERT INTO memory_audit_logs(memory_id, user_id, action, detail) "
                "VALUES (:memory_id, :user_id, 'confirm', :detail)"
            ),
            {"memory_id": memory_id, "user_id": user_id, "detail": "user confirmed"},
        )
        await self.session.commit()
        result = await self.session.execute(
            text("SELECT * FROM agent_memory_items WHERE id = :id AND user_id = :user_id"),
            {"id": memory_id, "user_id": user_id},
        )
        row = result.mappings().first()
        return dict(row) if row else None

    async def delete(self, user_id: int, memory_id: str) -> bool:
        result = await self.session.execute(
            text(
                "UPDATE agent_memory_items SET status = 'deleted' "
                "WHERE id = :id AND user_id = :user_id AND status <> 'deleted'"
            ),
            {"id": memory_id, "user_id": user_id},
        )
        if result.rowcount != 1:
            await self.session.rollback()
            return False
        await self.session.execute(
            text(
                "INSERT INTO memory_audit_logs(memory_id, user_id, action, detail) "
                "VALUES (:memory_id, :user_id, 'delete', :detail)"
            ),
            {"memory_id": memory_id, "user_id": user_id, "detail": "user deleted"},
        )
        await self.session.commit()
        return True

