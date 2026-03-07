# Adapter Status — 2026-03-07

Installed and verified by Cali — 2026-03-07 14:37 PST

| Package | Installed | Imports | Tests |
|---------|-----------|---------|-------|
| autogen-tokenpak | ✅ | ✅ | N/A (pre-existing) |
| crewai-tokenpak | ✅ | ✅ | ✅ (1 passed) |
| langchain-tokenpak | ✅ | ✅ | ✅ (18 passed) |
| langfuse-tokenpak | ✅ | ✅ | ✅ (30 passed) |
| llamaindex-tokenpak | ✅ | ✅ | ✅ (67 passed, 1 deprecation warning on asyncio.get_event_loop in test_fusion.py:170 — non-blocking) |

## pip3 list output (tokenpak packages)

```
autogen-tokenpak    0.1.0  /home/cali/tokenpak/packages/autogen-tokenpak
crewai-tokenpak     0.1.0  /home/cali/tokenpak/packages/crewai-tokenpak
langchain-tokenpak  0.1.0  /home/cali/tokenpak/packages/langchain-tokenpak
langfuse-tokenpak   0.1.0  /home/cali/tokenpak/packages/langfuse-tokenpak
llamaindex-tokenpak 0.1.0  /home/cali/tokenpak/packages/llamaindex-tokenpak
tokenpak            0.1.0  /home/cali/tokenpak
```

## Notes

- All packages installed with `pip install --no-deps -e . --break-system-packages`
- All 4 previously missing packages now importable and test-passing
- llamaindex deprecation warning: `asyncio.get_event_loop()` in test_fusion.py — tests still pass, non-critical
- No external service dependencies were hit; all tests run locally with mocks
