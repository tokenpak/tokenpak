#!/usr/bin/env python3
from setuptools import setup, find_packages

setup(
    name="tokenpak",
    version="1.0.0",
    description="Deterministic token compression and context optimization for LLMs",
    author="OpenClaw",
    packages=find_packages(),
    python_requires=">=3.9",
    entry_points={
        "console_scripts": [
            "tokenpak=tokenpak.cli:main",
        ],
    },
    install_requires=[
        "pandas>=1.3.0",
        "pyyaml>=5.4",
        "flask>=2.0.0",
    ],
)
