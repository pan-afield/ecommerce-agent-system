from hashlib import sha256

import pytest

from app.rag.chunking import (
    DEFAULT_MAX_CHARS,
    KnowledgeChunk,
    chunk_policy_text,
)


def expected_chunk_id(source_id: str, chunk_index: int, content: str) -> str:
    payload = f"{source_id}\0{chunk_index}\0{content}".encode()
    return sha256(payload).hexdigest()


def expected_page_chunk_id(
    source_id: str,
    page_number: int,
    chunk_index: int,
    content: str,
) -> str:
    payload = f"{source_id}\0page:{page_number}\0{chunk_index}\0{content}".encode()
    return sha256(payload).hexdigest()


def test_chunk_policy_text_creates_traceable_paragraph_chunks() -> None:
    chunks = chunk_policy_text(
        "  refund-policy-v1  ",
        "退款申请需由订单本人提交。\n\n审批通过后进入退款处理。",
    )

    assert chunks == [
        KnowledgeChunk(
            chunk_id=expected_chunk_id(
                "refund-policy-v1",
                0,
                "退款申请需由订单本人提交。",
            ),
            source_id="refund-policy-v1",
            chunk_index=0,
            content="退款申请需由订单本人提交。",
        ),
        KnowledgeChunk(
            chunk_id=expected_chunk_id(
                "refund-policy-v1",
                1,
                "审批通过后进入退款处理。",
            ),
            source_id="refund-policy-v1",
            chunk_index=1,
            content="审批通过后进入退款处理。",
        ),
    ]


def test_chunk_policy_text_is_stable_across_line_endings() -> None:
    linux = chunk_policy_text("policy-v1", "第一段。\n\n第二段。")
    windows = chunk_policy_text("policy-v1", "第一段。\r\n\r\n第二段。")
    old_mac = chunk_policy_text("policy-v1", "第一段。\r\r第二段。")

    assert windows == linux
    assert old_mac == linux
    assert chunk_policy_text("policy-v1", "第一段。\n\n第二段。") == linux


def test_chunk_policy_text_ignores_empty_paragraphs() -> None:
    chunks = chunk_policy_text(
        "policy-v1",
        "  \n\n第一段。\n\n\n\n  第二段。  \n\n",
    )

    assert [chunk.content for chunk in chunks] == ["第一段。", "第二段。"]
    assert [chunk.chunk_index for chunk in chunks] == [0, 1]


def test_chunk_policy_text_preserves_page_provenance_in_chunk_and_id() -> None:
    page_one = chunk_policy_text(
        "refund-policy-v1",
        "相同政策内容。",
        page_number=1,
    )
    page_two = chunk_policy_text(
        "refund-policy-v1",
        "相同政策内容。",
        page_number=2,
    )

    assert page_one[0].page_number == 1
    assert page_two[0].page_number == 2
    assert page_one[0].chunk_id == expected_page_chunk_id(
        "refund-policy-v1",
        1,
        0,
        "相同政策内容。",
    )
    assert page_two[0].chunk_id == expected_page_chunk_id(
        "refund-policy-v1",
        2,
        0,
        "相同政策内容。",
    )
    assert page_one[0].chunk_id != page_two[0].chunk_id


@pytest.mark.parametrize(
    ("text_length", "expected_lengths"),
    [
        (800, [800]),
        (801, [800, 1]),
        (1700, [800, 800, 100]),
    ],
)
def test_chunk_policy_text_bounds_long_paragraphs(
    text_length: int,
    expected_lengths: list[int],
) -> None:
    chunks = chunk_policy_text(
        "long-policy-v1",
        "知" * text_length,
        page_number=7,
    )

    assert [len(chunk.content) for chunk in chunks] == expected_lengths
    assert [chunk.chunk_index for chunk in chunks] == list(range(len(chunks)))
    assert all(chunk.page_number == 7 for chunk in chunks)
    assert "".join(chunk.content for chunk in chunks) == "知" * text_length
    assert all(len(chunk.content) <= DEFAULT_MAX_CHARS for chunk in chunks)


@pytest.mark.parametrize("max_chars", [0, -1])
def test_chunk_policy_text_rejects_non_positive_max_chars(max_chars: int) -> None:
    with pytest.raises(ValueError, match="max_chars must be greater than zero"):
        chunk_policy_text("policy-v1", "政策内容。", max_chars=max_chars)


@pytest.mark.parametrize("page_number", [0, -1])
def test_chunk_policy_text_rejects_non_positive_page_number(page_number: int) -> None:
    with pytest.raises(ValueError, match="page_number must be greater than zero"):
        chunk_policy_text("policy-v1", "政策内容。", page_number=page_number)


@pytest.mark.parametrize("text", ["", "   ", "\r\n\r\n"])
def test_chunk_policy_text_returns_empty_list_for_empty_text(text: str) -> None:
    assert chunk_policy_text("policy-v1", text) == []


@pytest.mark.parametrize("source_id", ["", "   ", "\n\t"])
def test_chunk_policy_text_rejects_blank_source_id(source_id: str) -> None:
    with pytest.raises(ValueError, match="source_id must not be empty or whitespace"):
        chunk_policy_text(source_id, "政策内容。")
