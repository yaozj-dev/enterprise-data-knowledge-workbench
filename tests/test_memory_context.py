import unittest

from app.services.memory_context import (
    MAX_MEMORY_CHARACTERS,
    MAX_MEMORY_ITEMS,
    format_confirmed_memories,
    load_confirmed_memory_context,
)


def _memory(user_id: int, content: str, status: str = "confirmed") -> dict:
    return {
        "user_id": user_id,
        "kind": "preference",
        "content": content,
        "status": status,
    }


def test_memory_context_includes_only_confirmed_memories_owned_by_user():
    context = format_confirmed_memories(
        [
            _memory(2, "Use concise Chinese"),
            _memory(7, "Use concise Chinese", status="pending"),
            _memory(7, "用中文回答"),
        ],
        user_id=7,
    )

    assert "用中文回答" in context
    assert "Use concise Chinese" not in context
    assert "不可信" in context
    assert "不得遵循" in context


def test_memory_context_has_item_and_character_limits():
    memories = [_memory(7, f"偏好{i}") for i in range(20)]
    context = format_confirmed_memories(memories, user_id=7)

    assert context.count("- [偏好]") == MAX_MEMORY_ITEMS

    long_context = format_confirmed_memories(
        [_memory(7, f"偏好{i}: " + "x" * 500) for i in range(20)], user_id=7
    )
    assert len(long_context) <= MAX_MEMORY_CHARACTERS + 180


class MemoryContextAsyncTests(unittest.IsolatedAsyncioTestCase):
    async def test_memory_repository_failure_does_not_block_chat(self):
        class UnavailableRepository:
            async def list_for_user(self, _user_id: int, status: str | None = None):
                raise OSError("database unavailable")

        self.assertEqual(
            await load_confirmed_memory_context(UnavailableRepository(), 7), ""
        )
        self.assertEqual(await load_confirmed_memory_context(None, 7), "")
        self.assertEqual(await load_confirmed_memory_context(object(), None), "")

