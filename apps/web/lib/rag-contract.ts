import {
  BACKEND_RAG_ERROR_CODES,
  CLIENT_RAG_ERROR_CODES,
  PROXY_RAG_ERROR_CODES,
  type RagError,
  type RagSearchResponse,
} from "@/types/rag";

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null;
}

function isNonEmptyString(value: unknown): value is string {
  return typeof value === "string" && value.length > 0;
}

export function isKnowledgeCitation(value: unknown): boolean {
  return (
    isRecord(value) &&
    isNonEmptyString(value.source_id) &&
    isNonEmptyString(value.chunk_id) &&
    (value.page_number === null ||
      (typeof value.page_number === "number" && Number.isInteger(value.page_number) && value.page_number > 0)) &&
    isNonEmptyString(value.content) &&
    typeof value.score === "number" &&
    Number.isFinite(value.score)
  );
}

export function isRagSearchResponse(value: unknown): value is RagSearchResponse {
  return (
    isRecord(value) &&
    isNonEmptyString(value.query) &&
    Array.isArray(value.citations) &&
    value.citations.every(isKnowledgeCitation)
  );
}

export function isRagErrorCode(value: unknown) {
  return (
    typeof value === "string" &&
    [
      ...BACKEND_RAG_ERROR_CODES,
      ...PROXY_RAG_ERROR_CODES,
      ...CLIENT_RAG_ERROR_CODES,
    ].includes(value as never)
  );
}

export function isRagError(value: unknown): value is RagError {
  return (
    isRecord(value) &&
    isRecord(value.error) &&
    isRagErrorCode(value.error.code) &&
    isNonEmptyString(value.error.message)
  );
}

export function isBackendRagError(value: unknown): value is RagError {
  return (
    isRagError(value) &&
    (BACKEND_RAG_ERROR_CODES as readonly string[]).includes(value.error.code)
  );
}
