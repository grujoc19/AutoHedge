# Provider fixture provenance

These are synthetic, schema-shaped fixtures, not recordings of live API calls.
Values and news content are arbitrary and carry no performance evidence. Tests
must remain offline and require no real keys. Schema sources inspected on
2026-10-21:

- [Jupiter Price V3 OpenAPI at 0066fde](https://github.com/jup-ag/docs/blob/0066fdeab9aebddae99d68f58ed121d58153e88a/openapi-spec/price/v3/price.yaml)
- [Jupiter Tokens V2 OpenAPI at 0066fde](https://github.com/jup-ag/docs/blob/0066fdeab9aebddae99d68f58ed121d58153e88a/openapi-spec/tokens/v2/tokens.yaml)
- [Official Exa Python SDK at e6aae25](https://github.com/exa-labs/exa-py/blob/e6aae2571b7bc1bb0cc269558b769e896a1ebbc0/exa_py/api.py):
  search option camel-casing, contents.text.maxCharacters, result fields,
  published_date described as an optional estimate, and context deprecation.

The Jupiter fixture includes ignored vendor fields to exercise additive schema
compatibility. Its long Decimal price exercises preservation of numeric JSON
precision. The token-search updatedAt is deliberately recent while price
createdAt is old; neither may be promoted to a price event timestamp. Exa's
publication date is fixed for an injected UTC clock. Malformed payload variants
are constructed in tests from these fixtures.
