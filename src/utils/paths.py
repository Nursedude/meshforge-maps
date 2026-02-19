"""
MeshForge Maps - Safe Path Utilities

Provides sudo/systemd-safe home directory resolution.
Ported from meshforge core's utils/paths.py pattern to fix
Path.home() returning /root when run via sudo or systemd.
"""

import logging
import os
import pwd
from pathlib import Path

logger = logging.getLogger(__name__)


def get_real_home() -> Path:
    """Get the real user's home directory, even under sudo or systemd.

    Path.home() returns /root when the process runs as root via sudo
    or as a systemd service. This function resolves the actual user's
    home by checking:
      1. SUDO_USER env var (set by sudo)
      2. LOGNAME / USER env vars (set by login shell)
      3. pwd database lookup for the effective UID
      4. Path.home() as final fallback

    Returns:
        Path to the real user's home directory.
    """
    # 1. Under sudo, SUDO_USER holds the original username
    sudo_user = os.environ.get("SUDO_USER")
    if sudo_user:
        try:
            return Path(pwd.getpwnam(sudo_user).pw_dir)
        except KeyError:
            pass

    # 2. Check LOGNAME or USER (set by login shells, systemd User= directive)
    for var in ("LOGNAME", "USER"):
        username = os.environ.get(var)
        if username and username != "root":
            try:
                return Path(pwd.getpwnam(username).pw_dir)
            except KeyError:
                pass

    # 3. Look up the effective UID in the password database
    try:
        pw = pwd.getpwuid(os.getuid())
        if pw.pw_dir and pw.pw_name != "root":
            return Path(pw.pw_dir)
    except KeyError:
        pass

    # 4. Final fallback
    return Path.home()


# Convenience paths derived from get_real_home()
def get_data_dir() -> Path:
    """~/.local/share/meshforge — data files, caches, databases."""
    return get_real_home() / ".local" / "share" / "meshforge"


def get_config_dir() -> Path:
    """~/.config/meshforge — configuration files."""
    return get_real_home() / ".config" / "meshforge"


def get_cache_dir() -> Path:
    """~/.cache/meshforge — logs, temporary files."""
    return get_real_home() / ".cache" / "meshforge"
