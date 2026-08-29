from pydantic import BaseModel


class KnowledgeCitationResponse(BaseModel):
    source_id: str
    chunk_id: str
    page_number: int | None
    content: str
    score: float
