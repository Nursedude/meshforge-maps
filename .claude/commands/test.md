# Run Tests

Execute the meshforge-maps test suite and report results.

## Instructions

1. Run all tests:
```bash
cd /opt/meshforge-maps
pytest tests/ -v --tb=short 2>&1 | head -120
```

2. Report pass/fail/skip counts and any failures
3. If failures found, read failing test and source, then fix
