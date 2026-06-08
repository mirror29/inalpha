"""divinations —— 占卜台历史记录（狐神签独立模块）

Revision ID: 0014
Revises: 0013
Create Date: 2026-06-08

狐神签从「点按钮发 LLM 会话」改为「独立模块直算确定性占卜 + 服务端历史」。占卜引擎
（``packages/orchestration/src/divination``）是纯函数、确定性，由 mastra 自定义端点
``POST /divination/cast`` 直算后落本表，供模块回看历史记录。

设计：

- ``subject`` = 控制台身份（JWT.sub），做隶属/隔离；查询永远带 subject 过滤。
- ``reading`` 存完整 ``DivinationView`` 的 jsonb 快照（卦象/牌面 + disclaimer）——
  引擎确定性，但典籍文案可能随版本演进，落库快照保证历史回看与当时一致。
- 纯娱乐：本表数据**永不**进决策（create_plan / approve / execute / 回测）。
"""
from __future__ import annotations

from alembic import op

revision: str = "0014"
down_revision: str | None = "0013"
branch_labels: str | tuple[str, ...] | None = None
depends_on: str | tuple[str, ...] | None = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE divinations (
            id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            subject     TEXT NOT NULL,
            mode        TEXT NOT NULL
                CHECK (mode IN ('hexagram', 'tarotSingle', 'tarotThree')),
            question    TEXT NOT NULL,
            symbol      TEXT,
            kind        TEXT NOT NULL
                CHECK (kind IN ('hexagram', 'tarot')),
            reading     JSONB NOT NULL,
            created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )
        """
    )
    # 历史面板按 subject 倒序拉 —— (subject, created_at desc) 覆盖该查询
    op.execute(
        "CREATE INDEX divinations_subject_created_idx "
        "ON divinations (subject, created_at DESC)"
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS divinations_subject_created_idx")
    op.execute("DROP TABLE IF EXISTS divinations")
