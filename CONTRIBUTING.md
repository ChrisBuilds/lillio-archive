# Contributing

Contributions are welcome for fixes, portability improvements, documentation,
and behavior that remains within an authorized Lillio account.

## Development

```bash
uv sync --extra test
uv run playwright install chromium
uv run pytest
uv run python -m compileall -q src tests
uv run python scripts/check_public_tree.py
```

Do not commit browser profiles, downloaded media, manifests, logs, reports,
screenshots, HAR files, signed URLs, or real post metadata. Use synthetic
fixtures in tests and documentation.

Keep changes focused and include tests for observable behavior. By submitting a
contribution, you agree that it is licensed under the MIT License.
