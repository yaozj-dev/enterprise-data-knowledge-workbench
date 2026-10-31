"""验证应用访问演示数仓时使用的账号与最小权限。"""

import asyncio

from sqlalchemy import text

from app.clients.mysql_client_manager import dw_mysql_client_manager

WRITE_PRIVILEGES = ("INSERT", "UPDATE", "DELETE", "CREATE", "ALTER", "DROP")


def grants_are_read_only(grants: list[str], database: str) -> bool:
    """确认授权中包含目标库的 SELECT，且不存在常见写权限。"""
    normalized_grants = "\n".join(grants).upper()
    database_scope = f"ON `{database.upper()}`.*"
    has_select = "GRANT SELECT" in normalized_grants and database_scope in normalized_grants
    has_write_access = "ALL PRIVILEGES" in normalized_grants or any(
        privilege in normalized_grants for privilege in WRITE_PRIVILEGES
    )
    return has_select and not has_write_access


async def verify_readonly_dw_access() -> None:
    """以应用配置创建连接，并验证账号、授权和最基本的读查询。"""
    dw_mysql_client_manager.init()
    try:
        async with dw_mysql_client_manager.session_factory() as session:
            current_user = (
                await session.execute(text("SELECT CURRENT_USER()"))
            ).scalar_one()
            grants = [
                row[0]
                for row in (
                    await session.execute(text("SHOW GRANTS FOR CURRENT_USER()"))
                ).fetchall()
            ]
            order_count = (
                await session.execute(text("SELECT COUNT(*) FROM fact_order"))
            ).scalar_one()

        configured_user = dw_mysql_client_manager.config.user
        actual_username = str(current_user).split("@", maxsplit=1)[0]
        if actual_username.lower() != configured_user.lower():
            raise RuntimeError(
                "数仓实际连接账号与应用配置不一致："
                f"configured={configured_user}, actual={current_user}"
            )
        if not grants_are_read_only(grants, dw_mysql_client_manager.config.database):
            raise RuntimeError(
                "数仓账号不符合目标库只读权限要求，请检查 agent_readonly 的 GRANT。"
            )

        print("Read-only DW access verification passed")
        print(f"configured_user={configured_user}")
        print(f"mysql_current_user={current_user}")
        print(f"fact_order_count={order_count}")
        for grant in grants:
            print(f"grant={grant}")
    finally:
        await dw_mysql_client_manager.close()


if __name__ == "__main__":
    asyncio.run(verify_readonly_dw_access())
