"""One model setting for all query-runtime stages."""
import os


def resolve_model():
    return os.environ.get("OPENAI_MODEL", "").strip() or "gpt-4o"
