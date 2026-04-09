"""
rag — Standalone RAG module.

Usage:
    from rag import RAG

    r = RAG()
    r.add_file("notes.pdf")
    r.add_url("https://example.com/article")
    r.add_text("Some raw text...", source_id="my-note")

    answer = r.query("What does the document say about X?")
    results = r.search("X", top_k=5)  # [(source_id, content, distance), ...]
"""

import os
from typing import List, Tuple

from .config import DB_PATH, EMBED_MODEL, OLLAMA_BASE_URL, OLLAMA_MODEL, TOP_K
from . import db as _db
from .loader import load_and_chunk, chunk_text


class RAG:
    def __init__(
        self,
        db_path: str = DB_PATH,
        embed_model: str = EMBED_MODEL,
        ollama_url: str = OLLAMA_BASE_URL,
        llm_model: str = OLLAMA_MODEL,
    ):
        self.db_path = db_path
        self.embed_model = embed_model
        self.ollama_url = ollama_url
        self.llm_model = llm_model
        _db.init_db(db_path)

    # ------------------------------------------------------------------
    # Ingest
    # ------------------------------------------------------------------

    def add_file(self, path: str) -> int:
        """Ingest a file (txt, md, pdf, csv). Returns number of chunks stored."""
        source_id = os.path.basename(path)
        chunks = load_and_chunk(path)
        self._upsert_chunks(source_id, chunks)
        return len(chunks)

    def add_url(self, url: str) -> int:
        """Fetch and ingest a web page. Returns number of chunks stored."""
        chunks = load_and_chunk(url)
        self._upsert_chunks(url, chunks)
        return len(chunks)

    def add_text(self, text: str, source_id: str) -> int:
        """Ingest raw text with a given source_id. Returns number of chunks stored."""
        chunks = chunk_text(text)
        self._upsert_chunks(source_id, chunks)
        return len(chunks)

    def _upsert_chunks(self, source_id: str, chunks: List[str]) -> None:
        for i, chunk in enumerate(chunks):
            _db.upsert(
                source_id=source_id,
                chunk_index=i,
                content=chunk,
                db_path=self.db_path,
                base_url=self.ollama_url,
                embed_model=self.embed_model,
            )

    # ------------------------------------------------------------------
    # Retrieve
    # ------------------------------------------------------------------

    def search(self, query: str, top_k: int = TOP_K) -> List[Tuple[str, str, float]]:
        """Semantic search. Returns [(source_id, content, distance), ...]. Lower = more similar."""
        return _db.search(
            query_text=query,
            top_k=top_k,
            db_path=self.db_path,
            base_url=self.ollama_url,
            embed_model=self.embed_model,
        )

    def query(self, question: str, top_k: int = TOP_K) -> str:
        """Retrieve relevant chunks and ask the LLM to answer the question."""
        from langchain_ollama import ChatOllama
        from langchain_core.messages import HumanMessage

        results = self.search(question, top_k=top_k)
        if not results:
            context = "No relevant documents found."
        else:
            context = "\n\n---\n\n".join(
                f"[{source_id}]\n{content}" for source_id, content, _ in results
            )

        prompt = (
            f"Use the following context to answer the question. "
            f"If the answer is not in the context, say so.\n\n"
            f"Context:\n{context}\n\n"
            f"Question: {question}\n"
            f"Answer:"
        )

        llm = ChatOllama(
            model=self.llm_model,
            base_url=self.ollama_url,
            temperature=0.2,
            reasoning=False,
        )
        response = llm.invoke([HumanMessage(content=prompt)])
        return response.content

    # ------------------------------------------------------------------
    # Manage
    # ------------------------------------------------------------------

    def list_sources(self) -> List[str]:
        """Return all unique source_ids in the database."""
        return _db.list_sources(self.db_path)

    def remove_source(self, source_id: str) -> None:
        """Delete all chunks associated with a source_id."""
        _db.delete_source(source_id, self.db_path)

    def clear(self) -> None:
        """Delete all vectors from the database."""
        _db.clear_all(self.db_path)
