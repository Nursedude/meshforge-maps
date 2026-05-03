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
    if python3 -m pytest tests/ -q --tb=short --timeout=30 --timeout-method=thread 2>&1 | tail -30 ; then
        print_ok "Tests passed"
    else
        print_fail "Tests failed"
        RC=2
    fi
fi

print_step "Summary"
[ "$RC" -eq 0 ] && print_ok "All checks passed — CI should be green on push" || print_fail "Failures detected — fix before pushing"
exit "$RC"
