"""
TokenPak Core - Universal Context Compiler

This is the working implementation for testing.
"""

import hashlib
import json
import os
import re
from pathlib import Path
from datetime import datetime
from typing import Dict, List, Optional, Any
from dataclasses import dataclass, field, asdict

# Try to import tiktoken for accurate token counting
try:
    import tiktoken
    _ENC = tiktoken.get_encoding("cl100k_base")
    def count_tokens(text: str) -> int:
        return len(_ENC.encode(text))
except ImportError:
    def count_tokens(text: str) -> int:
        # Rough estimate: ~4 chars per token
        return len(text) // 4


@dataclass
class Block:
    """A compiled content block."""
    block_id: str
    source_path: str
    content_type: str
    version: int
    content_hash: str
    raw_tokens: int
    compressed_tokens: int
    compressed_content: str
    metadata: Dict[str, Any] = field(default_factory=dict)
    created_at: str = field(default_factory=lambda: datetime.now().isoformat())
    
    @property
    def compression_ratio(self) -> float:
        if self.compressed_tokens == 0:
            return 0
        return round(self.raw_tokens / self.compressed_tokens, 2)


@dataclass 
class PackIndex:
    """The compiled knowledge base."""
    version: str = "1.0"
    created_at: str = field(default_factory=lambda: datetime.now().isoformat())
    source_dir: str = ""
    blocks: Dict[str, Block] = field(default_factory=dict)
    stats: Dict[str, Any] = field(default_factory=dict)
    
    def to_dict(self) -> dict:
        return {
            "version": self.version,
            "created_at": self.created_at,
            "source_dir": self.source_dir,
            "stats": self.stats,
            "blocks": {k: asdict(v) for k, v in self.blocks.items()}
        }


class TextCompressor:
    """Compresses text/markdown content."""
    
    def compress(self, content: str, file_type: str = "text") -> str:
        """Compress text content while preserving meaning."""
        lines = content.strip().split('\n')
        compressed = []
        
        for line in lines:
            line = line.rstrip()
            
            # Skip empty lines (but keep one between sections)
            if not line:
                if compressed and compressed[-1] != "":
                    compressed.append("")
                continue
            
            # Preserve headers
            if line.startswith('#'):
                compressed.append(line)
                continue
            
            # Compress bullet points - keep first 100 chars
            if line.strip().startswith(('- ', '* ', '• ')):
                compressed.append(line[:120])
                continue
            
            # Compress code blocks - keep signatures only
            if line.strip().startswith(('def ', 'class ', 'function ', 'const ', 'let ', 'var ')):
                # Extract just the signature
                compressed.append(line.split('{')[0].split(':')[0].strip())
                continue
            
            # Regular paragraphs - keep first sentence or 150 chars
            if len(line) > 150:
                # Try to find sentence boundary
                end = line.find('. ')
                if end > 0 and end < 200:
                    compressed.append(line[:end+1])
                else:
                    compressed.append(line[:150] + "...")
            else:
                compressed.append(line)
        
        return '\n'.join(compressed)


class CodeCompressor:
    """Compresses code files to signatures + docstrings."""
    
    PATTERNS = {
        '.py': {
            'function': r'^(async\s+)?def\s+\w+\s*\([^)]*\)\s*(?:->.*?)?:',
            'class': r'^class\s+\w+(?:\([^)]*\))?:',
            'docstring': r'"""[\s\S]*?"""|\'\'\'[\s\S]*?\'\'\'',
        },
        '.js': {
            'function': r'^(?:async\s+)?function\s+\w+\s*\([^)]*\)|^(?:const|let|var)\s+\w+\s*=\s*(?:async\s+)?\([^)]*\)\s*=>',
            'class': r'^class\s+\w+(?:\s+extends\s+\w+)?',
        },
        '.ts': {
            'function': r'^(?:async\s+)?function\s+\w+\s*\([^)]*\)|^(?:const|let|var)\s+\w+\s*=\s*(?:async\s+)?\([^)]*\)\s*=>',
            'class': r'^class\s+\w+(?:\s+extends\s+\w+)?',
            'interface': r'^interface\s+\w+',
            'type': r'^type\s+\w+',
        }
    }
    
    def compress(self, content: str, extension: str) -> str:
        """Extract signatures and docstrings from code."""
        lines = content.split('\n')
        compressed = []
        in_docstring = False
        docstring_buffer = []
        
        for i, line in enumerate(lines):
            stripped = line.strip()
            
            # Track docstrings (Python)
            if '"""' in line or "'''" in line:
                if in_docstring:
                    in_docstring = False
                    docstring_buffer.append(line)
                    # Keep first 3 lines of docstring
                    compressed.extend(docstring_buffer[:3])
                    if len(docstring_buffer) > 3:
                        compressed.append('    ...')
                    docstring_buffer = []
                else:
                    in_docstring = True
                    docstring_buffer = [line]
                continue
            
            if in_docstring:
                docstring_buffer.append(line)
                continue
            
            # Keep imports (condensed)
            if stripped.startswith(('import ', 'from ', 'require(', 'const ', 'use ')):
                if 'import' in stripped or 'require' in stripped or 'use ' in stripped:
                    compressed.append(line)
                continue
            
            # Keep class/function definitions
            if stripped.startswith(('def ', 'async def ', 'class ', 'function ', 'interface ', 'type ')):
                compressed.append(line)
                # Get the next line if it's a docstring start
                if i + 1 < len(lines) and ('"""' in lines[i+1] or "'''" in lines[i+1]):
                    continue
            
            # Keep decorators
            if stripped.startswith('@'):
                compressed.append(line)
        
        return '\n'.join(compressed)


class JSONCompressor:
    """Compresses JSON/YAML to schema + samples."""
    
    def compress(self, content: str, extension: str) -> str:
        """Extract schema and sample from JSON."""
        try:
            data = json.loads(content)
            return self._extract_schema(data, max_depth=3)
        except json.JSONDecodeError:
            # Return truncated content if not valid JSON
            return content[:500] + "\n..." if len(content) > 500 else content
    
    def _extract_schema(self, obj: Any, depth: int = 0, max_depth: int = 3) -> str:
        """Recursively extract schema."""
        indent = "  " * depth
        
        if depth >= max_depth:
            return f"{indent}..."
        
        if isinstance(obj, dict):
            if not obj:
                return f"{indent}{{}}"
            lines = [f"{indent}{{"]
            for i, (key, value) in enumerate(list(obj.items())[:5]):  # First 5 keys
                val_schema = self._extract_schema(value, depth + 1, max_depth)
                lines.append(f"{indent}  \"{key}\": {val_schema.strip()},")
            if len(obj) > 5:
                lines.append(f"{indent}  ... ({len(obj) - 5} more keys)")
            lines.append(f"{indent}}}")
            return '\n'.join(lines)
        
        elif isinstance(obj, list):
            if not obj:
                return f"{indent}[]"
            sample = self._extract_schema(obj[0], depth + 1, max_depth)
            return f"[{sample.strip()}] (×{len(obj)})"
        
        elif isinstance(obj, str):
            if len(obj) > 50:
                return f'"{obj[:50]}..."'
            return f'"{obj}"'
        
        elif isinstance(obj, bool):
            return str(obj).lower()
        
        elif isinstance(obj, (int, float)):
            return str(obj)
        
        elif obj is None:
            return "null"
        
        return str(type(obj).__name__)


class TokenPak:
    """Main TokenPak class - Universal Context Compiler."""
    
    # File type mappings
    TEXT_TYPES = {'.md', '.txt', '.rst', '.html', '.htm'}
    CODE_TYPES = {'.py', '.js', '.ts', '.jsx', '.tsx', '.go', '.rs', '.java', '.rb', '.php', '.c', '.cpp', '.h'}
    DATA_TYPES = {'.json', '.yaml', '.yml'}
    SKIP_TYPES = {'.pyc', '.pyo', '.so', '.dylib', '.dll', '.exe', '.bin', '.dat', '.db', '.sqlite'}
    SKIP_DIRS = {'node_modules', '__pycache__', '.git', '.svn', 'venv', 'env', '.env', 'dist', 'build'}
    
    def __init__(self, index_dir: Optional[str] = None):
        """Initialize TokenPak.
        
        Args:
            index_dir: Directory to store the PackIndex. Defaults to .tokenpak in source dir.
        """
        self.index_dir = index_dir
        self.index: Optional[PackIndex] = None
        self.text_compressor = TextCompressor()
        self.code_compressor = CodeCompressor()
        self.json_compressor = JSONCompressor()
    
    def pack(self, source_dir: str, verbose: bool = True) -> PackIndex:
        """Compile a directory into a PackIndex.
        
        Args:
            source_dir: Directory to process
            verbose: Print progress
            
        Returns:
            PackIndex with all compiled blocks
        """
        source_path = Path(source_dir).resolve()
        if not source_path.exists():
            raise FileNotFoundError(f"Directory not found: {source_dir}")
        
        # Setup index directory
        if self.index_dir:
            index_path = Path(self.index_dir)
        else:
            index_path = source_path / '.tokenpak'
        index_path.mkdir(exist_ok=True)
        
        # Initialize index
        self.index = PackIndex(source_dir=str(source_path))
        
        # Collect files
        files = self._collect_files(source_path)
        
        if verbose:
            print(f"📦 Packing {len(files)} files from {source_path}")
        
        # Process each file
        total_raw = 0
        total_compressed = 0
        
        for file_path in files:
            try:
                block = self._process_file(file_path, source_path)
                if block:
                    self.index.blocks[block.block_id] = block
                    total_raw += block.raw_tokens
                    total_compressed += block.compressed_tokens
                    
                    if verbose:
                        ratio = block.compression_ratio
                        print(f"  ✓ {block.block_id}: {block.raw_tokens} → {block.compressed_tokens} tokens ({ratio}x)")
            except Exception as e:
                if verbose:
                    rel_path = file_path.relative_to(source_path)
                    print(f"  ✗ {rel_path}: {e}")
        
        # Calculate stats
        self.index.stats = {
            "total_files": len(self.index.blocks),
            "total_raw_tokens": total_raw,
            "total_compressed_tokens": total_compressed,
            "compression_ratio": round(total_raw / max(total_compressed, 1), 2),
            "tokens_saved": total_raw - total_compressed,
            "savings_percent": round((1 - total_compressed / max(total_raw, 1)) * 100, 1)
        }
        
        # Save index
        self._save_index(index_path)
        
        if verbose:
            print(f"\n📊 Summary:")
            print(f"   Files: {self.index.stats['total_files']}")
            print(f"   Raw tokens: {total_raw:,}")
            print(f"   Compressed: {total_compressed:,}")
            print(f"   Ratio: {self.index.stats['compression_ratio']}x")
            print(f"   Saved: {self.index.stats['savings_percent']}%")
            print(f"\n💾 Index saved to {index_path}")
        
        return self.index
    
    def _collect_files(self, source_path: Path) -> List[Path]:
        """Collect all processable files."""
        files = []
        
        for item in source_path.rglob('*'):
            # Skip directories
            if item.is_dir():
                continue
            
            # Skip hidden files
            if item.name.startswith('.'):
                continue
            
            # Skip files in excluded directories
            if any(skip in item.parts for skip in self.SKIP_DIRS):
                continue
            
            # Skip binary/excluded types
            if item.suffix.lower() in self.SKIP_TYPES:
                continue
            
            files.append(item)
        
        return sorted(files)
    
    def _process_file(self, file_path: Path, source_path: Path) -> Optional[Block]:
        """Process a single file into a Block."""
        rel_path = file_path.relative_to(source_path)
        extension = file_path.suffix.lower()
        
        # Read content
        try:
            content = file_path.read_text(encoding='utf-8')
        except UnicodeDecodeError:
            # Skip binary files
            return None
        
        if not content.strip():
            return None
        
        # Determine content type and compress
        if extension in self.TEXT_TYPES:
            content_type = "text"
            compressed = self.text_compressor.compress(content, extension)
        elif extension in self.CODE_TYPES:
            content_type = "code"
            compressed = self.code_compressor.compress(content, extension)
        elif extension in self.DATA_TYPES:
            content_type = "data"
            compressed = self.json_compressor.compress(content, extension)
        else:
            # Default: treat as text
            content_type = "text"
            compressed = self.text_compressor.compress(content, extension)
        
        # Calculate tokens
        raw_tokens = count_tokens(content)
        compressed_tokens = count_tokens(compressed)
        
        # Generate block ID
        block_id = str(rel_path).replace('/', '.').replace('\\', '.')
        if block_id.startswith('.'):
            block_id = block_id[1:]
        
        # Calculate content hash
        content_hash = hashlib.sha256(content.encode()).hexdigest()[:16]
        
        return Block(
            block_id=block_id,
            source_path=str(rel_path),
            content_type=content_type,
            version=1,
            content_hash=content_hash,
            raw_tokens=raw_tokens,
            compressed_tokens=compressed_tokens,
            compressed_content=compressed,
            metadata={
                "extension": extension,
                "size_bytes": len(content.encode('utf-8'))
            }
        )
    
    def _save_index(self, index_path: Path):
        """Save the index to disk."""
        index_file = index_path / 'index.json'
        with open(index_file, 'w') as f:
            json.dump(self.index.to_dict(), f, indent=2)
        
        # Also save blocks as individual files for inspection
        blocks_dir = index_path / 'blocks'
        blocks_dir.mkdir(exist_ok=True)
        
        for block_id, block in self.index.blocks.items():
            block_file = blocks_dir / f"{block_id}.txt"
            with open(block_file, 'w') as f:
                f.write(f"# {block_id} (v{block.version})\n")
                f.write(f"# Type: {block.content_type}\n")
                f.write(f"# Tokens: {block.raw_tokens} → {block.compressed_tokens}\n")
                f.write(f"# Hash: {block.content_hash}\n")
                f.write("#" + "="*50 + "\n\n")
                f.write(block.compressed_content)
    
    def load_index(self, index_dir: str) -> PackIndex:
        """Load an existing PackIndex."""
        index_path = Path(index_dir)
        index_file = index_path / 'index.json'
        
        if not index_file.exists():
            raise FileNotFoundError(f"No index found at {index_dir}")
        
        with open(index_file) as f:
            data = json.load(f)
        
        self.index = PackIndex(
            version=data['version'],
            created_at=data['created_at'],
            source_dir=data['source_dir'],
            stats=data['stats'],
            blocks={k: Block(**v) for k, v in data['blocks'].items()}
        )
        
        return self.index
    
    def search(self, query: str, limit: int = 5) -> List[Block]:
        """Search the index for relevant blocks.
        
        Simple keyword-based search. In production, would use embeddings.
        """
        if not self.index:
            raise ValueError("No index loaded. Run pack() or load_index() first.")
        
        query_terms = query.lower().split()
        scored_blocks = []
        
        for block_id, block in self.index.blocks.items():
            content_lower = block.compressed_content.lower()
            
            # Score based on term matches
            score = 0
            for term in query_terms:
                if term in content_lower:
                    score += content_lower.count(term)
                if term in block_id.lower():
                    score += 5  # Boost filename matches
            
            if score > 0:
                scored_blocks.append((score, block))
        
        # Sort by score descending
        scored_blocks.sort(key=lambda x: x[0], reverse=True)
        
        return [block for _, block in scored_blocks[:limit]]
    
    def get_context(self, blocks: List[Block], budget: int = 4000) -> str:
        """Assemble context from blocks within a token budget."""
        context_parts = []
        used_tokens = 0
        
        for block in blocks:
            if used_tokens + block.compressed_tokens > budget:
                # Try to fit partial content
                remaining = budget - used_tokens
                if remaining > 100:  # Only include if meaningful
                    partial = block.compressed_content[:remaining * 4]  # Rough char estimate
                    context_parts.append(f"[{block.block_id}]\n{partial}...")
                break
            
            context_parts.append(f"[{block.block_id}]\n{block.compressed_content}")
            used_tokens += block.compressed_tokens
        
        return "\n\n---\n\n".join(context_parts)
    
    def stats(self) -> Dict[str, Any]:
        """Get current index statistics."""
        if not self.index:
            return {"error": "No index loaded"}
        return self.index.stats
