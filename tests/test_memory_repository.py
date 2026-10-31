import asyncio
import unittest
from unittest.mock import patch

from app.repositories.mysql.meta import memory_repository
from app.repositories.mysql.meta.memory_repository import MemoryRepository


class _Session:
    def __init__(self, *, fail_first_execute: bool = False):
        self.execute_count = 0
        self.commit_count = 0
        self.fail_first_execute = fail_first_execute

    async def execute(self, _statement):
        self.execute_count += 1
        await asyncio.sleep(0)
        if self.fail_first_execute:
            self.fail_first_execute = False
            raise OSError("database unavailable")

    async def commit(self):
        self.commit_count += 1


class MemoryRepositorySchemaTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.ready = patch.object(memory_repository, "_memory_schema_ready", False)
        self.lock = patch.object(memory_repository, "_memory_schema_lock", asyncio.Lock())
        self.ready.start()
        self.lock.start()
        self.addCleanup(self.lock.stop)
        self.addCleanup(self.ready.stop)

    async def test_schema_is_created_once_across_repository_instances(self):
        first_session = _Session()
        second_session = _Session()

        await MemoryRepository(first_session).ensure_tables()
        await MemoryRepository(second_session).ensure_tables()

        self.assertEqual(first_session.execute_count, len(memory_repository.MEMORY_TABLE_DDL))
        self.assertEqual(first_session.commit_count, 1)
        self.assertEqual(second_session.execute_count, 0)
        self.assertEqual(second_session.commit_count, 0)

    async def test_concurrent_initialization_executes_schema_once(self):
        sessions = [_Session() for _ in range(4)]

        await asyncio.gather(*(MemoryRepository(session).ensure_tables() for session in sessions))

        self.assertEqual(
            sum(session.execute_count for session in sessions),
            len(memory_repository.MEMORY_TABLE_DDL),
        )
        self.assertEqual(sum(session.commit_count for session in sessions), 1)

    async def test_failed_initialization_can_be_retried(self):
        failing_session = _Session(fail_first_execute=True)
        recovered_session = _Session()

        with self.assertRaisesRegex(OSError, "database unavailable"):
            await MemoryRepository(failing_session).ensure_tables()
        await MemoryRepository(recovered_session).ensure_tables()

        self.assertEqual(recovered_session.execute_count, len(memory_repository.MEMORY_TABLE_DDL))
        self.assertEqual(recovered_session.commit_count, 1)
        self.assertTrue(memory_repository._memory_schema_ready)


if __name__ == "__main__":
    unittest.main()
