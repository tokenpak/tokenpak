from langchain_tokenpak.context import TokenPakContextManager


def test_context_allocate():
    cm = TokenPakContextManager(total_budget=8000)
    allocated = cm.allocate("retrieval", 4000)
    assert allocated == 4000
    assert cm.remaining == 4000


def test_context_over_allocate():
    cm = TokenPakContextManager(total_budget=1000)
    cm.allocate("retrieval", 800)
    actual = cm.allocate("memory", 400)
    assert actual == 200


def test_context_trim():
    cm = TokenPakContextManager(total_budget=8000, avg_tokens_per_char=1.0)
    cm.allocate("retrieval", 100)
    text = "A" * 200
    trimmed = cm.trim_to_budget("retrieval", text)
    assert len(trimmed) <= 100


def test_context_fits():
    cm = TokenPakContextManager(total_budget=8000, avg_tokens_per_char=0.25)
    cm.allocate("retrieval", 100)
    assert cm.fits("retrieval", "hello")
    assert not cm.fits("retrieval", "x" * 10000)


def test_context_status():
    cm = TokenPakContextManager(total_budget=8000)
    cm.allocate("retrieval", 4000)
    status = cm.status()
    assert status["total"] == 8000
    assert "retrieval" in status["allocations"]
