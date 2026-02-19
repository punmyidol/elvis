import json
import sqlite3
import uuid
from datetime import datetime
from functools import lru_cache
from typing import List

from langchain_community.vectorstores import SQLiteVec
from langchain_core.documents import Document
from pydantic import BaseModel, Field

from elvis.data import create_db_connection
from elvis.models import create_embeddings

FIND_MEMORY_QUERY = """
SELECT text, metadata
FROM memories
WHERE json_extract(metadata, '$.user_id') = ?
"""

class Memory(BaseModel):
    id : str = Field(default_factory=lambda : str(uuid.uuid4()))
    content : str
    user_id : str
    created_at : datetime = Field(default_factory=datetime.now)
    importance : int = Field(5, ge=1, le=10)

    def to_document(self) -> Document:
        return Document(
            page_content = self.content,
            metadata={
                "memory_id" : self.id,
                "user_id" : self.user_id,
                "created_at" : self.created_at.isoformat(),
                "importance" : self.importance,
            },
        )

    @classmethod
    def from_document(cls, doc: Document) -> "Memory":
        return cls(
            id=doc.metadata.get("memory_id"),
            content=doc.page_content,
            user_id=doc.metadata.get("user_id"),
            created_at=datetime.fromisoformat(
                doc.metadata.get("created_at", datetime.now().isoformat())
            ),
            importance=int(doc.metadata.get("importance", 5)),
        )

class MemoryManager:
    def __init__(self, connection : sqlite3.Connection):
        self.connection = connection
        self.vectorstore = SQLiteVec(
            table="memories",
            connection=connection, 
            embedding=create_embeddings(),  
        )
    
    def save_memory(self, memory : Memory) -> str:
        doc = memory.to_document()
        self.vectorstore.add_documents([doc])
        return memory.id
    
    def retrieve_memories(self, query : str, user_id : str, k: int=5) -> List[Memory]:
        def filter_function(doc : Document) -> bool:
            return doc.metadata.get("user_id") == user_id
        
        documents = self.vectorstore.similarity_search(query, k=k, filter=filter_function)
        return [Memory.from_document(doc) for doc in documents]
    
    def update_memory(self, memory_id : str, new_content : str) -> str:
        # 1. Update DB
        cursor = self.connection.cursor()
        cursor.execute(
            "UPDATE memories SET text = ? WHERE json_extract(metadata, '$.id') = ?",
            (new_content, memory_id)
        )
        self.connection.commit()

        # 2. Recompute embedding
        new_embedding = self.vectorstore.embedding.embed_documents([new_content])[0]

        # 3. Update vectorstore
        # This depends on SQLiteVec API; you may need to remove and re-add
        self.vectorstore.delete(ids=[memory_id])
        doc = Document(page_content=new_content, metadata={"id": memory_id})
        self.vectorstore.add_documents([doc])
        
        return f"Memory: {memory_id} updated successfully"

    def delete_memory(self, memory_id : str) -> str:
        cursor = self.connection.cursor()
        cursor.execute(
            "DELETE FROM memories WHERE json_extract(metadata, '$.id') = ?",
            (memory_id,)
        )
        self.connection.commit()

        # Delete from vectorstore
        self.vectorstore.delete(ids=[memory_id])

        return f"Memory: {memory_id} deleted successfully"

    def find_all_memories(self, user_id : str) -> List[Memory]:
        cursor = self.connection.cursor()
        cursor.execute(
            FIND_MEMORY_QUERY,
            (user_id,),
        )
        rows = cursor.fetchall()

        memories = []
        
        for row in rows:
            document = Document(page_content=row[0], metadata=json.loads(row[1]))
            memories.append(Memory.from_document(document))
        return memories
    
@lru_cache(maxsize=1)
def create_memory_manager() -> MemoryManager:
    return MemoryManager(create_db_connection())

