# Code Review

Review recent changes for security, quality, and MeshForge ecosystem compliance.

## Instructions

1. Check for security rule violations:
```bash
cd /opt/meshforge-maps
# MF001: Path.home()
grep -rn "Path\.home()" src/ --include="*.py" | grep -v test | grep -v "def get_real"

# MF002: shell=True
grep -rn "shell=True" src/ --include="*.py"

# MF003: bare except
grep -rn "except:" src/ --include="*.py" | grep -v "except "

# MF004: missing timeout
grep -rn "subprocess\.\(run\|call\|check_output\)" src/ --include="*.py" | grep -v timeout

# XSS: unescaped HTML output
grep -rn "\.format\|f'" src/ --include="*.py" | grep -i "html\|response"
```

2. Run tests to verify no regressions
3. Check coordinate validation uses `validate_coordinates()`
4. Check GeoJSON uses `make_feature()` / `make_feature_collection()`
5. Report findings with severity and fix suggestions
