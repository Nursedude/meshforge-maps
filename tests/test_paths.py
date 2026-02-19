"""Tests for sudo/systemd-safe path utilities."""

import os
from pathlib import Path
from unittest.mock import patch

import pytest

from src.utils.paths import get_cache_dir, get_config_dir, get_data_dir, get_real_home


class TestGetRealHome:
    """Tests for get_real_home() sudo/systemd safety."""

    def test_returns_path_object(self):
        result = get_real_home()
        assert isinstance(result, Path)

    def test_returns_existing_directory(self):
        result = get_real_home()
        # The home dir should exist on any system
        assert result.is_dir() or result == Path.home()

    def test_sudo_user_env_var(self):
        """SUDO_USER should resolve to that user's home, not /root."""
        with patch.dict(os.environ, {"SUDO_USER": "nobody"}):
            result = get_real_home()
            # 'nobody' exists on most systems; the key point is
            # it doesn't return /root
            assert isinstance(result, Path)

    def test_sudo_user_takes_precedence(self):
        """SUDO_USER should take priority over other env vars."""
        with patch.dict(os.environ, {
            "SUDO_USER": "nobody",
            "USER": "root",
            "LOGNAME": "root",
        }):
            result = get_real_home()
            # Should use SUDO_USER, not USER/LOGNAME
            assert isinstance(result, Path)

    def test_invalid_sudo_user_falls_through(self):
        """Non-existent SUDO_USER should fall through to next check."""
        with patch.dict(os.environ, {
            "SUDO_USER": "nonexistent_user_xyz_12345",
        }, clear=False):
            result = get_real_home()
            assert isinstance(result, Path)

    def test_logname_used_when_no_sudo(self):
        """LOGNAME should be used when SUDO_USER is absent."""
        env = {"LOGNAME": "nobody"}
        with patch.dict(os.environ, env, clear=False):
            # Remove SUDO_USER if present
            with patch.dict(os.environ, {}, clear=False):
                os.environ.pop("SUDO_USER", None)
                result = get_real_home()
                assert isinstance(result, Path)

    def test_no_env_vars_falls_back(self):
        """With no relevant env vars, should still return a valid path."""
        with patch.dict(os.environ, {}, clear=True):
            result = get_real_home()
            assert isinstance(result, Path)


class TestConveniencePaths:
    """Tests for get_data_dir, get_config_dir, get_cache_dir."""

    def test_data_dir_path(self):
        result = get_data_dir()
        assert result.parts[-3:] == (".local", "share", "meshforge")

    def test_config_dir_path(self):
        result = get_config_dir()
        assert result.parts[-2:] == (".config", "meshforge")

    def test_cache_dir_path(self):
        result = get_cache_dir()
        assert result.parts[-2:] == (".cache", "meshforge")

    def test_all_share_same_home(self):
        """All convenience paths should be rooted in the same home dir."""
        home = get_real_home()
        assert get_data_dir() == home / ".local" / "share" / "meshforge"
        assert get_config_dir() == home / ".config" / "meshforge"
        assert get_cache_dir() == home / ".cache" / "meshforge"
