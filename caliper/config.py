"""Default configuration."""

# LLM provider is pluggable. Anthropic is the default; OpenAI is fully supported.
DEFAULT_PROVIDER = "anthropic"  # overall default
PROVIDER_DEFAULT_MODEL = {
    "anthropic": "claude-opus-4-8",  # overall default model — most capable
    "openai": "gpt-5",
}

# Trust-gate defaults (one-sided risk control).
#   alpha = max tolerated rate of confident-but-wrong (auto-accepted) results
#   delta = confidence with which that bound holds
DEFAULT_ALPHA = 0.10
DEFAULT_DELTA = 0.05

# Executor
DEFAULT_TIMEOUT_SEC = 600
