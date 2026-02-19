from enum import Enum
from dataclasses import dataclass
from pathlib import Path
import os
import sys
import random

class ModelProvider(str, Enum):
    OLLAMA = "ollama"

@dataclass
class ModelConfig:
    name : str
    temperature : float
    provider : ModelProvider

QWEN = ModelConfig("qwen2.5:7b-instruct", 0.0, ModelProvider.OLLAMA)

class Config:
    SEED = 42
    CHAT_MODEL = QWEN
    TOOL_MODEL = QWEN

    class Path:
        APP_HOME = Path(os.getenv("APP_HOME", Path(__file__).parent.parent))
        DATA_DIR = APP_HOME / "data"
        LOGS_DIR = DATA_DIR / "elvis.sqlite"

    class Memory:
        EMBEDDING_MODEL = "BAAI/bge-small-en-v1.5"
        MAX_RECALL_COUNT = 5

def seed_everything(seed : int):
    random.seed(seed)

def configure_logging():
    config = {
        "handlers": [
            {
                "sink": sys.stdout,
                "colorize": True,
                
            }
        ]
    }