"""Tests for TokenPak Team features: agent registry, shared vault, templates."""

from __future__ import annotations

import pytest

pytest.importorskip("tokenpak._internal.team.agent_registry", reason="module not available in current build")
import time

import pytest
from tokenpak._internal.team.agent_registry import AgentRecord, AgentRegistry
from tokenpak._internal.team.shared_vault import SharedVault, SharedVaultBlock
from tokenpak._internal.team.templates import ROLE_ADMIN, ROLE_MEMBER, Template, TemplateStore

# ===========================================================================
# Agent Registry Tests (5.3)
# ===========================================================================


class TestAgentRecord:
    def test_to_dict_includes_seconds_since_heartbeat(self):
        rec = AgentRecord(name="cali", last_heartbeat=time.time() - 5)
        d = rec.to_dict()
        assert d["seconds_since_heartbeat"] >= 5.0
        assert d["name"] == "cali"

    def test_roundtrip_serialisation(self):
        rec = AgentRecord(name="trix", capabilities=["compression"], status="online")
        d = rec.to_dict()
        restored = AgentRecord.from_dict(d)
        assert restored.name == rec.name
        assert restored.capabilities == rec.capabilities


class TestAgentRegistry:
    def _registry(self):
        return AgentRegistry(store_path=":memory:")

    def test_register_creates_agent(self):
        reg = self._registry()
        agent = reg.register("cali", capabilities=["compression", "tools"])
        assert agent.name == "cali"
        assert agent.status == "online"
        assert "compression" in agent.capabilities

    def test_list_agents_returns_all(self):
        reg = self._registry()
        reg.register("cali")
        reg.register("trix")
        reg.register("sue")
        names = {a.name for a in reg.list_agents()}
        assert names == {"cali", "trix", "sue"}

    def test_heartbeat_updates_timestamp(self):
        reg = self._registry()
        reg.register("trix")
        before = reg.get("trix").last_heartbeat
        time.sleep(0.01)
        reg.heartbeat("trix")
        after = reg.get("trix").last_heartbeat
        assert after > before

    def test_heartbeat_unknown_agent_returns_false(self):
        reg = self._registry()
        assert reg.heartbeat("ghost") is False

    def test_mark_stale_transitions_online_to_stale(self):
        reg = AgentRegistry(store_path=":memory:", stale_timeout=0.01)
        reg.register("old-agent")
        # Force heartbeat into the past
        reg._agents["old-agent"].last_heartbeat = time.time() - 10
        stale = reg.mark_stale()
        assert "old-agent" in stale
        assert reg.get("old-agent").status == "stale"

    def test_heartbeat_revives_stale_agent(self):
        reg = AgentRegistry(store_path=":memory:", stale_timeout=0.01)
        reg.register("sleepy")
        reg._agents["sleepy"].last_heartbeat = time.time() - 10
        reg.mark_stale()
        assert reg.get("sleepy").status == "stale"
        reg.heartbeat("sleepy")
        assert reg.get("sleepy").status == "online"

    def test_deregister_removes_agent(self):
        reg = self._registry()
        reg.register("temp")
        assert reg.deregister("temp") is True
        assert reg.get("temp") is None

    def test_stats_counts_by_status(self):
        reg = AgentRegistry(store_path=":memory:", stale_timeout=0.01)
        reg.register("a1")
        reg.register("a2")
        reg._agents["a2"].last_heartbeat = time.time() - 10
        reg.mark_stale()
        stats = reg.stats()
        assert stats["online"] == 1
        assert stats["stale"] == 1
        assert stats["total"] == 2

    def test_list_agents_dict_serialisable(self):
        reg = self._registry()
        reg.register("cali", capabilities=["tools"])
        dicts = reg.list_agents_dict()
        assert isinstance(dicts, list)
        assert dicts[0]["name"] == "cali"
        assert "seconds_since_heartbeat" in dicts[0]

    def test_get_v1_agents_response_format(self):
        """Simulate what the server would return."""
        reg = self._registry()
        reg.register("trix", capabilities=["vault"])
        agents = reg.list_agents_dict()
        stats = reg.stats()
        response = {"agents": agents, "stats": stats}
        assert "agents" in response
        assert response["stats"]["total"] == 1


# ===========================================================================
# Shared Vault Tests (5.5)
# ===========================================================================

def _make_block(block_id: str, contributor: str = "trix", path: str = "foo.py") -> SharedVaultBlock:
    return SharedVaultBlock(
        block_id=block_id,
        contributor=contributor,
        path=path,
        content_hash="abc123",
        file_type="code",
        raw_tokens=100,
        compressed_tokens=50,
        compressed_content="# compressed",
    )


class TestSharedVault:
    def _vault(self):
        return SharedVault(store_path=":memory:")

    def test_push_and_pull_blocks(self):
        vault = self._vault()
        block = _make_block("trix:foo.py#abc123")
        vault.push_block(block)
        blocks = vault.pull_blocks()
        assert len(blocks) == 1
        assert blocks[0].block_id == "trix:foo.py#abc123"

    def test_pull_by_contributor(self):
        vault = self._vault()
        vault.push_block(_make_block("trix:a.py#1", contributor="trix"))
        vault.push_block(_make_block("cali:b.py#2", contributor="cali"))
        trix_blocks = vault.pull_blocks(contributor="trix")
        assert len(trix_blocks) == 1
        assert trix_blocks[0].contributor == "trix"

    def test_merge_local_takes_priority(self):
        vault = self._vault()
        # Team has block at path foo.py
        vault.push_block(_make_block("trix:foo.py#team", path="foo.py"))

        # Local also has foo.py — local should win
        class FakeLocalBlock:
            path = "foo.py"
            label = "local"

        local = [FakeLocalBlock()]
        merged = vault.merge_with_local(local)
        # Only 1 foo.py (local), team block excluded
        paths = [b.path for b in merged]
        assert paths.count("foo.py") == 1
        assert merged[0].label == "local"

    def test_merge_team_fills_missing_paths(self):
        vault = self._vault()
        vault.push_block(_make_block("trix:bar.py#team", path="bar.py"))

        class FakeLocalBlock:
            path = "foo.py"

        merged = vault.merge_with_local([FakeLocalBlock()])
        paths = {b.path for b in merged}
        assert "foo.py" in paths
        assert "bar.py" in paths

    def test_delete_block(self):
        vault = self._vault()
        block = _make_block("trix:x.py#abc")
        vault.push_block(block)
        assert vault.delete_block("trix:x.py#abc") is True
        assert vault.get_block("trix:x.py#abc") is None

    def test_stats(self):
        vault = self._vault()
        vault.push_block(_make_block("trix:a.py#1", contributor="trix"))
        vault.push_block(_make_block("cali:b.py#2", contributor="cali"))
        stats = vault.stats()
        assert stats["total_blocks"] == 2
        assert set(stats["contributors"]) == {"trix", "cali"}
        assert stats["tokens_saved"] == 100  # 2 blocks × 50 tokens each

    def test_search_finds_content(self):
        vault = self._vault()
        block = SharedVaultBlock(
            block_id="trix:readme#1",
            contributor="trix",
            path="README.md",
            content_hash="abc",
            file_type="text",
            raw_tokens=200,
            compressed_tokens=80,
            compressed_content="This is about compression pipeline optimisation",
        )
        vault.push_block(block)
        results = vault.search("compression")
        assert len(results) == 1

    def test_roundtrip_serialisation(self):
        block = _make_block("trix:foo.py#abc123")
        d = block.to_dict()
        restored = SharedVaultBlock.from_dict(d)
        assert restored.block_id == block.block_id
        assert restored.contributor == block.contributor


# ===========================================================================
# Team Templates Tests (5.10)
# ===========================================================================

class TestTemplate:
    def test_render_substitutes_variables(self):
        t = Template(name="greet", content="Hello, {{name}}!", created_by="admin")
        rendered = t.render({"name": "Kevin"})
        assert rendered == "Hello, Kevin!"

    def test_render_no_variables(self):
        t = Template(name="fixed", content="Static content", created_by="admin")
        assert t.render() == "Static content"

    def test_roundtrip_serialisation(self):
        t = Template(name="test", content="{{x}}", created_by="sue", tags=["qa"])
        d = t.to_dict()
        restored = Template.from_dict(d)
        assert restored.name == t.name
        assert restored.tags == ["qa"]


class TestTemplateStore:
    def _store(self):
        return TemplateStore(store_path=":memory:")

    def test_create_and_get(self):
        store = self._store()
        store.create("summarise", "Summarise: {{content}}", created_by="sue", actor_role=ROLE_ADMIN)
        t = store.get("summarise")
        assert t is not None
        assert t.name == "summarise"

    def test_create_non_admin_raises(self):
        store = self._store()
        with pytest.raises(PermissionError):
            store.create("bad", "content", created_by="hacker", actor_role=ROLE_MEMBER)

    def test_create_duplicate_raises(self):
        store = self._store()
        store.create("dup", "content", created_by="sue", actor_role=ROLE_ADMIN)
        with pytest.raises(ValueError):
            store.create("dup", "content2", created_by="sue", actor_role=ROLE_ADMIN)

    def test_list_templates_visible_to_member(self):
        store = self._store()
        store.create("public", "content", created_by="sue", actor_role=ROLE_ADMIN, role_required=ROLE_MEMBER)
        store.create("secret", "secret", created_by="sue", actor_role=ROLE_ADMIN, role_required=ROLE_ADMIN)
        visible = store.list_templates(actor_role=ROLE_MEMBER)
        names = {t.name for t in visible}
        assert "public" in names
        assert "secret" not in names

    def test_list_templates_admin_sees_all(self):
        store = self._store()
        store.create("public", "content", created_by="sue", actor_role=ROLE_ADMIN)
        store.create("secret", "secret", created_by="sue", actor_role=ROLE_ADMIN, role_required=ROLE_ADMIN)
        visible = store.list_templates(actor_role=ROLE_ADMIN)
        assert len(visible) == 2

    def test_use_renders_template(self):
        store = self._store()
        store.create("greet", "Hi {{name}}", created_by="sue", actor_role=ROLE_ADMIN)
        result = store.use("greet", variables={"name": "Kevin"})
        assert result == "Hi Kevin"

    def test_use_admin_only_template_as_member_raises(self):
        store = self._store()
        store.create("priv", "secret", created_by="sue", actor_role=ROLE_ADMIN, role_required=ROLE_ADMIN)
        with pytest.raises(PermissionError):
            store.use("priv", actor_role=ROLE_MEMBER)

    def test_delete_template(self):
        store = self._store()
        store.create("todelet", "bye", created_by="sue", actor_role=ROLE_ADMIN)
        assert store.delete("todelet", actor_role=ROLE_ADMIN) is True
        assert store.get("todelet") is None

    def test_delete_non_admin_raises(self):
        store = self._store()
        store.create("keep", "content", created_by="sue", actor_role=ROLE_ADMIN)
        with pytest.raises(PermissionError):
            store.delete("keep", actor_role=ROLE_MEMBER)

    def test_stats(self):
        store = self._store()
        store.create("p1", "content", created_by="sue", actor_role=ROLE_ADMIN)
        store.create("p2", "content", created_by="sue", actor_role=ROLE_ADMIN, role_required=ROLE_ADMIN)
        stats = store.stats()
        assert stats["total"] == 2
        assert stats["admin_only"] == 1
        assert stats["all_members"] == 1

    def test_list_templates_filter_by_tag(self):
        store = self._store()
        store.create("t1", "content", created_by="sue", actor_role=ROLE_ADMIN, tags=["qa", "dev"])
        store.create("t2", "content", created_by="sue", actor_role=ROLE_ADMIN, tags=["prod"])
        qa_templates = store.list_templates(tag="qa")
        assert len(qa_templates) == 1
        assert qa_templates[0].name == "t1"

    def test_update_template(self):
        store = self._store()
        store.create("upd", "old content", created_by="sue", actor_role=ROLE_ADMIN)
        store.update("upd", content="new content", actor_role=ROLE_ADMIN)
        t = store.get("upd")
        assert t.content == "new content"

    def test_update_non_admin_raises(self):
        store = self._store()
        store.create("x", "content", created_by="sue", actor_role=ROLE_ADMIN)
        with pytest.raises(PermissionError):
            store.update("x", content="hacked", actor_role=ROLE_MEMBER)
