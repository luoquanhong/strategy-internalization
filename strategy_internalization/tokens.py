def estimate_tokens(text: str) -> int:
    return int(len(text) / 1.5) + 1
