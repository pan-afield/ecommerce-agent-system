import argparse
import asyncio
from collections.abc import Sequence
from pathlib import Path
from typing import cast

from app.core.config import Settings, get_settings
from app.core.database import create_database_engine
from app.core.redis import create_redis_client, invalidate_rag_cache
from app.rag.chunking import ALLOWED_VISIBILITIES, KnowledgeChunk
from app.rag.ingestion import (
    ingest_knowledge_chunks,
    load_policy_files_chunks,
    rebuild_knowledge_chunks,
)
from app.rag.local_embeddings import create_local_embeddings


async def write_policy_chunks(
    settings: Settings,
    chunks: list[KnowledgeChunk],
    *,
    rebuild: bool,
) -> int:
    """为一次 CLI 导入创建资源，并保证数据库引擎最终释放。"""
    embeddings = create_local_embeddings(settings)
    engine = create_database_engine(settings.database_url)
    redis_client = create_redis_client(settings)

    try:
        if rebuild:
            count = await rebuild_knowledge_chunks(
                engine,
                chunks,
                embeddings,
                embedding_model=settings.rag_embedding_model,
            )
        else:
            count = await ingest_knowledge_chunks(
                engine,
                chunks,
                embeddings,
                embedding_model=settings.rag_embedding_model,
            )

        if redis_client is not None:
            await invalidate_rag_cache(redis_client)

        return count
    finally:
        if redis_client is not None:
            await redis_client.aclose()

        await engine.dispose()


def build_argument_parser() -> argparse.ArgumentParser:
    """定义文本、Markdown 和 PDF 导入参数。"""
    parser = argparse.ArgumentParser(
        description="将企业政策文档导入本地 RAG 知识库。",
    )
    parser.add_argument(
        "--visibility",
        choices=ALLOWED_VISIBILITIES,
        default="PUBLIC",
        help="知识块可见性，默认 PUBLIC。",
    )
    parser.add_argument(
        "file_paths",
        nargs="+",
        type=Path,
        help="一个或多个 .txt、.md 或 .pdf 文件。",
    )
    parser.add_argument(
        "--rebuild",
        action="store_true",
        help="使用当前模型重新生成并原子替换全部知识块。",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """解析命令行参数并运行一次导入任务。"""
    arguments = build_argument_parser().parse_args(argv)
    file_paths = cast(list[Path], arguments.file_paths)
    rebuild = cast(bool, arguments.rebuild)
    visibility = cast(str, arguments.visibility)
    chunks = load_policy_files_chunks(
        file_paths,
        visibility=visibility,
    )
    count = asyncio.run(
        write_policy_chunks(
            get_settings(),
            chunks,
            rebuild=rebuild,
        )
    )

    print(f"已写入 {count} 个知识块。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
