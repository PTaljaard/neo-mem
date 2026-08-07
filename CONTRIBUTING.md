# Contributing to neo-mem

Thank you for your interest! This project started as part of a **PhD in
Agentic Complex Problem Solving** at the University of South Africa and is
used in production daily by the author.

## Expectations

- **Single maintainer.** This is a research-driven project, not a full-time
  open-source product. Reviews may be slow — please be patient.
- **Stability first.** The plugin runs in production. Breaking changes to the
  config contract (env vars, provider names) will be avoided unless absolutely
  necessary and will come with a clear migration path.
- **Design decisions are documented.** If you disagree with an approach,
  there's likely a tradeoff discussion in the ARCHITECTURE.md or README.
  Happy to discuss via issues.

## PR guidelines

1. **Open an issue first** — discuss the change before coding, especially for
   new features or architecture changes.
2. **Keep it focused** — one change per PR. Small, reviewable diffs.
3. **Update the docs** — if you change env vars, defaults, or behaviour,
   update `README.md`, `.env.example`, and docstrings.
4. **Test with both providers** — if you touch the embedding layer, verify
   with both `ollama` and `openai` providers.
5. **No secrets in code** — all credentials via env vars with `.env.example`
   placeholders. Never commit real keys.

## Code style

- Follow the existing style (PEP 8, type hints, Google-style docstrings).
- The module docstring is the source of truth for env vars — keep it in sync.
- Log messages start with `logger.info/warning/error` — no `print()`.

## Local dev setup

```bash
git clone https://github.com/your-org/neo-mem
cd neo-mem
cp .env.example .env   # edit credentials
docker compose up -d   # start Neo4j
pip install -e plugin/ # install plugin in editable mode
```

## License

By contributing, you agree that your contributions will be licensed under
the Apache 2.0 license.