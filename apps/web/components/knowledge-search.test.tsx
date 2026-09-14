import { act, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

const { searchKnowledgeMock, useReducedMotionMock } = vi.hoisted(() => ({
  searchKnowledgeMock: vi.fn(),
  useReducedMotionMock: vi.fn(),
}));

vi.mock("@/lib/rag-api", async (importOriginal) => {
  const actual = (await importOriginal()) as Record<string, unknown>;
  return { ...actual, searchKnowledge: searchKnowledgeMock };
});

vi.mock("motion/react", async (importOriginal) => {
  const actual = (await importOriginal()) as Record<string, unknown>;
  return { ...actual, useReducedMotion: useReducedMotionMock };
});

import { RagApiError } from "@/lib/rag-api";

import { KnowledgeSearch } from "./knowledge-search";

const citation = {
  source_id: "refund-policy-v1",
  chunk_id: "a".repeat(64),
  page_number: 2,
  content: "退款需要订单本人提交。",
  score: 0.25,
};

describe("KnowledgeSearch", () => {
  beforeEach(() => {
    searchKnowledgeMock.mockReset();
    useReducedMotionMock.mockReset();
    useReducedMotionMock.mockReturnValue(false);
  });

  it("queries the knowledge base and renders expandable evidence fields", async () => {
    const user = userEvent.setup();
    searchKnowledgeMock.mockResolvedValue({ query: "退款政策", citations: [citation] });
    render(<KnowledgeSearch />);

    await user.type(screen.getByRole("textbox", { name: "知识库查询" }), "  退款政策  ");
    await user.click(screen.getByRole("button", { name: "检索知识库" }));

    expect(searchKnowledgeMock).toHaveBeenCalledWith("退款政策");
    expect(await screen.findByRole("region", { name: "知识库证据" })).toHaveTextContent(
      "refund-policy-v1",
    );
    expect(screen.getByRole("region", { name: "知识库证据" })).toHaveTextContent("1 条证据");
    expect(screen.getByText("第 2 页")).toBeInTheDocument();
    expect(screen.getAllByText("退款需要订单本人提交。")).toHaveLength(2);
    expect(screen.getByText(citation.chunk_id)).toBeInTheDocument();
    expect(screen.getByText("融合排序分 0.2500")).toBeInTheDocument();

    const details = screen.getByText("refund-policy-v1").closest("details");
    expect(details).not.toBeNull();
    await user.click(screen.getByText("refund-policy-v1"));
    expect(details).toHaveAttribute("open");
  });

  it("shows a no-result state without inventing evidence", async () => {
    const user = userEvent.setup();
    searchKnowledgeMock.mockResolvedValue({ query: "未知", citations: [] });
    render(<KnowledgeSearch />);

    await user.type(screen.getByRole("textbox", { name: "知识库查询" }), "未知");
    await user.keyboard("{Enter}");

    expect(await screen.findByText("未检索到相关知识库证据，请换一种问法重试。")).toBeInTheDocument();
    expect(screen.queryByRole("region", { name: "知识库证据" })).not.toBeInTheDocument();
  });

  it("shows a permission error as an error, not as an empty result", async () => {
    const user = userEvent.setup();
    searchKnowledgeMock.mockRejectedValue(
      new RagApiError("rag_forbidden", "当前用户没有知识库访问权限。", 403),
    );
    render(<KnowledgeSearch />);

    await user.type(screen.getByRole("textbox", { name: "知识库查询" }), "内部政策");
    await user.click(screen.getByRole("button", { name: "检索知识库" }));

    expect(await screen.findByRole("alert")).toHaveTextContent("当前用户没有知识库访问权限。");
    expect(screen.queryByText("未检索到相关知识库证据，请换一种问法重试。")).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "重试知识库检索" })).toBeInTheDocument();
  });

  it("disables duplicate submissions, announces loading, and retries stable errors", async () => {
    const user = userEvent.setup();
    let resolveSearch: ((value: { query: string; citations: [] }) => void) | undefined;
    searchKnowledgeMock
      .mockReturnValueOnce(
        new Promise((resolve) => {
          resolveSearch = resolve;
        }),
      )
      .mockResolvedValueOnce({ query: "重试", citations: [] });
    render(<KnowledgeSearch />);

    const input = screen.getByRole("textbox", { name: "知识库查询" });
    await user.type(input, "重试");
    await user.click(screen.getByRole("button", { name: "检索知识库" }));
    expect(input).toBeDisabled();
    expect(screen.getByRole("status")).toHaveTextContent("正在检索知识库");
    await user.keyboard("{Enter}");
    expect(searchKnowledgeMock).toHaveBeenCalledTimes(1);

    await act(async () => resolveSearch?.({ query: "重试", citations: [] }));
    expect(await screen.findByText("未检索到相关知识库证据，请换一种问法重试。")).toBeInTheDocument();

    searchKnowledgeMock.mockReset();
    searchKnowledgeMock
      .mockRejectedValueOnce(new RagApiError("rag_upstream_timeout", "知识库响应超时，请稍后重试。", 504))
      .mockResolvedValueOnce({ query: "网络", citations: [] });
    await user.clear(input);
    await user.type(input, "网络");
    await user.click(screen.getByRole("button", { name: "检索知识库" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("知识库响应超时，请稍后重试。");
    await user.click(screen.getByRole("button", { name: "重试知识库检索" }));
    await waitFor(() => expect(searchKnowledgeMock).toHaveBeenCalledTimes(2));
    expect(await screen.findByText("未检索到相关知识库证据，请换一种问法重试。")).toBeInTheDocument();
  });

  it("marks evidence as reduced motion when preferred", async () => {
    const user = userEvent.setup();
    useReducedMotionMock.mockReturnValue(true);
    searchKnowledgeMock.mockResolvedValue({ query: "退款", citations: [citation] });
    render(<KnowledgeSearch />);

    await user.type(screen.getByRole("textbox", { name: "知识库查询" }), "退款");
    await user.click(screen.getByRole("button", { name: "检索知识库" }));

    expect(await screen.findByRole("region", { name: "知识库证据" })).toHaveAttribute(
      "data-motion-mode",
      "reduced",
    );
  });

  it("uses the evidence count for multiple chunk-level citations", async () => {
    const user = userEvent.setup();
    searchKnowledgeMock.mockResolvedValue({
      query: "退款",
      citations: [
        citation,
        {
          ...citation,
          chunk_id: "b".repeat(64),
          source_id: "refund-policy-v1",
          page_number: 3,
          score: 0.12,
        },
      ],
    });
    render(<KnowledgeSearch />);

    await user.type(screen.getByRole("textbox", { name: "知识库查询" }), "退款");
    await user.click(screen.getByRole("button", { name: "检索知识库" }));

    const evidence = await screen.findByRole("region", { name: "知识库证据" });
    expect(evidence).toHaveTextContent("2 条证据");
    expect(evidence).not.toHaveTextContent(/SOURCES?|来源数量|相似度|相关度/);
    expect(evidence).toHaveTextContent("融合排序分 0.2500");
    expect(evidence).toHaveTextContent("融合排序分 0.1200");
  });
});
