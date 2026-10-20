# Fork development guide

This fork is **research-only by default**. The main `AutoHedge.run()` path returns
AI-generated analysis, not executed brokerage/chain transactions. Although
Solana signing helpers exist in `autohedge/tools/ultra_tools.py`, they are NOT
attached to the main agents. Do not put real wallet credentials into the repo.

## Initial setup

Use Python 3.10+ and a virtual environment. Install project dependencies with
`pip install -e .`. Copy `.env.example` to `.env` and provide
`OPENAI_API_KEY` for model access and `EXA_API_KEY` for Exa web search.
`JUPITER_API_KEY` is needed only when using standalone Jupiter helpers.
For research, leave `SOLANA_PRIVATE_KEY` empty.

## Test the agent lifecycle without external services

```bash
python -m unittest discover -s tests -v
```

The lifecycle regression tests stub the Swarms agent, need no API credentials
and never sign or send transactions. They verify the per-cycle agent factory
and current timestamp. They do not prove market performance or live trading.

## Development safety requirements

- Paper-trade before integrating any live execution.
- Introduce a separate deterministic risk gate before any wallet API call.
- Do not expose wallet signing tools to language-model agents.
- Keep a mandatory explicit live-trading enable switch disabled by default.
- Evaluate historical fill realism, transaction costs and slippage.
- Never commit `.env`, private keys or wallet secrets.
