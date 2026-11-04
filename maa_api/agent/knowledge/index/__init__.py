"""知识检索索引层（M76）。

本包按里程碑拆分：``chunker``（预处理与分块）、``fts``（全文与 LIKE 回退）、
``vectors``（BLOB 与内存矩阵）、``embeddings``（向量服务与解析）。索引服务与
检索器随后在本包里补齐。
"""

from maa_api.agent.knowledge.index.chunker import (
    CHUNKER_VERSION,
    ChunkDraft,
    chunk_markdown,
    chunker_signature,
    normalize_markdown,
)
from maa_api.agent.knowledge.index.embeddings import (
    EmbeddingProvider,
    EmbeddingResolver,
    EmbeddingStatus,
    LocalOnnxEmbeddingProvider,
    NullEmbeddingProvider,
    RemoteEmbeddingProvider,
)
from maa_api.agent.knowledge.index.fts import (
    FtsPlan,
    LexicalHit,
    build_fts_plan,
    search_lexical,
)
from maa_api.agent.knowledge.index.vectors import (
    VectorCache,
    VectorMatrix,
    pack_vector,
    top_k,
    unpack_vector,
)

__all__ = [
    "CHUNKER_VERSION",
    "ChunkDraft",
    "EmbeddingProvider",
    "EmbeddingResolver",
    "EmbeddingStatus",
    "FtsPlan",
    "LexicalHit",
    "LocalOnnxEmbeddingProvider",
    "NullEmbeddingProvider",
    "RemoteEmbeddingProvider",
    "VectorCache",
    "VectorMatrix",
    "build_fts_plan",
    "chunk_markdown",
    "chunker_signature",
    "normalize_markdown",
    "pack_vector",
    "search_lexical",
    "top_k",
    "unpack_vector",
]
