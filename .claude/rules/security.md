# Security Rules — meshforge-maps

Shared with the MeshForge ecosystem. See `meshforge/.claude/rules/security.md` for canonical versions.

## MF001: Path.home() — NEVER use directly

```python
# WRONG
config_path = Path.home() / ".config"

# CORRECT
from src.utils.paths import get_real_home
config_path = get_real_home() / ".config"
```

## MF002: shell=True — NEVER use

```python
# WRONG
subprocess.run(f"rnstatus {arg}", shell=True)

# CORRECT
subprocess.run(["rnstatus", arg], timeout=30)
```

## MF003: Bare except — Always specify exception type

## MF004: subprocess timeout — ALWAYS include

## Repo-Specific Rules

### HTML-escape all browser output
XSS prevention — never render unescaped user data in map HTML.

### API key comparison — timing-safe
```python
import hmac
# CORRECT
hmac.compare_digest(provided_key, expected_key)

# WRONG
if provided_key == expected_key:
```

### Network bindings
Default to `127.0.0.1`. Only `0.0.0.0` when explicitly configured.

### Node ID validation
Use `NODE_ID_RE` regex from `src/collectors/base.py`:
```python
NODE_ID_RE = re.compile(r'^!?[0-9a-fA-F]{1,16}$')
```

### Query parameter safety
Use `_safe_query_param()` helper — never access raw query dicts.

### Security headers on all responses
`X-Content-Type-Options: nosniff`, `X-Frame-Options: DENY`, `Content-Security-Policy`.

### Config file permissions
Write settings.json with umask `0o077` (owner-only) to protect MQTT credentials.

### CORS disabled by default
`cors_allowed_origin: None` in config.
