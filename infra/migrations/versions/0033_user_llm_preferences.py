"""user_llm_preferences —— 多租户 LLM 配置的 preferences 字段

Revision ID: 0033
Revises: 0032
Create Date: 2026-07-10

背景:
  当前 LLM 配置硬编码在根目录 .env，所有用户共享同一套 API key。
  本迁移扩展 users 表，增加 preferences JSONB 字段，支持用户级多配置存储。

字段设计:
  preferences JSONB DEFAULT '{}' —— 存储用户级配置，结构如下：
  {
    "llm": {
      "configs": [
        {
          "id": "cfg-xxx",
          "provider": "deepseek|anthropic|openai|gemini|kimi|zhipu|custom",
          "model": "可选，留空用默认旗舰",
          "api_key_encrypted": "base64 ciphertext",
          "api_key_nonce": "base64 12-byte nonce",
          "api_key_tag": "base64 16-byte auth tag",
          "custom_base_url": "自定义端点（中转站）",
          "custom_provider_name": "自定义供应商显示名",
          "label": "用户自定义标签",
          "created_at": "ISO 8601",
          "updated_at": "ISO 8601"
        }
      ],
      "active_config_id": "当前激活配置 ID"
    }
  }

安全:
  API key 使用 AES-256-GCM 加密存储，密钥来自环境变量 LLM_CONFIG_ENCRYPTION_KEY。
  解密后的明文仅存在于请求级内存，永不日志记录。

支持:
  - 多配置存储（不同供应商/同一供应商多 key）
  - 快速切换激活配置
  - 自定义端点（中转站/私有部署）
  - 向后兼容（未配置时回落到系统 .env）
"""
from __future__ import annotations

from alembic import op

revision: str = "0033"
down_revision: str | None = "0032"
branch_labels: str | tuple[str, ...] | None = None
depends_on: str | tuple[str, ...] | None = None


def upgrade() -> None:
    op.execute(
        """
        ALTER TABLE users ADD COLUMN IF NOT EXISTS preferences JSONB DEFAULT '{}'
        """
    )
    # 索引：快速查询已配置 LLM 的用户（preferences->>'llm' 非空）
    op.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_users_has_llm_config
        ON users ((preferences->>'llm') IS NOT NULL)
        WHERE (preferences->>'llm') IS NOT NULL
        """
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS idx_users_has_llm_config")
    op.execute("ALTER TABLE users DROP COLUMN IF EXISTS preferences")