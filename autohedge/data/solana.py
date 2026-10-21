"""Solana address syntax shared by read-only data adapters, not token safety checks."""

# Solana public keys are exactly 32 bytes, base58-encoded without leading zero loss.
_ALPHABET = "123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz"


def validate_mint(mint: str) -> str:
    if not isinstance(mint, str) or not 32 <= len(mint) <= 44:
        raise ValueError("Expected a Solana mint address")
    value = 0
    for char in mint:
        if char not in _ALPHABET:
            raise ValueError("Expected a Solana mint address")
        value = value * 58 + _ALPHABET.index(char)
    size = (value.bit_length() + 7) // 8 + len(mint) - len(mint.lstrip("1"))
    if size != 32:
        raise ValueError("Expected a 32-byte Solana mint address")
    return mint
