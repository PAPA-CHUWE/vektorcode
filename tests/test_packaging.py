"""Packaging guards.

These catch the "works from source, breaks once installed" failures: data
files left out of the wheel, a version string that drifts from
pyproject.toml, an entry point pointing at a function that moved.

Run directly: python3 tests/test_packaging.py
"""
from __future__ import annotations

import sys
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

_passed, _failed = 0, 0


def check(cond, label):
    global _passed, _failed
    if cond:
        _passed += 1
        print(f"  PASS: {label}")
    else:
        _failed += 1
        print(f"  FAIL: {label}")


def _pyproject() -> dict:
    with open(ROOT / "pyproject.toml", "rb") as handle:
        return tomllib.load(handle)


def test_version_has_one_source_of_truth():
    """A hardcoded copy in config.py used to sit alongside pyproject.toml."""
    from app.config import __version__

    declared = _pyproject()["project"]["version"]
    check(__version__ == declared,
          f"app.__version__ matches pyproject ({__version__} vs {declared})")

    hardcoded = [
        path for path in (ROOT / "app").rglob("*.py")
        if f'"{declared}"' in path.read_text(encoding="utf-8")
        and "pyproject" not in path.name
    ]
    check(not hardcoded,
          f"no module hardcodes the version string ({[p.name for p in hardcoded]})")


def test_entry_point_resolves():
    """`vektorcode` is useless if the entry point names a missing function."""
    scripts = _pyproject()["project"]["scripts"]
    check("vektorcode" in scripts, "a vektorcode console script is declared")

    module_path, _, attribute = scripts["vektorcode"].partition(":")
    module = __import__(module_path, fromlist=[attribute])
    check(callable(getattr(module, attribute, None)),
          f"{scripts['vektorcode']} resolves to a callable")


def test_declared_dependencies_cover_what_is_imported():
    """A missing dependency only shows up on someone else's machine."""
    import re

    declared = {
        re.split(r"[<>=!\[]", spec)[0].strip().lower().replace("-", "_")
        for spec in _pyproject()["project"]["dependencies"]
    }
    stdlib = set(sys.stdlib_module_names)

    imported: set[str] = set()
    for path in (ROOT / "app").rglob("*.py"):
        for match in re.findall(r"^\s*(?:from|import)\s+([a-zA-Z0-9_]+)",
                                path.read_text(encoding="utf-8"), re.MULTILINE):
            if match not in stdlib and match != "app":
                imported.add(match.lower())

    missing = imported - declared
    check(not missing, f"every third-party import is declared ({sorted(missing)})")


def test_skills_are_package_data_not_just_source():
    """18 markdown files load at runtime; a wheel without them silently
    disables every skill."""
    from app.skills.loader import load_skills, skills_dir

    on_disk = sorted(skills_dir().glob("*.md"))
    check(len(on_disk) > 0, f"skill files exist ({len(on_disk)})")
    check(len(load_skills()) == len(on_disk), "every skill file parses")

    # Anything non-.py under app/ must survive the build.
    data_files = [
        p.relative_to(ROOT) for p in (ROOT / "app").rglob("*")
        if p.is_file() and p.suffix not in (".py", ".pyc")
        and "__pycache__" not in str(p)
    ]
    unexpected = [p for p in data_files if p.suffix != ".md"]
    check(not unexpected,
          f"no stray non-markdown files inside the package ({unexpected})")


def test_requires_python_matches_the_syntax_used():
    """PEP 604 unions (`X | Y`) need 3.10; claiming less would install and
    then fail at import."""
    import re

    declared = _pyproject()["project"]["requires-python"]
    check(">=3.10" in declared, f"requires-python is at least 3.10 ({declared})")

    uses_pep604 = [
        path.name for path in (ROOT / "app").rglob("*.py")
        if re.search(r":\s*[\w\[\], ]+\s\|\s(None|\w+)", path.read_text(encoding="utf-8"))
    ]
    check(bool(uses_pep604), f"PEP 604 syntax is in fact used ({len(uses_pep604)} files)")


def test_licence_is_declared_and_not_permissive_by_accident():
    """pyproject once declared MIT with no LICENSE file to back it — a
    standing grant to use, sell and close-source the project."""
    from importlib.metadata import PackageNotFoundError, metadata

    project = _pyproject()["project"]
    licence_file = ROOT / "LICENSE"

    check(licence_file.is_file(), "a LICENSE file exists")
    check(project.get("license", {}).get("file") == "LICENSE",
          f"pyproject points at it ({project.get('license')})")

    classifiers = project.get("classifiers", [])
    osi = [c for c in classifiers if c.startswith("License :: OSI Approved")]
    check(not osi, f"no OSI licence classifier is claimed ({osi})")
    check("Private :: Do Not Upload" in classifiers,
          "the private classifier blocks an accidental PyPI upload")

    text = licence_file.read_text(encoding="utf-8")
    check("All rights reserved" in text, "the licence reserves rights")

    # The built artifact must agree with the repo.
    try:
        declared = metadata("vektorcode")["License"] or ""
        check("All rights reserved" in declared,
              f"installed metadata agrees ({declared.splitlines()[0][:50]})")
    except PackageNotFoundError:
        check(True, "not installed here; repo declaration checked instead")


def test_readme_leads_with_pipx():
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    check("pipx install vektorcode" in readme, "README documents the pipx install")
    check("pip install --user vektorcode" in readme, "README documents the pip fallback")
    check(readme.index("pipx install vektorcode") < readme.index("pip install --user"),
          "pipx is presented first, as the recommended path")


if __name__ == "__main__":
    for fn in [v for k, v in sorted(globals().items()) if k.startswith("test_")]:
        fn()
    print(f"\n{'=' * 40}\nResults: {_passed} passed, {_failed} failed")
    sys.exit(1 if _failed else 0)
