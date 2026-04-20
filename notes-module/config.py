import os

OLLAMA_MODEL    = os.getenv("ELVIS_MODEL", "qwen3-vl:8b")
QUERY_MODEL     = os.getenv("NOTES_QUERY_MODEL", "qwen2.5:7b")
OLLAMA_BASE_URL = os.getenv("OLLAMA_BASE_URL", "http://localhost:11434")
VAULT_ROOT      = os.getenv("NOTES_VAULT_ROOT", "/Users/punmyidol/Library/Mobile Documents/iCloud~md~obsidian/Documents/elvis")
DPI             = int(os.getenv("NOTES_DPI", "150"))

SUBJECT_MAP = {
    "compsci":      "A-Levels/Com Sci",
    "puremaths":    "A-Levels/Pure Maths",
    "furthermaths": "A-Levels/Further Maths",
    "physics":      "A-Levels/Physics",
}

TOPIC_TAGS = {
    "compsci": [
        "algorithms", "data-structures", "networking", "databases",
        "boolean-logic", "oop", "functional-programming", "hardware",
        "os-concepts", "security", "computational-thinking", "theory-of-computation",
        "machine-learning", "neural-networks", "llm", "ai", "deep-learning",
        "natural-language-processing", "computer-vision", "reinforcement-learning",
    ],
    "puremaths": [
        "algebra", "calculus", "trigonometry", "functions", "sequences-series",
        "coordinate-geometry", "exponentials-logs", "proof", "vectors",
        "statistics", "probability", "numerical-methods",
    ],
    "furthermaths": [
        "complex-numbers", "matrices", "further-calculus", "polar-coordinates",
        "hyperbolic-functions", "differential-equations", "further-vectors",
        "further-statistics", "decision-maths", "mechanics",
    ],
    "physics": [
        "mechanics", "electricity", "waves", "thermal-physics", "nuclear-physics",
        "fields", "quantum-physics", "measurement-uncertainty", "optics",
        "astrophysics", "capacitors", "particle-physics",
    ],
}

NOTE_TYPE_TAGS = ["lecture", "tutorial", "revision", "worked-example", "formula-sheet"]

ALL_TOPICS: set[str] = {t for tags in TOPIC_TAGS.values() for t in tags}
VALID_TAGS: set[str] = set(SUBJECT_MAP) | ALL_TOPICS | set(NOTE_TYPE_TAGS)
