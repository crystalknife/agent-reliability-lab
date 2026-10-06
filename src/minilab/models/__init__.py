"""Model adapters for the Mini Agent Reliability Lab."""

from .openai import make_openai_model
from .openrouter import make_openrouter_model, DEFAULT_MODEL as OPENROUTER_DEFAULT_MODEL
from .groq import make_groq_model, DEFAULT_MODEL as GROQ_DEFAULT_MODEL
from .gemini import make_gemini_model, DEFAULT_MODEL as GEMINI_DEFAULT_MODEL
from .omniroute import (
    make_omniroute_model,
    DEFAULT_MODEL as OMNIROUTE_DEFAULT_MODEL,
    OMNIROUTE_BASE_URL,
)
from .local import (
    make_local_model,
    DEFAULT_MODEL as LOCAL_DEFAULT_MODEL,
    LOCAL_BASE_URL,
    MODEL_VERSION as LOCAL_MODEL_VERSION,
)

__all__ = [
    "make_openai_model",
    "make_openrouter_model",
    "OPENROUTER_DEFAULT_MODEL",
    "make_groq_model",
    "GROQ_DEFAULT_MODEL",
    "make_gemini_model",
    "GEMINI_DEFAULT_MODEL",
    "make_omniroute_model",
    "OMNIROUTE_DEFAULT_MODEL",
    "OMNIROUTE_BASE_URL",
    "make_local_model",
    "LOCAL_DEFAULT_MODEL",
    "LOCAL_BASE_URL",
    "LOCAL_MODEL_VERSION",
]
