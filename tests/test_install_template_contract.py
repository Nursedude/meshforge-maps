"""Contract tests for scripts/install.sh + scripts/meshforge-maps.service.

Phase D-1 of the map-domain audit arc closed F4: a placeholder
mismatch between the systemd unit template and the install.sh sed
substitution caused fresh installs to crash-loop in 226/NAMESPACE.
The fix harmonized both files on the literal `pi` placeholder.

These tests lock the contract so the bug can't come back. They
read the files as text — no install.sh execution required.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest


REPO_ROOT = Path(__file__).resolve().parents[1]
SERVICE = REPO_ROOT / "scripts" / "meshforge-maps.service"
INSTALL = REPO_ROOT / "scripts" / "install.sh"


@pytest.fixture(scope="module")
def service_text() -> str:
    return SERVICE.read_text()


@pytest.fixture(scope="module")
def install_text() -> str:
    return INSTALL.read_text()


class TestTemplatePlaceholders:
    """The template MUST use literal `pi` placeholders.

    install.sh substitutes these to the real user's name + home at
    deploy time. Any drift (operator-specific values, alt placeholder
    syntax) breaks deploys for users whose name isn't whatever value
    is hardcoded.
    """

    def test_user_is_pi_placeholder(self, service_text):
        assert re.search(r"^User=pi$", service_text, re.MULTILINE), (
            "Template must use literal `User=pi` — install.sh subs to "
            "the real user. F4 reincarnation if any operator name "
            "leaks back in (e.g. User=wh6gxz)."
        )

    def test_group_is_pi_placeholder(self, service_text):
        assert re.search(r"^Group=pi$", service_text, re.MULTILINE)

    def test_readwrite_paths_use_home_pi(self, service_text):
        # Every ReadWritePaths under /home must use /home/pi (NOT
        # /home/<user> or any other sentinel install.sh can't see).
        for line in service_text.splitlines():
            if line.startswith("ReadWritePaths=/home/"):
                assert line.startswith("ReadWritePaths=/home/pi/"), (
                    f"ReadWritePaths must use /home/pi/ — install.sh "
                    f"subs /home/pi -> $REAL_HOME. Found: {line!r}"
                )

    def test_no_operator_names_baked_in(self, service_text):
        # Direct guard against the F4 root-cause pattern.
        forbidden = ["wh6gxz", "/home/<user>", "/home/wh6gxz"]
        for needle in forbidden:
            assert needle not in service_text, (
                f"Operator-specific value {needle!r} leaked into "
                f"the systemd unit template — Phase D-1 of the "
                f"map-domain audit arc closed this; do not regress."
            )


class TestInstallSubstitutions:
    """install.sh's sed commands must agree with the template's
    placeholder convention."""

    def test_install_subs_user_pi(self, install_text):
        # The substitution that turns User=pi into User=$REAL_USER
        assert "s|User=pi|User=$REAL_USER|g" in install_text, (
            "install.sh must sub User=pi — placeholder mismatch with "
            "template breaks deploys (the F4 mode)."
        )

    def test_install_subs_group_pi(self, install_text):
        assert "s|Group=pi|Group=$REAL_USER|g" in install_text

    def test_install_subs_home_pi(self, install_text):
        assert "s|/home/pi|$REAL_HOME|g" in install_text, (
            "install.sh must sub /home/pi — placeholder mismatch "
            "with template's ReadWritePaths breaks deploys."
        )

    def test_install_does_not_exclude_dot_git(self, install_text):
        # F4 third leg: the rsync step must include .git/ so the
        # installed dir is a fleet_sync-ready git checkout.
        # Confirm there's an rsync at all, then forbid the .git
        # exclusion anywhere in the file (it's the only place the
        # line should appear).
        assert "rsync " in install_text, "expected an rsync call in install.sh"
        assert "--exclude='.git'" not in install_text, (
            "install.sh rsync must NOT exclude .git/ — without it, "
            "fleet_sync.sh reports no_repo and skips the host. "
            "F4 third leg, do not regress."
        )

    def test_install_initializes_git_when_missing(self, install_text):
        # Install.sh ALSO needs to handle the tarball-download case
        # where the source has no .git/. Look for the init-checkout
        # block we added in Phase D-1.
        assert "git init" in install_text, (
            "install.sh must `git init` when source has no .git/ "
            "(tarball download case) so fleet_sync can reach the box."
        )
        assert "git fetch" in install_text and "origin main" in install_text


class TestServiceFileSecurity:
    """Smoke checks that we didn't loosen the existing security
    posture while fixing F4."""

    def test_protect_system_strict(self, service_text):
        assert re.search(r"^ProtectSystem=strict$", service_text, re.MULTILINE)

    def test_protect_home_readonly(self, service_text):
        assert re.search(r"^ProtectHome=read-only$", service_text, re.MULTILINE)

    def test_no_new_privileges(self, service_text):
        assert re.search(r"^NoNewPrivileges=true$", service_text, re.MULTILINE)

    def test_start_limit_burst_set(self, service_text):
        # Crash-loop protection — the F4 incident hit StartLimitBurst.
        # If anyone removes it, a future placeholder bug runs unbounded.
        assert re.search(r"^StartLimitBurst=", service_text, re.MULTILINE)
        assert re.search(r"^StartLimitIntervalSec=", service_text, re.MULTILINE)
