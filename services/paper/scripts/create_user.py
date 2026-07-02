"""创建 / 改密一个登录用户。

多用户登录暂不做注册 UI —— 初始用户和改密都走本 CLI。密码用 argon2 哈希后落
``users`` 表,``ON CONFLICT (subject) DO UPDATE`` 幂等(重跑 = 改密 / 改邮箱)。

用法::

    # 作者:沿用 console:dev subject → 继承现有模拟盘 / 会话历史
    uv --project services/paper run python services/paper/scripts/create_user.py \
        --email me@example.com --password 's3cr3t' --subject console:dev

    # 新用户:不给 --subject 则自动生成 user:<uuid4> → 独立空账户
    uv --project services/paper run python services/paper/scripts/create_user.py \
        --email bob@example.com --password 'hunter2'

    # 容器内(生产 paper 镜像已含 DB 依赖 + DATABASE_URL 环境):
    docker compose -f infra/docker-compose.prod.yml run --rm paper \
        uv --project paper run python scripts/create_user.py --email ... --password ... --subject console:dev

约束:

- 密码经 argon2 哈希,明文不落库、不打日志(仅 argparse 短暂持有)。
- ``--subject`` 是 JWT ``sub``,也是 paper ``account_id_from_sub`` 的派生源;改它 = 换账户。
"""
from __future__ import annotations

import argparse
import asyncio
import logging
import os
from uuid import uuid4

from argon2 import PasswordHasher
from inalpha_shared.db import close_pool, get_conn, init_pool

logger = logging.getLogger(__name__)

_hasher = PasswordHasher()


async def _upsert_user(
    *,
    subject: str,
    email: str,
    password: str,
    username: str | None,
    roles: list[str],
) -> None:
    password_hash = _hasher.hash(password)
    async with get_conn() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                """
                INSERT INTO users (subject, email, username, password_hash, roles)
                VALUES (%s, %s, %s, %s, %s)
                ON CONFLICT (subject) DO UPDATE SET
                    email         = EXCLUDED.email,
                    username      = EXCLUDED.username,
                    password_hash = EXCLUDED.password_hash,
                    roles         = EXCLUDED.roles,
                    updated_at    = now()
                """,
                (subject, email, username, password_hash, roles),
            )
            await conn.commit()


async def _amain(args: argparse.Namespace) -> int:
    db_url = os.environ.get(
        "DATABASE_URL",
        "postgresql+psycopg://quant:devpass@localhost:5433/inalpha",
    )
    subject = args.subject or f"user:{uuid4()}"
    roles = [r.strip() for r in (args.roles or "").split(",") if r.strip()]

    await init_pool(db_url)
    try:
        await _upsert_user(
            subject=subject,
            email=args.email,
            password=args.password,
            username=args.username,
            roles=roles,
        )
    finally:
        await close_pool()

    print(f"✔ 用户已写入:email={args.email!r} subject={subject!r} roles={roles}")
    print("  用该邮箱 + 密码在 dashboard /login 登录即可。")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="创建 / 改密一个登录用户(argon2 落库)。")
    parser.add_argument("--email", required=True, help="登录邮箱(大小写不敏感唯一)")
    parser.add_argument("--password", required=True, help="明文密码(会被 argon2 哈希)")
    parser.add_argument(
        "--subject",
        default=None,
        help="JWT sub(= account 派生源)。作者继承现有数据用 'console:dev';省略则生成 user:<uuid4>",
    )
    parser.add_argument("--username", default=None, help="可选备用登录名")
    parser.add_argument("--roles", default="", help="逗号分隔角色(预留,v1 不做权限门)")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO)
    return asyncio.run(_amain(args))


if __name__ == "__main__":
    raise SystemExit(main())
