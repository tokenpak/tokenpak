"""
pytest configuration for middleware tests.
"""

import sys
from pathlib import Path

# Add tokenpak to path
tokenpak_root = Path(__file__).parent.parent.parent.parent
sys.path.insert(0, str(tokenpak_root))
