"""
Test suite for lesson_ingest module.

Tests extraction and ingestion of lessons from vault daily logs.
"""

import pytest
import tempfile
import os
import sqlite3
from pathlib import Path
from datetime import datetime

# Import the modules under test
from tokenpak._internal.memory.lesson_ingest import extract_lessons, ingest_from_vault
from tokenpak._internal.memory.decision_memory import DecisionMemoryDB


class TestExtractLessons:
    """Test lesson extraction from markdown files."""
    
    def test_extract_lessons_explicit_section(self):
        """Test extraction from explicit 'Lessons Learned' section."""
        content = """# Daily Log 2026-03-27

## Lessons Learned
- Always commit before pushing to origin
- Remember to check SUSPENDED-TASKS.md before claiming
- Test locally before submitting to QA
"""
        with tempfile.NamedTemporaryFile(mode='w', suffix='2026-03-27.md', delete=False) as f:
            f.write(content)
            f.flush()
            
            try:
                lessons = extract_lessons(f.name)
                assert len(lessons) == 3
                assert any('commit' in l['lesson'].lower() for l in lessons)
                assert all(l['confidence'] == 0.9 for l in lessons)  # Explicit lessons = 0.9
            finally:
                os.unlink(f.name)
    
    def test_extract_lessons_notes_section(self):
        """Test extraction from 'Notes' section."""
        content = """# Daily Log 2026-03-27

## Notes
- Rebase conflict during sync; reset to origin/main (safe state)
- All TokenPak sprint tasks either done or in review
- GitHub mirror failures expected (SSH key issue, not agent problem)
"""
        with tempfile.NamedTemporaryFile(mode='w', suffix='2026-03-27.md', delete=False) as f:
            f.write(content)
            f.flush()
            
            try:
                lessons = extract_lessons(f.name)
                assert len(lessons) == 3
                assert all(l['confidence'] == 0.7 for l in lessons)  # Notes = 0.7
            finally:
                os.unlink(f.name)
    
    def test_extract_task_completion_patterns(self):
        """Test extraction from task completion sections."""
        content = """# Daily Log 2026-03-26

## Task Summary
Task TPK-SPEED-QUICKWINS: Three performance fixes
✓ estimate_tokens() — 1700x speedup
✓ SQLite WAL mode — saves 2-5ms/req
✓ CompressionPipeline warmup — eliminates 523ms penalty

Tests: 1067 passed, commit 14b122b60 pushed.
"""
        with tempfile.NamedTemporaryFile(mode='w', suffix='2026-03-26.md', delete=False) as f:
            f.write(content)
            f.flush()
            
            try:
                lessons = extract_lessons(f.name)
                assert len(lessons) > 0
                assert any('speedup' in l['lesson'].lower() for l in lessons)
            finally:
                os.unlink(f.name)
    
    def test_extract_bold_patterns(self):
        """Test extraction of **Key**: value patterns."""
        content = """# Daily Log 2026-03-26

## Work Done
**Finding**: Proxy stress test was legitimate work; just needed commit hash
**Recommendation**: Add checklist to AGENTS.md for submission verification
**Action**: Update submission protocol in communication-protocol.md
"""
        with tempfile.NamedTemporaryFile(mode='w', suffix='2026-03-26.md', delete=False) as f:
            f.write(content)
            f.flush()
            
            try:
                lessons = extract_lessons(f.name)
                # Should extract all **Key**: value patterns
                assert len(lessons) > 0
                assert any('finding' in l['lesson'].lower() for l in lessons)
            finally:
                os.unlink(f.name)
    
    def test_extract_empty_file(self):
        """Test handling of empty files."""
        with tempfile.NamedTemporaryFile(mode='w', suffix='2026-03-27.md', delete=False) as f:
            f.write("")
            f.flush()
            
            try:
                lessons = extract_lessons(f.name)
                assert len(lessons) == 0
            finally:
                os.unlink(f.name)
    
    def test_extract_nonexistent_file(self):
        """Test handling of nonexistent files."""
        lessons = extract_lessons('/nonexistent/path/to/file.md')
        assert len(lessons) == 0
    
    def test_extract_date_from_filename(self):
        """Test that date is extracted from filename."""
        content = """# Daily Log

## Lessons Learned
- Test lesson
"""
        with tempfile.NamedTemporaryFile(mode='w', suffix='2026-03-15.md', delete=False) as f:
            f.write(content)
            f.flush()
            
            try:
                lessons = extract_lessons(f.name)
                assert len(lessons) == 1
                assert lessons[0]['timestamp'] == '2026-03-15'
            finally:
                os.unlink(f.name)


class TestIngestFromVault:
    """Test ingestion of lessons from vault structure."""
    
    def test_ingest_single_agent_log(self):
        """Test ingesting from a single agent's memory logs."""
        with tempfile.TemporaryDirectory() as tmpdir:
            # Create mock vault structure
            agent_memory = Path(tmpdir) / '03_AGENT_PACKS' / 'TestAgent' / 'memory'
            agent_memory.mkdir(parents=True)
            
            # Write a test daily log
            log_file = agent_memory / '2026-03-27.md'
            log_file.write_text("""# Daily Log

## Lessons Learned
- Lesson 1
- Lesson 2
""")
            
            # Ingest using temp DB
            with tempfile.NamedTemporaryFile(suffix='.db', delete=False) as db_f:
                try:
                    db = DecisionMemoryDB(db_f.name)
                    count = ingest_from_vault(tmpdir, db)
                    assert count == 2
                    assert db.count() == 2
                finally:
                    os.unlink(db_f.name)
    
    def test_ingest_multiple_dates(self):
        """Test ingesting from multiple date files."""
        with tempfile.TemporaryDirectory() as tmpdir:
            agent_memory = Path(tmpdir) / '03_AGENT_PACKS' / 'TestAgent' / 'memory'
            agent_memory.mkdir(parents=True)
            
            # Write multiple logs
            for date in ['2026-03-25', '2026-03-26', '2026-03-27']:
                log_file = agent_memory / f'{date}.md'
                log_file.write_text(f"""# Daily Log {date}

## Lessons Learned
- Lesson from {date}
""")
            
            with tempfile.NamedTemporaryFile(suffix='.db', delete=False) as db_f:
                try:
                    db = DecisionMemoryDB(db_f.name)
                    count = ingest_from_vault(tmpdir, db)
                    assert count == 3
                finally:
                    os.unlink(db_f.name)
    
    def test_ingest_multiple_agents(self):
        """Test ingesting from multiple agents."""
        with tempfile.TemporaryDirectory() as tmpdir:
            # Create logs for multiple agents
            for agent in ['Sue', 'Trix', 'Cali']:
                agent_memory = Path(tmpdir) / '03_AGENT_PACKS' / agent / 'memory'
                agent_memory.mkdir(parents=True)
                
                log_file = agent_memory / '2026-03-27.md'
                log_file.write_text(f"""# Daily Log {agent}

## Lessons Learned
- {agent} lesson 1
- {agent} lesson 2
""")
            
            with tempfile.NamedTemporaryFile(suffix='.db', delete=False) as db_f:
                try:
                    db = DecisionMemoryDB(db_f.name)
                    count = ingest_from_vault(tmpdir, db)
                    assert count == 6  # 3 agents × 2 lessons each
                finally:
                    os.unlink(db_f.name)
    
    def test_ingest_skips_non_date_files(self):
        """Test that non-YYYY-MM-DD.md files are skipped."""
        with tempfile.TemporaryDirectory() as tmpdir:
            agent_memory = Path(tmpdir) / '03_AGENT_PACKS' / 'TestAgent' / 'memory'
            agent_memory.mkdir(parents=True)
            
            # Write various files
            log_file = agent_memory / '2026-03-27.md'
            log_file.write_text("""# Daily Log
## Lessons Learned
- Valid lesson
""")
            
            # These should be skipped
            (agent_memory / 'README.md').write_text("# Readme")
            (agent_memory / 'archive.md').write_text("# Archive")
            (agent_memory / 'legacy-03-27.md').write_text("# Legacy")
            
            with tempfile.NamedTemporaryFile(suffix='.db', delete=False) as db_f:
                try:
                    db = DecisionMemoryDB(db_f.name)
                    count = ingest_from_vault(tmpdir, db)
                    assert count == 1  # Only the 2026-03-27.md file
                finally:
                    os.unlink(db_f.name)
    
    def test_ingest_missing_vault_structure(self):
        """Test handling of missing vault structure."""
        with tempfile.TemporaryDirectory() as tmpdir:
            with tempfile.NamedTemporaryFile(suffix='.db', delete=False) as db_f:
                try:
                    db = DecisionMemoryDB(db_f.name)
                    count = ingest_from_vault(tmpdir, db)
                    assert count == 0
                finally:
                    os.unlink(db_f.name)
    
    def test_ingest_stores_in_db(self):
        """Test that ingested lessons are actually stored in DB."""
        with tempfile.TemporaryDirectory() as tmpdir:
            agent_memory = Path(tmpdir) / '03_AGENT_PACKS' / 'TestAgent' / 'memory'
            agent_memory.mkdir(parents=True)
            
            log_file = agent_memory / '2026-03-27.md'
            log_file.write_text("""# Daily Log

## Lessons Learned
- Commit before pushing
- Check suspended tasks
""")
            
            with tempfile.NamedTemporaryFile(suffix='.db', delete=False) as db_f:
                try:
                    db = DecisionMemoryDB(db_f.name)
                    count = ingest_from_vault(tmpdir, db)
                    
                    # Verify records are in DB
                    assert count == 2
                    all_records = db.all()
                    assert len(all_records) == 2
                    
                    # Verify record structure
                    for record in all_records:
                        assert record.decision in ['Commit before pushing', 'Check suspended tasks']
                        assert record.confidence == 0.9  # Explicit lessons
                        assert 'TestAgent' in record.notes
                        assert 'Lessons Learned' in record.notes
                finally:
                    os.unlink(db_f.name)


class TestIntegration:
    """Integration tests with real vault structure patterns."""
    
    def test_real_vault_log_pattern(self):
        """Test with a realistic vault daily log."""
        content = """# Cali Daily Log — 2026-03-27

## Status Summary
- **Vault sync:** ✓ Complete (rebase-merge cleanup handled)
- **Suspended tasks check:** ✓ All non-TokenPak work suspended
- **Queue scan:** ✓ 8 open tasks available

## Lessons Learned
- Always pull vault-sync before claiming tasks
- Rebase conflicts are safe if we reset to origin/main
- Check SUSPENDED-TASKS.md before every task claim

## Notes
- GitHub mirror has SSH key issues (expected)
- TokenPak sprint is the priority this week
- Telemetry collection is running normally

## Task Execution
**Task:** p2-tokenpak-memory-lesson-ingestion-2026-03-27.md
**Result:** Implemented core logic, tests passing, DB schema validated
**Action:** Submitted for Sue QA review

## Next Cycle
Ready to pick up next available p3 task in queue.
"""
        with tempfile.NamedTemporaryFile(mode='w', suffix='2026-03-27.md', delete=False) as f:
            f.write(content)
            f.flush()
            
            try:
                lessons = extract_lessons(f.name)
                
                # Should extract multiple lessons from various sections
                assert len(lessons) > 0
                
                # Verify explicit lessons are higher confidence
                explicit_lessons = [l for l in lessons if 'pull vault-sync' in l['lesson'].lower()]
                assert len(explicit_lessons) > 0
                assert explicit_lessons[0]['confidence'] == 0.9
                
                # Verify structured extraction
                assert all(l['timestamp'] == '2026-03-27' for l in lessons)
            finally:
                os.unlink(f.name)
    
    def test_complex_task_pattern_extraction(self):
        """Test extraction from complex task completion patterns."""
        content = """# Daily Log 2026-03-26

## Task 1: Speed Optimization
Task TPK-SPEED-QUICKWINS: Three independent performance fixes
→ estimate_tokens() — 1700x speedup (0.96μs vs ~1700μs)
→ SQLite WAL mode — saves 2-5ms/req per request
→ CompressionPipeline warmup — eliminates 523ms first-request penalty

**Result:** All 1067 tests pass, commit 14b122b60 pushed
**Recommendation:** Consider WAL mode as production default

## Task 2: HTTP Correctness
Task TPK-UX-HTTP: HTTP correctness fixes
→ Added do_HEAD() for health checks
→ Added do_OPTIONS() for CORS preflight
→ Added 405 handler for invalid methods
→ Added root / welcome JSON handler

**Result:** 1067 tests passed, 2 skipped
**Finding:** HEAD and OPTIONS support critical for reverse proxy compatibility
"""
        with tempfile.NamedTemporaryFile(mode='w', suffix='2026-03-26.md', delete=False) as f:
            f.write(content)
            f.flush()
            
            try:
                lessons = extract_lessons(f.name)
                assert len(lessons) > 0
                
                # Verify we extract key task IDs
                task_ids = [l['task_id'] for l in lessons if l['task_id']]
                assert 'TPK-SPEED-QUICKWINS' in task_ids or 'TPK-UX-HTTP' in task_ids
            finally:
                os.unlink(f.name)


if __name__ == '__main__':
    pytest.main([__file__, '-v', '--tb=short'])
