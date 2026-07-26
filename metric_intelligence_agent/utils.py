"""
Shared utility functions used across agent.py, graph.py,
and the eval layer. No project dependencies -- safe to
import from anywhere.
"""


def compute_cost(input_tokens, output_tokens) -> float:
    """
    Compute estimated API cost using gpt-4o pricing.
    $2.50 per 1M input tokens, $10.00 per 1M output tokens.
    """
    return input_tokens / 1_000_000 * 2.50 + output_tokens / 1_000_000 * 10.00
