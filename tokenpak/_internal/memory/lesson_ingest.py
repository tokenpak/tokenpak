"""
Lesson Ingestion Module

Extracts lessons from vault daily logs (markdown) and populates the DecisionMemoryDB.
Parses sections like "## Lessons Learned", "## Notes", decision patterns, and stores them.

Usage:
    from lesson_ingest import extract_lessons, ingest_from_vault
    from decision_memory import DecisionMemoryDB
    
    db = DecisionMemoryDB()
    lessons = extract_lessons("~/.tokenpak/memory/2026-03-27.md")
    count = ingest_from_vault("~/.tokenpak", db)
"""

import os
import re
from pathlib import Path
from typing import List, Dict, Optional, Any
from datetime import datetime
from tokenpak._internal.memory.decision_memory import DecisionMemoryDB


def extract_lessons(filepath: str) -> List[Dict[str, Any]]:
    """
    Parse a vault daily log markdown file and extract lessons.
    
    Looks for:
    - "## Lessons Learned" sections
    - "## Notes" sections with decision/insight content
    - "## Status" or "## Result" patterns that contain insights
    - Task completion summaries with outcomes
    
    Args:
        filepath: path to markdown daily log file
    
    Returns:
        List of lesson dictionaries with keys:
        - lesson: the lesson/insight text
        - section: which section it came from (e.g., "Lessons Learned", "Notes", "Task Summary")
        - task_id: extracted task ID if found
        - timestamp: extracted from filename or file stat
        - confidence: initial confidence (0.7 for standard lessons, 0.9 for explicit "Lesson")
    """
    lessons = []
    
    filepath = os.path.expanduser(filepath)
    if not os.path.exists(filepath):
        return lessons
    
    # Extract date from filename (YYYY-MM-DD.md)
    filename = os.path.basename(filepath)
    date_match = re.search(r'(\d{4})-(\d{2})-(\d{2})', filename)
    file_date = f"{date_match.group(1)}-{date_match.group(2)}-{date_match.group(3)}" if date_match else None
    
    with open(filepath, 'r', encoding='utf-8') as f:
        content = f.read()
    
    # Split into sections
    sections = re.split(r'^## ', content, flags=re.MULTILINE)
    
    for section in sections[1:]:  # skip first empty split
        lines = section.split('\n')
        section_name = lines[0].strip()
        section_content = '\n'.join(lines[1:]).strip()
        
        # Extract lessons from explicit "Lessons Learned" section
        if 'lesson' in section_name.lower():
            # Each bullet/line in this section is a lesson
            for line in section_content.split('\n'):
                line = line.strip()
                if line and line.startswith('-'):
                    lesson_text = line.lstrip('-').strip()
                    if lesson_text:
                        lessons.append({
                            'lesson': lesson_text,
                            'section': section_name,
                            'task_id': None,
                            'timestamp': file_date,
                            'confidence': 0.9
                        })
        
        # Extract insights from "Notes" section
        elif 'note' in section_name.lower():
            for line in section_content.split('\n'):
                line = line.strip()
                if line and not line.startswith('#'):
                    lessons.append({
                        'lesson': line,
                        'section': section_name,
                        'task_id': None,
                        'timestamp': file_date,
                        'confidence': 0.7
                    })
        
        # Extract task completion patterns from task summary sections
        elif any(x in section_name.lower() for x in ['task', 'work', 'status', 'result']):
            # Look for patterns like "Task X: description → outcome"
            task_match = re.search(r'([A-Z]+-[A-Z0-9-]+)[:|\s]+(.+?)(?:→|—|-{2,}|✓|✅)(.*?)(?:\n\n|\n\*\*|$)', section_content, re.DOTALL)
            if task_match:
                task_id = task_match.group(1)
                task_desc = task_match.group(2).strip()
                task_outcome = task_match.group(3).strip() if task_match.group(3) else ''
                
                # Extract decision/lesson from outcome
                if task_outcome:
                    lessons.append({
                        'lesson': f"Task {task_id}: {task_outcome}",
                        'section': section_name,
                        'task_id': task_id,
                        'timestamp': file_date,
                        'confidence': 0.8
                    })
            
            # Also extract any bold patterns as insights (e.g., **Action**: ...)
            bold_patterns = re.findall(r'\*\*([^*]+)\*\*:\s*([^\n]+)', section_content)
            for key, value in bold_patterns:
                if key.lower() in ['lesson', 'insight', 'finding', 'recommendation', 'action', 'result']:
                    lessons.append({
                        'lesson': f"{key}: {value}",
                        'section': section_name,
                        'task_id': None,
                        'timestamp': file_date,
                        'confidence': 0.8
                    })
    
    return lessons


def ingest_from_vault(vault_dir: str, db: DecisionMemoryDB) -> int:
    """
    Walk vault daily logs and ingest all lessons into the DecisionMemoryDB.
    
    Scans all files matching <vault_dir>/agents/*/memory/YYYY-MM-DD.md
    and extracts lessons, populating the database.
    
    Args:
        vault_dir: path to vault root directory
        db: DecisionMemoryDB instance to write to
    
    Returns:
        Total count of lessons ingested
    """
    vault_dir = os.path.expanduser(vault_dir)
    memory_dir = os.path.join(vault_dir, '03_AGENT_PACKS')
    
    total_ingested = 0
    
    if not os.path.isdir(memory_dir):
        return 0
    
    # Walk all agent memory directories
    for agent_dir in os.listdir(memory_dir):
        agent_memory_path = os.path.join(memory_dir, agent_dir, 'memory')
        
        if not os.path.isdir(agent_memory_path):
            continue
        
        # Process all YYYY-MM-DD.md files
        for filename in sorted(os.listdir(agent_memory_path)):
            if re.match(r'\d{4}-\d{2}-\d{2}\.md$', filename):
                filepath = os.path.join(agent_memory_path, filename)
                lessons = extract_lessons(filepath)
                
                for lesson in lessons:
                    # Record in DecisionMemoryDB
                    query = f"lesson_{lesson['timestamp']}_{lesson.get('task_id', 'general')}"
                    
                    record_id = db.record(
                        query=query,
                        decision=lesson['lesson'],
                        confidence=lesson['confidence'],
                        notes=f"source: {agent_dir}/memory/{filename}, section: {lesson['section']}"
                    )
                    
                    total_ingested += 1
    
    return total_ingested


if __name__ == '__main__':
    # Simple test
    import sys
    
    if len(sys.argv) > 1:
        filepath = sys.argv[1]
        lessons = extract_lessons(filepath)
        print(f"Extracted {len(lessons)} lessons from {filepath}:")
        for lesson in lessons:
            print(f"  - [{lesson['confidence']}] {lesson['lesson']}")
    else:
        print("Usage: python3 lesson_ingest.py <path-to-daily-log.md>")
