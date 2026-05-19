#!/usr/bin/env bash
# Run all three second-brain benchmarks and save outputs to out/.
# Run from the repo root: bash tests/sb_bench/run.sh

set -e
BENCH=tests/sb_bench
OUT=$BENCH/out

echo "=== B1: Evidence fabrication guard (synthesize) ==="
python -m chatbot.services.second_brain synthesize \
  --topics $BENCH/b1_topics.json \
  --ctx    $BENCH/b1_ctx.json \
  --out    $OUT/b1_notes.json

echo ""
echo "=== B2: Calendar noise rejection (pick) ==="
python -m chatbot.services.second_brain pick \
  --ctx $BENCH/b2_ctx.json \
  --out $OUT/b2_topics.json

echo ""
echo "=== B3: Cross-source threading (pick) ==="
python -m chatbot.services.second_brain pick \
  --ctx $BENCH/b3_ctx.json \
  --out $OUT/b3_topics.json

echo ""
echo "Outputs written to $OUT/. Review against BENCHMARKS.md."
