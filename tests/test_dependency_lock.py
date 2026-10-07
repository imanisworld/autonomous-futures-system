"""U11: one authoritative, complete production dependency lock."""
from __future__ import annotations

import importlib.metadata as md
from pathlib import Path

import pytest

from ops import dependency_lock as dl

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(scope="module")
def lock():
    return dl.parse_lock((ROOT / dl.LOCK_REL).read_text(encoding="utf-8"))


def test_lock_satisfies_every_direct_requirement(lock):
    assert dl.requirement_problems(ROOT / dl.REQUIREMENTS_REL, lock) == []


def test_every_production_import_resolves_to_a_locked_distribution(lock):
    imports = dl.production_third_party_imports(ROOT)
    assert dl.import_problems(imports, lock, md.packages_distributions()) == []


def test_previously_unlocked_runtime_dependencies_are_locked(lock):
    # The pre-U11 lock omitted these although requirements.txt / imports need them.
    for name in ("webull-openapi-python-sdk", "requests", "packaging", "paho-mqtt", "grpcio", "protobuf"):
        assert name in lock, name


def test_separate_components_are_declared_not_silently_skipped():
    assert "ops/push_relay" in dl.SEPARATE_COMPONENTS
    assert (ROOT / "ops/push_relay/requirements.txt").is_file()


@pytest.mark.parametrize(
    "text,match",
    [
        ("fastapi>=0.1\n", "exact name==version"),
        ("fastapi==1.0\nFastAPI==1.0\n", "more than once"),
        ("# only comments\n", "pins nothing"),
        ("fastapi\n", "exact name==version"),
    ],
)
def test_lock_lines_must_be_exact_unique_pins(text, match):
    with pytest.raises(dl.LockError, match=match):
        dl.parse_lock(text)


def test_freeze_must_equal_lock_exactly():
    lock = dl.parse_lock("a==1.0\nb_c==2.0\n")
    assert dl.freeze_problems(lock, dl.parse_freeze("A==1.0\nb-c==2.0\npip==25.0\nsetuptools==80\n")) == []
    problems = dl.freeze_problems(lock, dl.parse_freeze("a==1.1\nextra==3.0\n"))
    assert "a installed 1.1 but lock pins 1.0" in problems
    assert "locked b-c==2.0 is not installed" in problems
    assert "extra==3.0 is installed but not in the lock" in problems


def test_freeze_duplicate_distribution_is_refused():
    with pytest.raises(dl.LockError, match="more than once"):
        dl.parse_freeze("A==1.0\na==1.0\n")


def test_nested_file_stem_does_not_hide_third_party_import(tmp_path):
    (tmp_path / "nested").mkdir()
    (tmp_path / "nested/helper.py").write_text("VALUE = 1\n", encoding="utf-8")
    (tmp_path / "app.py").write_text("import helper\n", encoding="utf-8")
    imports = dl.production_third_party_imports(tmp_path)
    assert "helper" in imports
    problems = dl.import_problems(imports, {}, {"helper": ["third-party-helper"]})
    assert any("third-party-helper" in problem and "not in the lock" in problem for problem in problems)


def test_real_top_level_local_module_is_not_treated_as_third_party(tmp_path):
    (tmp_path / "helper.py").write_text("VALUE = 1\n", encoding="utf-8")
    (tmp_path / "app.py").write_text("import helper\n", encoding="utf-8")
    assert "helper" not in dl.production_third_party_imports(tmp_path)



def test_requirement_drift_is_detected(tmp_path):
    base = tmp_path / "base.txt"
    base.write_text("fastapi>=2.0\n")
    req = tmp_path / "requirements.txt"
    req.write_text("-r base.txt\nmissing-pkg>=1\nwin-only>=1; sys_platform == 'win32'\n")
    problems = dl.requirement_problems(req, {"fastapi": "1.5"})
    assert any("does not satisfy 'fastapi>=2.0'" in p for p in problems)
    assert any("'missing-pkg>=1' is not in the lock" in p for p in problems)
    assert not any("win-only" in p for p in problems)


def test_unlocked_or_unknown_imports_fail():
    problems = dl.import_problems(
        {"yaml": {"a.py"}, "mystery": {"b.py"}, "requests": {"c.py"}},
        {"pyyaml": "6"},
        {"yaml": ["PyYAML"], "requests": ["requests"]},
    )
    assert any("'mystery'" in p and "no known distribution" in p for p in problems)
    assert any("'requests'" in p and "not in the lock" in p for p in problems)
    assert not any("'yaml'" in p for p in problems)


def test_invalid_requirement_syntax_fails_closed(tmp_path):
    req = tmp_path / "requirements.txt"
    req.write_text("not a valid requirement ???\n", encoding="utf-8")
    with pytest.raises(dl.LockError, match="invalid requirement"):
        dl.requirement_problems(req, {})



def test_check_freeze_cli_exit_codes(tmp_path):
    lock = tmp_path / "lock.txt"
    lock.write_text("a==1.0\n")
    good = tmp_path / "good.txt"
    good.write_text("a==1.0\npip==25\n")
    bad = tmp_path / "bad.txt"
    bad.write_text("a==2.0\n")
    assert dl.main(["check-freeze", "--lock", str(lock), "--freeze", str(good)]) == 0
    assert dl.main(["check-freeze", "--lock", str(lock), "--freeze", str(bad)]) == 1
    lock.write_text("a>=1\n")
    assert dl.main(["check-freeze", "--lock", str(lock), "--freeze", str(good)]) == 2
