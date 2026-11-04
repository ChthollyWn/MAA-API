"""M76：检索索引三表、FTS5 虚拟表与触发器（v3 M76 规格 §4.1、v3 docs/03 §9）。

纯新增表，不碰既有表、不回填数据。

- ``knowledge_source``：每个检索来源一行，服务启动时按已注册的来源补齐；
  ``config`` 是 JSON ``{}``，``error`` 用 ``sa.Text()``。
- ``knowledge_document``：``source_id`` 用 ``ON DELETE CASCADE``（来源删了文档没有
  独立存在的意义）；``(source_id, external_id)`` 唯一，``meta`` 是 JSON ``{}``。
- ``knowledge_chunk``：自增主键，``id`` 同时是 FTS5 的 rowid；``document_id`` 用
  ``ON DELETE CASCADE``，``text`` 用 ``sa.Text()``，``embedding`` 是
  ``sa.LargeBinary()``（float32 小端序，没有向量时为空）；两条索引分别服务
  「按文档取有序分块」与「缺向量的分块扫描」。

随后四条原生 SQL 建 FTS5 虚拟表与三个同步触发器，与 ``models.KNOWLEDGE_FTS_DDL``
逐字相同（去掉 ``IF NOT EXISTS``；``END`` 后不加分号，交给 Alembic 渲染）。
触发器让 ``knowledge_chunk`` 的增删改自动维护全文索引，写入路径不需要手写 FTS 语句。
迁移里不做任何查询，离线 ``upgrade head --sql`` 可以直接渲染。

``downgrade`` 的顺序有讲究：先删三个触发器，再删虚拟表，然后两条索引与三张表。
删了虚拟表之后不能再先删分块行——``AFTER DELETE`` 触发器会往一张不存在的表里写。
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0030_knowledge_index"
down_revision: Union[str, Sequence[str], None] = "0029_artifacts_plans"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

#: 与 ``models.KNOWLEDGE_FTS_DDL`` 相同（去掉 IF NOT EXISTS）；END 后不加分号。
_FTS_STATEMENTS: tuple[str, ...] = (
    "CREATE VIRTUAL TABLE knowledge_chunk_fts USING fts5("
    "text, heading, content='knowledge_chunk', content_rowid='id', tokenize='trigram')",
    "CREATE TRIGGER knowledge_chunk_ai AFTER INSERT ON knowledge_chunk BEGIN "
    "INSERT INTO knowledge_chunk_fts(rowid, text, heading) VALUES (new.id, new.text, new.heading); END",
    "CREATE TRIGGER knowledge_chunk_ad AFTER DELETE ON knowledge_chunk BEGIN "
    "INSERT INTO knowledge_chunk_fts(knowledge_chunk_fts, rowid, text, heading) "
    "VALUES ('delete', old.id, old.text, old.heading); END",
    "CREATE TRIGGER knowledge_chunk_au AFTER UPDATE ON knowledge_chunk BEGIN "
    "INSERT INTO knowledge_chunk_fts(knowledge_chunk_fts, rowid, text, heading) "
    "VALUES ('delete', old.id, old.text, old.heading); "
    "INSERT INTO knowledge_chunk_fts(rowid, text, heading) VALUES (new.id, new.text, new.heading); END",
)


def upgrade() -> None:
    op.create_table(
        "knowledge_source",
        sa.Column("id", sa.String(length=48), nullable=False),
        sa.Column("kind", sa.String(length=24), nullable=False),
        sa.Column("title", sa.String(length=64), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False),
        sa.Column("trusted", sa.Boolean(), nullable=False),
        sa.Column("license", sa.String(length=64), nullable=True),
        sa.Column("config", sa.JSON(), nullable=False, server_default=sa.text("'{}'")),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("doc_count", sa.Integer(), nullable=False),
        sa.Column("chunk_count", sa.Integer(), nullable=False),
        sa.Column("last_indexed_at", sa.DateTime(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_knowledge_source"),
    )

    op.create_table(
        "knowledge_document",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("source_id", sa.String(length=48), nullable=False),
        sa.Column("external_id", sa.String(length=255), nullable=False),
        sa.Column("title", sa.String(length=255), nullable=False),
        sa.Column("url", sa.String(length=512), nullable=True),
        sa.Column("content_hash", sa.String(length=64), nullable=False),
        sa.Column("version", sa.String(length=64), nullable=True),
        sa.Column("fetched_at", sa.DateTime(), nullable=True),
        sa.Column("meta", sa.JSON(), nullable=False, server_default=sa.text("'{}'")),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(
            ["source_id"],
            ["knowledge_source.id"],
            name="fk_knowledge_document_source_id_knowledge_source",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_knowledge_document"),
        sa.UniqueConstraint(
            "source_id",
            "external_id",
            name="uq_knowledge_document_source_id_external_id",
        ),
    )

    op.create_table(
        "knowledge_chunk",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("document_id", sa.String(length=36), nullable=False),
        sa.Column("ord", sa.Integer(), nullable=False),
        sa.Column("heading", sa.String(length=255), nullable=True),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column("char_count", sa.Integer(), nullable=False),
        sa.Column("embedding", sa.LargeBinary(), nullable=True),
        sa.Column("embedding_model", sa.String(length=128), nullable=True),
        sa.Column("embedding_dim", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(
            ["document_id"],
            ["knowledge_document.id"],
            name="fk_knowledge_chunk_document_id_knowledge_document",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_knowledge_chunk"),
        # id 同时是 FTS5 的 rowid：必须是真正的 AUTOINCREMENT。
        sqlite_autoincrement=True,
    )
    op.create_index(
        "ix_knowledge_chunk_document_id_ord",
        "knowledge_chunk",
        ["document_id", "ord"],
    )
    op.create_index(
        "ix_knowledge_chunk_embedding_model",
        "knowledge_chunk",
        ["embedding_model"],
    )

    for statement in _FTS_STATEMENTS:
        op.execute(statement)


def downgrade() -> None:
    # 先删触发器，再删虚拟表；反过来的顺序会让删分块行时触发器写向不存在的表。
    op.execute("DROP TRIGGER IF EXISTS knowledge_chunk_au")
    op.execute("DROP TRIGGER IF EXISTS knowledge_chunk_ad")
    op.execute("DROP TRIGGER IF EXISTS knowledge_chunk_ai")
    op.execute("DROP TABLE IF EXISTS knowledge_chunk_fts")
    op.drop_index("ix_knowledge_chunk_embedding_model", table_name="knowledge_chunk")
    op.drop_index("ix_knowledge_chunk_document_id_ord", table_name="knowledge_chunk")
    op.drop_table("knowledge_chunk")
    op.drop_table("knowledge_document")
    op.drop_table("knowledge_source")
