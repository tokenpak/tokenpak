
import pytest
pytest.importorskip("tokenpak.registry", reason="module not available in current build")
import hashlib
import unittest
from pathlib import Path

from tokenpak.registry import BlockRegistry, Block
from tokenpak.walker import walk_directory
from tokenpak.processors import get_processor
from tokenpak.tokens import count_tokens
from tokenpak.wire import pack
from tokenpak.budget import BudgetBlock, quadratic_allocate


class TokenPakSmokeTest(unittest.TestCase):
    def test_index_and_search_smoke(self):
        from tempfile import TemporaryDirectory

        with TemporaryDirectory() as td:
            root = Path(td)
            d = root / "docs"
            d.mkdir()
            (d / "a.md").write_text("# TokenPak\n\nThis is a benchmark test for token compression.")
            (d / "b.py").write_text("import os\n\ndef hello(name):\n    \"\"\"Say hi\"\"\"\n    print(name)")
            (d / "c.json").write_text('{"name":"tokenpak","version":1,"items":[1,2,3]}')

            db = root / "registry.db"
            reg = BlockRegistry(str(db))

            files = walk_directory(str(root))
            self.assertGreaterEqual(len(files), 3)

            for path, ftype, _ in files:
                content = Path(path).read_text(encoding="utf-8", errors="ignore")
                p = get_processor(ftype)
                if not p:
                    continue
                compressed = p.process(content, path)
                reg.add_block(Block(
                    path=path,
                    content_hash=hashlib.sha256(content.encode()).hexdigest(),
                    version=1,
                    file_type=ftype,
                    raw_tokens=count_tokens(content),
                    compressed_tokens=count_tokens(compressed),
                    compressed_content=compressed,
                ))

            stats = reg.get_stats()
            self.assertGreaterEqual(stats["total_files"], 3)

            matches = reg.search("token compression", top_k=5)
            self.assertGreaterEqual(len(matches), 1)

            budget_blocks = [BudgetBlock(ref=f"{m.path}#v{m.version}") for m in matches]
            alloc = quadratic_allocate(budget_blocks, 1000)
            self.assertEqual(sum(alloc.values()), 1000)

            wire_blocks = [{
                "ref": f"{m.path}#v{m.version}",
                "type": m.file_type,
                "quality": m.quality_score,
                "tokens": min(200, m.compressed_tokens),
                "content": m.compressed_content,
            } for m in matches]
            out = pack(wire_blocks, 1000, {"query": "token compression"})
            self.assertIn("TOKPAK:1", out)


if __name__ == "__main__":
    unittest.main()
