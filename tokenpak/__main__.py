# SPDX-License-Identifier: MIT
"""Allow `python -m tokenpak` to invoke the CLI."""

# Import directly from _cli_core to bypass package namespace issues
# This ensures that even if tokenpak.cli is a broken namespace package,
# we can still invoke the CLI correctly.
from tokenpak._cli_core import main

if __name__ == "__main__":
    main()
