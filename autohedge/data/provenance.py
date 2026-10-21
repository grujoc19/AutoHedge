"""Stable content identities; hashes detect changes, not source authenticity."""

import hashlib
import json

from pydantic import BaseModel


def canonical_json(value: BaseModel | dict) -> bytes:
    if isinstance(value, BaseModel):
        value = value.model_dump(mode="json")
    return json.dumps(value, sort_keys=True, separators=(",", ":")).encode("utf-8")


def content_id(value: BaseModel | dict) -> str:
    return hashlib.sha256(canonical_json(value)).hexdigest()
