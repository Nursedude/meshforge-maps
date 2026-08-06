#!/bin/bash
# meshforge-maps healthcheck — would CI be green right now?
#
# Mirrors CI's ruff invocation. Driven by the 2026-05-03 ecosystem
# audit which found this repo had been red 3 days unnoticed.
#
# Usage:
#   scripts/healthcheck.sh                # lint + tests
#   scripts/healthcheck.sh --lint-only
#   scripts/healthcheck.sh --tests-only
#
# Exit: 0 ok / 1 lint / 2 tests / 3 setup

set -u
RUN_LINT=1
RUN_TESTS=1
for a in "$@"; do
    case "$a" in
        --lint-only) RUN_TESTS=0 ;;
        --tests-only) RUN_LINT=0 ;;
        --help|-h) head -15 "$0" | sed 's/^# \?//' ; exit 0 ;;
    esac
done

cd "$(dirname "$0")/.."
RUFF="${RUFF:-$HOME/.local/bin/ruff}"
[ -x "$RUFF" ] || RUFF="$(command -v ruff)" || true

print_ok()   { printf "\033[1;32m✓\033[0m %s\n" "$1"; }
print_fail() { printf "\033[1;31m✗\033[0m %s\n" "$1"; }
print_step() { printf "\n\033[1;36m=== %s ===\033[0m\n" "$1"; }

RC=0

if [ "$RUN_LINT" -eq 1 ]; then
    print_step "Lint (ruff)"
    if [ -z "$RUFF" ] || [ ! -x "$RUFF" ]; then
        print_fail "ruff not installed (pip install --user ruff)"
        RC=3
    elif "$RUFF" check src/ tests/ ; then
        print_ok "Lint passed"
    else
        print_fail "Lint failed"
        RC=1
    fi
fi

if [ "$RUN_TESTS" -eq 1 ] && [ "$RC" -eq 0 ]; then
    print_step "Tests"
    # Consumer-of-record interpreter (MF/MA honest_status port 2026-07-19):
    # the service's ExecStart runs the repo venv python — test THAT env, not
    # whatever system python3 happens to carry. Falls back on venv-less boxes.
    PY="python3"
    [ -x "venv/bin/python" ] && PY="venv/bin/python"
    # File-capture the REAL pytest exit code — `pytest | tail` tests tail's
    # exit, so a failing suite read as healthy (calibrated_claims rule 4;
    # feedback_verify_ci_exit_code_not_masked). Tail is for display only.
    # Then classify from LOG + rc (MF pytest_verdict port, 2026-08-06): the
    # rc alone was measured flapping to 0 on FAILED runs — the flap only
    # loses failures toward zero. UNKNOWN is never a pass.
    PYTEST_LOG="$(mktemp)"
    "$PY" -m pytest tests/ -q --tb=short --timeout=30 --timeout-method=thread \
        >"$PYTEST_LOG" 2>&1; PYTEST_RC=$?
    tail -30 "$PYTEST_LOG"
    if VERDICT="$(PYTEST_VERDICT_PY="$PY" bash scripts/pytest_verdict.sh \
                  --log "$PYTEST_LOG" --rc "$PYTEST_RC")"; then
        print_ok "Tests — $VERDICT"
    else
        print_fail "Tests — $VERDICT"
        RC=2
    fi
    rm -f "$PYTEST_LOG"
fi

print_step "Summary"
[ "$RC" -eq 0 ] && print_ok "All checks passed — CI should be green on push" || print_fail "Failures detected — fix before pushing"
exit "$RC"
