"""
Jupiter Ultra Swap API – execute signed transactions and get wallet holdings.
API docs: https://dev.jup.ag (see /ultra/v1).
"""

import base64
import json
from typing import Dict

from autohedge.config import load_settings
from autohedge.errors import ConfigurationError
from autohedge.providers import request_json
from solders.keypair import Keypair
from solders.message import to_bytes_versioned
from solders.transaction import VersionedTransaction

JUPITER_ULTRA_BASE = "https://api.jup.ag/ultra/v1"


def _headers() -> Dict[str, str]:
    """
    Construct headers for requests to the Jupiter Ultra API.

    Reads the API key from the JUPITER_API_KEY environment variable (in .env).
    If present, adds it as 'x-api-key' header; otherwise, returns an empty dict.

    Returns
    -------
    Dict[str, str]
        HTTP headers to authenticate with Jupiter APIs.
    """
    return load_settings().headers("jupiter")


def _get_keypair() -> Keypair:
    """
    Load the Solana wallet keypair from environment.

    Reads the base58-encoded private key from the SOLANA_PRIVATE_KEY
    environment variable (in .env), and returns a Keypair for signing
    and transaction execution.

    Returns
    -------
    Keypair
        A Solana Keypair object to sign transactions.

    Raises
    ------
    ValueError
        If SOLANA_PRIVATE_KEY is missing or invalid.
    """
    secret = load_settings().solana_private_key
    raw = secret.get_secret_value() if secret else None
    if not raw or not raw.strip():
        raise ValueError("SOLANA_PRIVATE_KEY is required in .env to sign transactions")
    try:
        return Keypair.from_base58_string(raw.strip())
    except Exception:
        raise ConfigurationError("Invalid SOLANA_PRIVATE_KEY") from None


def _get_wallet_pubkey() -> str:
    """
    Get the wallet's public key (base58).

    Derives the public key from the private key found in
    SOLANA_PRIVATE_KEY in .env.

    Returns
    -------
    str
        The wallet's public key as a base58 string.
    """
    return str(_get_keypair().pubkey())


def get_order(input_mint: str, output_mint: str, amount: str) -> str:
    """
    Request a base64-encoded unsigned swap transaction for use with execute_trade (Jupiter Ultra).

    Taker, receiver, and payer are always the wallet from SOLANA_PRIVATE_KEY in .env.
    Returns a quote/order containing `transaction` (unsigned base64) and `requestId`.
    Pass those to execute_trade() to sign and execute.

    Parameters
    ----------
    input_mint : str
        Input token mint address (e.g. SOL: So11111111111111111111111111111111111111112).
    output_mint : str
        Output token mint address (e.g. USDC: EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v).
    amount : str
        Input amount in smallest units (e.g. lamports for SOL).

    Taker, receiver, and payer are derived from SOLANA_PRIVATE_KEY in .env (not input params).

    API key is read from JUPITER_API_KEY in .env (https://portal.jup.ag).

    Returns
    -------
    str
        JSON string with mode, inputMint, outputMint, inAmount, outAmount, routePlan,
        transaction (unsigned base64), requestId, feeBps, platformFee, gas fees, etc.
        Parse the JSON and pass the "transaction" and "requestId" fields to execute_trade().

    Raises
    ------
    ValueError
        If input_mint, output_mint, or amount is empty, or SOLANA_PRIVATE_KEY is missing.
    autohedge.errors.ProviderError
        On HTTP/network errors.

    Examples
    --------
    >>> get_order("So11111111111111111111111111111111111111112", "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v", "10000000")
    """
    if not input_mint or not input_mint.strip():
        raise ValueError("input_mint is required")
    if not output_mint or not output_mint.strip():
        raise ValueError("output_mint is required")
    if not amount or not amount.strip():
        raise ValueError("amount is required")

    wallet_pubkey = _get_wallet_pubkey()

    params = {
        "inputMint": input_mint.strip(),
        "outputMint": output_mint.strip(),
        "amount": amount.strip(),
        "taker": wallet_pubkey,
        "receiver": wallet_pubkey,
        "payer": wallet_pubkey,
    }

    settings = load_settings()
    return json.dumps(
        request_json(
            "jupiter",
            "GET",
            str(settings.jupiter_base_url).rstrip("/") + "/ultra/v1/order",
            settings=settings,
            params=params,
            headers=settings.headers("jupiter"),
        )
    )


def execute_trade(unsigned_transaction: str, request_id: str) -> str:
    """
    Sign the transaction with the wallet private key from .env and execute it (Jupiter Ultra).

    The unsigned transaction (base64) from the `/order` response is signed using
    SOLANA_PRIVATE_KEY from .env, then submitted to Jupiter for execution.

    Parameters
    ----------
    unsigned_transaction : str
        The unsigned (or partially signed) transaction from `/order` (base64).
    request_id : str
        Request ID from the response of the `/order` endpoint.

    API key from JUPITER_API_KEY in .env. Signing key from SOLANA_PRIVATE_KEY in .env (base58).

    Returns
    -------
    str
        JSON string with status (Success/Failed), signature, slot, error, code,
        totalInputAmount, totalOutputAmount, inputAmountResult, outputAmountResult,
        swapEvents, etc.

    Raises
    ------
    ValueError
        If unsigned_transaction or request_id is empty, or SOLANA_PRIVATE_KEY is missing/invalid.
    autohedge.errors.ProviderError
        On HTTP/network errors.

    Examples
    --------
    >>> execute_trade("AQAAAAAAAAAAAAAAAA...", "b5e5f3a7-8c4d-4e2f-9a1b-3c6d8e0f2a4b")
    """
    if not unsigned_transaction or not unsigned_transaction.strip():
        raise ValueError("unsigned_transaction is required")
    if not request_id or not request_id.strip():
        raise ValueError("request_id is required")

    keypair = _get_keypair()
    tx_b64 = unsigned_transaction.strip()

    try:
        tx_bytes = base64.b64decode(tx_b64)
    except Exception as e:
        raise ValueError(f"unsigned_transaction is not valid base64: {e}") from e

    try:
        raw_tx = VersionedTransaction.from_bytes(tx_bytes)
    except Exception as e:
        raise ValueError(
            f"unsigned_transaction is not a valid versioned transaction: {e}"
        ) from e

    message_bytes = to_bytes_versioned(raw_tx.message)
    signature = keypair.sign_message(message_bytes)
    signed_tx = VersionedTransaction.populate(raw_tx.message, [signature])
    signed_b64 = base64.b64encode(bytes(signed_tx)).decode("ascii")

    settings = load_settings()
    return json.dumps(
        request_json(
            "jupiter",
            "POST",
            str(settings.jupiter_base_url).rstrip("/") + "/ultra/v1/execute",
            settings=settings,
            headers=settings.headers("jupiter"),
            json={"signedTransaction": signed_b64, "requestId": request_id.strip()},
        )
    )


def get_holdings(address: str) -> str:
    """
    Get token balances and SOL balance for a wallet address (Jupiter Ultra).

    Parameters
    ----------
    address : str
        The wallet address (e.g. Solana public key) to get holdings for.

    API key is read from JUPITER_API_KEY in .env (https://portal.jup.ag).

    Returns
    -------
    str
        JSON string with amount (SOL lamports), uiAmount, uiAmountString, and
        tokens (object keyed by mint address, each value array of token account
        info: account, amount, uiAmount, uiAmountString, isFrozen, decimals, etc.).

    Raises
    ------
    ValueError
        If address is empty.
    autohedge.errors.ProviderError
        On HTTP/network errors.

    Examples
    --------
    >>> get_holdings("BQ72nSv9f3PRyRKCBnHLVrerrv37CYTHm5h3s9VSGQDV")
    """
    if not address or not address.strip():
        raise ValueError("address is required")

    settings = load_settings()
    return json.dumps(
        request_json(
            "jupiter",
            "GET",
            str(settings.jupiter_base_url).rstrip("/")
            + f"/ultra/v1/holdings/{address.strip()}",
            settings=settings,
            headers=settings.headers("jupiter"),
        )
    )
