"""The cryptography/pyOpenSSL pin is ONE constant, in four places.

Ported from meshforge 558ffe55 (2026-09-05) on 2026-09-19. That fix's own
lesson was that raising the pin alone is a half-fix: the constraint was
hardcoded in five more places, so a fresh install or an in-app repair would
have re-installed the vulnerable range. This repo had four copies
(requirements.txt, pyproject.toml [tls], README.md's pip line, CLAUDE.md's
rule) and they had already drifted from the parent by two weeks.

requirements.txt is the SSOT. These tests DERIVE from it rather than
restating the versions, so the pin can be raised in one place -- the
honest_failure_modes #5 form of "two consumers of one artifact share ONE
constant" across a txt/toml/markdown boundary.
"""
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

#: The floor below which the pair is known-vulnerable. This is the ONE version
#: number the tests own, deliberately: it encodes the security DECISION (not
#: the current pin), so lowering requirements.txt to a range with known highs
#: fails here instead of passing quietly. GHSA-g6cj-pr64-35w5 (high, PKCS#7
#: Bleichenbacher) is `< 50.0.0`, so 49.x is not enough. Measured against the
#: external advisory DB 2026-09-19: 46.0.7 -> 4, 49.0.0 -> 1, 50.0.1 -> 0.
MIN_SAFE_CRYPTOGRAPHY = (50, 0, 1)


def _requirements_pin(pkg):
    """The (floor, raw_spec) for `pkg` as requirements.txt declares it."""
    for line in (ROOT / "requirements.txt").read_text().splitlines():
        line = line.strip()
        if line.startswith("#") or not line:
            continue
        if line.lower().startswith(pkg.lower() + ">="):
            floor = re.match(rf"{pkg}>=([0-9.]+)", line, re.I).group(1)
            return tuple(int(x) for x in floor.split(".")), line
    raise AssertionError(f"{pkg} not pinned in requirements.txt")


def test_cryptography_floor_is_not_below_the_known_safe_version():
    floor, spec = _requirements_pin("cryptography")
    assert floor >= MIN_SAFE_CRYPTOGRAPHY, (
        f"requirements.txt has {spec}; anything below "
        f"{'.'.join(map(str, MIN_SAFE_CRYPTOGRAPHY))} ships a known high "
        f"(GHSA-g6cj-pr64-35w5 is <50.0.0). Re-derive before changing: "
        f"gh api '/advisories?ecosystem=pip&affects=cryptography@<v>' --jq length"
    )


def test_cryptography_ceiling_does_not_forbid_the_floor():
    """The 2026-02 defect in one line: a ceiling that excludes the patch."""
    _, spec = _requirements_pin("cryptography")
    ceiling = re.search(r"<([0-9.]+)", spec)
    assert ceiling, f"unexplained/absent ceiling in {spec!r}"
    cap = tuple(int(x) for x in ceiling.group(1).split("."))
    cap = cap + (0,) * (3 - len(cap))
    assert cap > MIN_SAFE_CRYPTOGRAPHY, (
        f"{spec} forbids the patched line -- an install can be fully "
        f"requirements-COMPLIANT and vulnerable at the same time. "
        f"That is exactly the defect this file exists to prevent."
    )


def test_pyproject_tls_extra_matches_requirements():
    _, crypto = _requirements_pin("cryptography")
    _, pyssl = _requirements_pin("pyopenssl")
    tls = [line for line in (ROOT / "pyproject.toml").read_text().splitlines()
           if line.strip().startswith("tls =")]
    assert tls, "no [tls] extra in pyproject.toml"
    for spec in (crypto, pyssl):
        assert spec in tls[0], (
            f"pyproject.toml [tls] is out of step with requirements.txt: "
            f"expected {spec!r} in {tls[0].strip()!r}"
        )


def test_readme_install_line_matches_requirements():
    """A README pip line is a copy a human will paste into a shell."""
    _, crypto = _requirements_pin("cryptography")
    _, pyssl = _requirements_pin("pyopenssl")
    readme = (ROOT / "README.md").read_text()
    for spec in (crypto, pyssl):
        assert f"'{spec}'" in readme, (
            f"README.md's pip install line does not offer {spec!r}; a reader "
            f"pasting it would install a different range than requirements.txt"
        )


def test_claude_md_rule_matches_requirements():
    """CLAUDE.md is the instruction a future session follows; if it still
    names the old pin, the next session re-introduces it in good faith."""
    _, crypto = _requirements_pin("cryptography")
    _, pyssl = _requirements_pin("pyopenssl")
    claude = (ROOT / "CLAUDE.md").read_text()
    for spec in (crypto, pyssl):
        assert spec in claude, f"CLAUDE.md does not state the current pin {spec!r}"
