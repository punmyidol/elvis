from functools import lru_cache

from langchain_community.embeddings.fastembed import FastEmbedEmbeddings
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_ollama import ChatOllama

from elvis.config import Config, ModelConfig, ModelProvider

@lru_cache(maxsize=1)
def create_embeddings() -> FastEmbedEmbeddings:
    return FastEmbedEmbeddings(model=Config.Memory.EMBEDDING_MODEL)

def create_llm(model_config : ModelConfig) -> BaseChatModel:
    return ChatOllama(
        model=model_config.name,
        temperature=model_config.temperature,
        verbose=False,
        keep_alive=1,
    )