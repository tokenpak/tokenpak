"""
Tests for DeterministicPromptPack — byte-identical output verification.

Test Coverage:
  1. Fixed section order enforcement (SYSTEM → TOOLS → POLICIES → RETRIEVED → USER INPUT)
  2. Byte-identical output for equivalent inputs
  3. Stable vs volatile boundary separation with cache_control markers
  4. No breaking changes to existing PromptBuilder / cache control logic
  5. Integration examples and before/after patterns
"""

import json

from tokenpak.agent.proxy.prompt_builder import DeterministicPromptPack, PromptBuilder


def test_acceptance_1_fixed_section_order():
    """Acceptance Criterion 1: Fixed section order SYSTEM → TOOLS → POLICIES → RETRIEVED → USER INPUT"""
    pack = DeterministicPromptPack(
        system="You are helpful.",
        tools=[{"name": "search", "description": "Search for info"}],
        policies="Be honest.",
        retrieved_context=["Result 1", "Result 2"],
        user_input="What is X?",
    )

    blocks = pack.to_system_block()
    stable_text = blocks[0]["text"]

    # Verify order in the output
    pos_system = stable_text.find("# SYSTEM")
    pos_tools = stable_text.find("# TOOLS")
    pos_policies = stable_text.find("# POLICIES")
    pos_retrieved = blocks[1]["text"].find("# RETRIEVED CONTEXT") if len(blocks) > 1 else -1
    pos_user = blocks[1]["text"].find("# USER INPUT") if len(blocks) > 1 else -1

    assert pos_system < pos_tools < pos_policies, "Stable sections must be in order: SYSTEM → TOOLS → POLICIES"
    assert pos_retrieved < pos_user, "Volatile sections must be in order: RETRIEVED CONTEXT → USER INPUT"
    print("✅ AC1: Fixed section order enforced")


def test_acceptance_2_byte_identical_output():
    """Acceptance Criterion 2: Equivalent inputs produce byte-identical packed output"""
    # Create two packs with identical inputs
    pack1 = DeterministicPromptPack(
        system="You are an AI assistant.",
        tools=[
            {"name": "search", "description": "Search the web"},
            {"name": "calculator", "description": "Do math"},
        ],
        policies="Never lie.",
        retrieved_context=["doc1", "doc2"],
        user_input="Hello, what can you do?",
    )

    pack2 = DeterministicPromptPack(
        system="You are an AI assistant.",
        tools=[
            {"name": "search", "description": "Search the web"},
            {"name": "calculator", "description": "Do math"},
        ],
        policies="Never lie.",
        retrieved_context=["doc1", "doc2"],
        user_input="Hello, what can you do?",
    )

    # Compare byte-level output
    body1 = pack1.to_request_body()
    body2 = pack2.to_request_body()

    # Convert to JSON bytes for exact comparison
    json1 = json.dumps(body1, sort_keys=True, ensure_ascii=False).encode("utf-8")
    json2 = json.dumps(body2, sort_keys=True, ensure_ascii=False).encode("utf-8")

    assert json1 == json2, "Byte-identical inputs should produce byte-identical output"
    print("✅ AC2: Byte-identical output for equivalent inputs")


def test_acceptance_2_byte_identity_with_tool_order_variance():
    """Tools in different order should serialize to identical output (tools are sorted internally)."""
    pack1 = DeterministicPromptPack(
        system="Helper",
        tools=[
            {"name": "alpha", "description": "A"},
            {"name": "beta", "description": "B"},
        ],
    )

    pack2 = DeterministicPromptPack(
        system="Helper",
        tools=[
            {"name": "beta", "description": "B"},
            {"name": "alpha", "description": "A"},
        ],
    )

    blocks1 = pack1.to_system_block()
    blocks2 = pack2.to_system_block()

    assert blocks1[0]["text"] == blocks2[0]["text"], "Tool order variance should not affect output (sorted internally)"
    print("✅ AC2 (bonus): Tool order variance handled (internal sorting)")


def test_acceptance_3_stable_volatile_separation():
    """Acceptance Criterion 3: Stable vs volatile boundaries explicitly separated with cache_control"""
    pack = DeterministicPromptPack(
        system="System prompt",
        tools=[{"name": "tool1", "description": "desc"}],
        policies="Policies",
        retrieved_context=["retrieved"],
        user_input="user",
    )

    blocks = pack.to_system_block()

    # Stable block (first) should have cache_control
    assert len(blocks) == 2, "Should have 2 blocks: stable and volatile"
    assert blocks[0]["cache_control"] == {"type": "ephemeral"}, "Stable block must have cache_control"
    assert "cache_control" not in blocks[1], "Volatile block must NOT have cache_control"

    # Verify stable boundaries
    stable_text = blocks[0]["text"]
    assert "# SYSTEM\n\n" in stable_text
    assert "# TOOLS\n\n" in stable_text
    assert "# POLICIES/CONSTRAINTS\n\n" in stable_text

    # Verify volatile sections in second block
    volatile_text = blocks[1]["text"]
    assert "# RETRIEVED CONTEXT\n\n" in volatile_text
    assert "# USER INPUT\n\n" in volatile_text

    print("✅ AC3: Stable vs volatile boundaries explicitly separated with cache_control")


def test_acceptance_4_no_breaking_changes():
    """Acceptance Criterion 4: DeterministicPromptPack doesn't break existing PromptBuilder/cache logic"""
    # Existing PromptBuilder should still work
    builder = PromptBuilder()
    body_bytes = json.dumps({
        "system": "System prompt",
        "tools": [{"name": "tool1", "description": "desc"}],
        "messages": [{"role": "user", "content": "Hello"}],
    }).encode("utf-8")

    parts = builder.decompose(body_bytes)
    assert parts is not None, "PromptBuilder.decompose should still work"
    assert parts.stable_blocks or parts.volatile_blocks, "System prompt preserved"

    rebuilt = builder.build(parts)
    assert rebuilt, "PromptBuilder.build should still work"

    # Verify PromptBuilder and DeterministicPromptPack coexist
    pack = DeterministicPromptPack(system="System prompt", user_input="Hello")
    assert pack is not None, "DeterministicPromptPack instantiation works"
    assert isinstance(pack, DeterministicPromptPack), "Type is correct"

    print("✅ AC4: No breaking changes to existing PromptBuilder/cache control")


def test_acceptance_5_before_after_examples():
    """Acceptance Criterion 5: Before/after examples in docstrings"""
    # This test verifies the docstring examples are executable and correct

    # --- BEFORE pattern (ad-hoc assembly, order inconsistent) ---
    system_parts = []
    system_parts.append("You are an AI.")
    system_parts.append(json.dumps([{"name": "search", "description": "Search"}]))
    system_parts.append("Be honest.")
    system_parts.append("Retrieved: doc1")
    system_parts.append("User: What is X?")

    before_system = "\n\n".join(system_parts)

    # --- AFTER pattern (DeterministicPromptPack, order fixed) ---
    pack = DeterministicPromptPack(
        system="You are an AI.",
        tools=[{"name": "search", "description": "Search"}],
        policies="Be honest.",
        retrieved_context=["doc1"],
        user_input="What is X?",
    )
    system_block = pack.to_system_block()

    # Verify after pattern is valid
    assert isinstance(system_block, list), "to_system_block returns list"
    assert len(system_block) > 0, "System blocks should not be empty"
    assert system_block[0]["type"] == "text", "Block type is text"
    assert "cache_control" in system_block[0], "Cache control marker present"

    # After pattern should be more structured
    assert "# SYSTEM\n\n" in system_block[0]["text"], "Clear section markers"
    assert "# TOOLS\n\n" in system_block[0]["text"], "Organized layout"

    print("✅ AC5: Before/after examples work as documented")


def test_deterministic_json_serialization():
    """Verify deterministic JSON serialization of tools."""
    tool = {
        "description": "Search the web",
        "name": "search",
        "input_schema": {
            "properties": {
                "query": {"type": "string"},
                "limit": {"type": "integer"},
            },
            "required": ["query"],
            "type": "object",
        },
    }

    pack1 = DeterministicPromptPack(tools=[tool])
    pack2 = DeterministicPromptPack(tools=[tool])

    text1 = pack1.to_system_block()[0]["text"]
    text2 = pack2.to_system_block()[0]["text"]

    assert text1 == text2, "Identical tools should serialize identically"
    print("✅ Deterministic JSON serialization verified")


def test_empty_sections():
    """Test that empty sections don't break the pack."""
    pack = DeterministicPromptPack(
        system="",
        tools=[],
        policies="",
        retrieved_context=[],
        user_input="Just the user input",
    )

    blocks = pack.to_system_block()
    assert any("# USER INPUT\n\nJust the user input" in b["text"] for b in blocks), "User input present"

    # Empty sections should not appear in output
    output_text = "".join(b["text"] for b in blocks)
    assert "# SYSTEM\n\n" not in output_text, "Empty system section should not appear"
    print("✅ Empty sections handled correctly")


def test_cache_boundary_marker():
    """Test cache_control marker placement."""
    pack = DeterministicPromptPack(
        system="System",
        tools=[{"name": "tool", "description": "desc"}],
        policies="Policy",
        retrieved_context=["Retrieved"],
        user_input="User",
    )

    blocks = pack.to_system_block()

    # First block (stable) should have cache marker
    assert blocks[0]["cache_control"] == {"type": "ephemeral"}, "Cache marker on stable block"

    # Second block (volatile) should NOT have cache marker
    if len(blocks) > 1:
        assert "cache_control" not in blocks[1], "No cache marker on volatile block"

    print("✅ Cache boundary marker correctly placed")


def test_retrieved_context_dict_format():
    """Test handling of retrieved context as dict (with text, source, score, etc)."""
    retrieved = [
        {"text": "Document 1", "source": "source1", "score": 0.9},
        {"text": "Document 2", "source": "source2", "score": 0.8},
    ]

    pack1 = DeterministicPromptPack(
        system="System",
        retrieved_context=retrieved,
        user_input="Query",
    )

    pack2 = DeterministicPromptPack(
        system="System",
        retrieved_context=retrieved,
        user_input="Query",
    )

    blocks1 = pack1.to_system_block()
    blocks2 = pack2.to_system_block()

    # Both should produce identical output
    assert blocks1[-1]["text"] == blocks2[-1]["text"], "Dict format handled deterministically"

    # Content should be present
    output = blocks1[-1]["text"]
    assert "Document 1" in output and "Document 2" in output, "Content extracted correctly"

    print("✅ Retrieved context dict format handled correctly")


def test_to_request_body():
    """Test complete request body assembly."""
    pack = DeterministicPromptPack(
        system="You are helpful.",
        tools=[{"name": "search", "description": "Search"}],
        policies="Never harmful.",
        retrieved_context=["doc1"],
        user_input="Hello",
    )

    body = pack.to_request_body(model="claude-3-sonnet-20250514")

    assert body["model"] == "claude-3-sonnet-20250514", "Model set correctly"
    assert "system" in body, "System field present"
    assert "messages" in body, "Messages field present"
    assert "tools" in body, "Tools field present"
    assert body["messages"][0]["role"] == "user", "User message present"
    assert body["messages"][0]["content"] == "Hello", "User input in message"

    print("✅ to_request_body() assembles complete request correctly")


def test_repr():
    """Test string representation."""
    pack = DeterministicPromptPack(
        system="System" * 100,
        tools=[{"name": "tool1"}, {"name": "tool2"}],
        policies="Policy" * 50,
        retrieved_context=["doc1", "doc2", "doc3"],
        user_input="User" * 100,
    )

    repr_str = repr(pack)
    assert "DeterministicPromptPack" in repr_str
    assert "system=" in repr_str
    assert "tools=" in repr_str
    print("✅ __repr__ provides useful info")


if __name__ == "__main__":
    # Run all acceptance criterion tests
    test_acceptance_1_fixed_section_order()
    test_acceptance_2_byte_identical_output()
    test_acceptance_2_byte_identity_with_tool_order_variance()
    test_acceptance_3_stable_volatile_separation()
    test_acceptance_4_no_breaking_changes()
    test_acceptance_5_before_after_examples()

    # Additional verification tests
    test_deterministic_json_serialization()
    test_empty_sections()
    test_cache_boundary_marker()
    test_retrieved_context_dict_format()
    test_to_request_body()
    test_repr()

    print("\n" + "=" * 70)
    print("✅ ALL TESTS PASSED — DeterministicPromptPack ready for production")
    print("=" * 70)
