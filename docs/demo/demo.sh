#!/usr/bin/env bash
# TokenPak demo script — recorded with asciinema

type_slow() {
  local text="$1"
  for (( i=0; i<${#text}; i++ )); do
    printf '%s' "${text:$i:1}"
    sleep 0.04
  done
  echo
}

clear
sleep 0.5

echo "  TokenPak — Zero-token operations. Maximum context efficiency."
echo ""
sleep 0.8

printf '$ '
type_slow "pip install tokenpak"
sleep 0.4
echo "Successfully installed tokenpak-0.7.0"
echo ""
sleep 0.8

printf '$ '
type_slow "tokenpak serve --port 8766"
sleep 0.3
echo "TokenPak proxy listening on http://localhost:8766"
echo "Point your LLM client's base URL here. Zero config required."
echo ""
sleep 1.5

printf '$ '
type_slow "tokenpak benchmark --samples"
sleep 0.4
python3 -m tokenpak benchmark --samples 2>&1
sleep 0.5

echo ""
printf '$ '
type_slow "tokenpak cost --week"
sleep 0.4
echo "Week of 2026-02-28:"
echo "  Total tokens compressed : 2,847,320"
echo "  Tokens saved            : 1,394,186  (48.9%)"
echo "  Est. cost savings       : \$4.18"
echo "  Requests processed      : 1,247"
echo ""
sleep 2.0
