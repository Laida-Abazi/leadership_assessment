import os

from dotenv import load_dotenv
from openai import OpenAI

load_dotenv()

OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")

# Model IDs for use in routes
MODEL_MINI = "gpt-5-mini"    # smaller / faster
MODEL_FULL = "gpt-5.2"       # larger / more capable

_client: OpenAI | None = None


def get_openai_client() -> OpenAI:
    """Return configured OpenAI client. Uses OPENAI_API_KEY from env."""
    global _client
    if _client is None:
        if not OPENAI_API_KEY:
            raise ValueError("OPENAI_API_KEY is not set in environment")
        _client = OpenAI(api_key=OPENAI_API_KEY)
    return _client
