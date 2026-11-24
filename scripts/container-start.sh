#!/usr/bin/env bash
set -euo pipefail
set -a
source /opt/dsv41/production.env
set +a
export RUSTUP_TOOLCHAIN=/cache/rust-1.92.0-aarch64
test -x "$RUSTUP_TOOLCHAIN/bin/rustc"
test -f "$SGLANG_NCCL_SO_PATH"
test "$NCCL_NET" = IB
test "$NCCL_SWITCHLESS_RING_ONLY" = 1
test "$NNODES" = 4
test "$TP_SIZE" = 4
test "$EP_SIZE" = 1
test "$SGLANG_ROCE_ALLREDUCE" = 0
export MAX_TOTAL_TOKENS=8000000
export CHUNKED_PREFILL_SIZE=4096
export MEM_FRACTION_STATIC=0.80
export DSV41_DRAFT_HEAD_FP8_IMPL=tp4
test "${DSPARK_BLOCK_SIZE:-5}" = 5
test "$DSV41_VERIFY_CAP" = conf:0.1
export DSV41_VERIFY_CAP=conf:0.05
test "$DSV41_DRAFT_HEAD_FP8" = 1
test "$CONTEXT_LENGTH" = 1048576
exec python3 -u /opt/dsv41/boot.py run
