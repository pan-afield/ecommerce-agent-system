export const RAG_QUERY_MAX_LENGTH = 500;
export const RAG_LIMIT_MAX = 10;

export const BACKEND_RAG_ERROR_CODES = [
  "rag_not_configured",
  "rag_invalid_query",
  "rag_embedding_unavailable",
  "rag_database_incompatible",
  "rag_forbidden",
] as const;

export const PROXY_RAG_ERROR_CODES = [
  "rag_invalid_request",
  "rag_unauthorized",
  "rag_auth_unavailable",
  "rag_upstream_unreachable",
  "rag_upstream_timeout",
  "rag_invalid_upstream_response",
] as const;

export const CLIENT_RAG_ERROR_CODES = [
  "rag_network_error",
  "rag_invalid_response",
] as const;

export type BackendRagErrorCode = (typeof BACKEND_RAG_ERROR_CODES)[number];
export type ProxyRagErrorCode = (typeof PROXY_RAG_ERROR_CODES)[number];
export type ClientRagErrorCode = (typeof CLIENT_RAG_ERROR_CODES)[number];
export type RagErrorCode =
  | BackendRagErrorCode
  | ProxyRagErrorCode
  | ClientRagErrorCode;

export interface KnowledgeCitation {
  source_id: string;
  chunk_id: string;
  page_number: number | null;
  content: string;
  score: number;
}

export interface RagSearchResponse {
  query: string;
  citations: KnowledgeCitation[];
}

export interface RagErrorDetail {
  code: RagErrorCode;
  message: string;
}

export interface RagError {
  error: RagErrorDetail;
}
