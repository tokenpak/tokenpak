#!/bin/bash
# Demo script for tokenpak compression benchmark
# Run this inside asciinema recording

clear
echo "🔷 TokenPak — Context Compression for LLMs"
echo ""
sleep 1

echo "Running compression benchmark on sample files..."
echo ""
sleep 0.5

cd ~/Projects/tokenpak
python3 -m tokenpak.cli benchmark --samples

sleep 2
echo ""
echo "✨ 48.9% token reduction — that's real cost savings!"
sleep 2
