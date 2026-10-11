"""File-ownership facts and gating (IP-011). No network.

Pure tests for apply_ownership and the static-rule exemption run everywhere; the
fact tests and the twelve end-to-end fixtures build a real git repository in a
temporary directory and are skipped when git is unavailable."""
from __future__ import annotations

import copy
import json
import os
import shutil
import subprocess
import tempfile
from pathlib import Path

import pytest

from fluid import ownership
from fluid.ledger import Ledger
from fluid.policy import apply_ownership, evaluate_gate, load_pack, static_denial

POLICY = load_pack("gate")["policy"]
RISK_ORDER = POLICY["risk_order"]
_HAS_GIT = shutil.which("git") is not None
gitonly = pytest.mark.skipif(not _HAS_GIT, reason="git not on PATH")

# pytest's tmp dirs live under the system temp directory, which is a scratch root
# by pack default ($TMPDIR), so every sandbox target would read as under_scratch.
# The sandbox tests therefore use a policy whose scratch root is FLUID_SCRATCH_DIRS
# only; the $TMPDIR default is exercised separately in test_tmpdir_is_scratch.
SANDBOX_POLICY = copy.deepcopy(POLICY)
SANDBOX_POLICY["facts"]["scratch_roots"] = ["$FLUID_SCRATCH_DIRS"]


def _noul(p):
    return {"type": "noul", "noul": p}


def _clean_axes(operation="mutating"):
    axes = {a: _noul(0.02) for a in ("destructive", "exfiltration", "privilege", "secret_exposure",
                                     "off_task", "violates_constraint", "unresolved_choice")}
    axes["authorized"] = _noul(0.9)
    axes["user_requested"] = _noul(0.9)
    idx = RISK_ORDER.index(operation)
    axes["operation"] = {"type": "score", "score": idx, "legend": {str(i): v for i, v in enumerate(RISK_ORDER)},
                         "probabilities": {str(i): float(i == idx) for i in range(len(RISK_ORDER))}}
    return axes


def _targets(**flags):
    base = {"created_this_session": False, "under_scratch": False, "tracked": None,
            "outside_workdir": False, "catastrophic": False}
    base.update(flags)
    return {"enabled": True, "cwd": "/work", "targets": [base]}


# ---- apply_ownership: the four branches (pure) ----

def test_apply_ownership_owned_sets_mutating_and_zeroes_destructive():
    axes = _clean_axes(operation="destructive")
    axes["destructive"] = _noul(0.9)
    out = apply_ownership(axes, _targets(under_scratch=True), POLICY)
    assert out["floor"] is None and out["reasons"] == ["owned_scratch_delete"]
    assert out["axes"]["destructive"]["noul"] == 0.0
    top = max(out["axes"]["operation"]["probabilities"], key=lambda k: out["axes"]["operation"]["probabilities"][k])
    assert out["axes"]["operation"]["legend"][top] == "mutating"


def test_apply_ownership_tracked_floors_ask_unsoftenable():
    out = apply_ownership(_clean_axes(), _targets(tracked=True), POLICY)
    assert out["floor"] == "ask" and out["unsoftenable"] is True
    assert out["reasons"] == ["delete_of_tracked_path"]


def test_apply_ownership_foreign_floors_deny():
    out = apply_ownership(_clean_axes(), _targets(outside_workdir=True), POLICY)
    assert out["floor"] == "deny" and out["reasons"] == ["delete_outside_workdir"]


def test_apply_ownership_unknown_leaves_axes_unchanged():
    # no cwd -> unknown; the model's answers stand
    unknown = {"enabled": True, "cwd": None, "targets": []}
    axes = _clean_axes()
    out = apply_ownership(axes, unknown, POLICY)
    assert out["floor"] is None and out["reasons"] == [] and out["axes"] is axes


def test_apply_ownership_tracked_beats_owned_when_mixed():
    mixed = {"enabled": True, "cwd": "/work", "targets": [
        {"created_this_session": True, "under_scratch": False, "tracked": False,
         "outside_workdir": False, "catastrophic": False},
        {"created_this_session": False, "under_scratch": False, "tracked": True,
         "outside_workdir": False, "catastrophic": False}]}
    out = apply_ownership(_clean_axes(), mixed, POLICY)
    assert out["floor"] == "ask" and out["reasons"] == ["delete_of_tracked_path"]


# ---- static_denial: exemption and the catastrophic rule (pure) ----

def test_recursive_rule_denies_without_ownership():
    out = static_denial("run_shell", {"command": "rm -rf build"}, POLICY, None)
    assert out and out["verdict"] == "deny" and out["rule"] == "recursive_force_remove"


def test_recursive_rule_exempt_for_owned_targets():
    owned = _targets(under_scratch=True)
    assert ownership.owned_targets(owned) is True
    out = static_denial("run_shell", {"command": "rm -rf build"}, POLICY, owned)
    assert out is None  # the owned recursive delete is judged contextually, not denied


def test_catastrophic_rule_denies_root_even_when_owned_claimed():
    # a catastrophic target is never exempt
    own = {"enabled": True, "cwd": "/work", "targets": [
        {"created_this_session": True, "under_scratch": True, "tracked": False,
         "outside_workdir": False, "catastrophic": True}]}
    assert ownership.owned_targets(own) is False
    out = static_denial("delete_file", {"path": "/"}, POLICY, own)
    assert out and out["rule"] == "catastrophic_remove"


@pytest.mark.parametrize("command", ["rm -rf /", "rm ~", "rm -rf $HOME", "rm -rf *", "rm -rf .."])
def test_catastrophic_regex_forms(command):
    out = static_denial("run_shell", {"command": command}, POLICY, None)
    assert out and out["rule"] == "catastrophic_remove"


# ---- ledger: created round trip ----

def test_ledger_created_round_trip(tmp_path):
    ledger = Ledger(tmp_path / "sessions")
    ledger.created("s1", ["/work/a", "/work/b", "/work/a"], tool_name="write_file")
    paths = ledger.created_paths("s1", POLICY)
    assert paths == {"/work/a", "/work/b"}
    # an operator clear drops them
    ledger.clear("s1", "operator")
    assert ledger.created_paths("s1", POLICY) == set()


# ---- facts over a real git repository ----

@pytest.fixture
def sandbox(tmp_path, monkeypatch):
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.email", "t@example.invalid"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.name", "t"], cwd=repo, check=True)
    (repo / "tracked.txt").write_text("tracked\n")
    subprocess.run(["git", "add", "tracked.txt"], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-qm", "init"], cwd=repo, check=True)
    (repo / "untracked.txt").write_text("untracked\n")
    scratch = tmp_path / "scratch"
    scratch.mkdir()
    (scratch / "junk.txt").write_text("junk\n")
    monkeypatch.setenv("FLUID_SCRATCH_DIRS", str(scratch))
    return {"repo": repo, "scratch": scratch, "tmp": tmp_path}


@gitonly
def test_facts_tracked_untracked_created_scratch_outside(sandbox):
    repo = sandbox["repo"]
    scratch = sandbox["scratch"]
    created = {str(Path(os.path.realpath(repo / "made.txt")))}
    (repo / "made.txt").write_text("x\n")

    def facts(path):
        own = ownership.compute("delete_file", {"path": str(path)}, SANDBOX_POLICY,
                                cwd=str(repo), created_paths=created)
        return own["targets"][0]

    assert facts(repo / "tracked.txt")["tracked"] is True
    assert facts(repo / "untracked.txt")["tracked"] is False
    assert facts(repo / "made.txt")["created_this_session"] is True
    assert facts(scratch / "junk.txt")["under_scratch"] is True
    assert facts(scratch / "junk.txt")["outside_workdir"] is True  # scratch is outside cwd
    outside = facts(sandbox["tmp"] / "elsewhere.txt")
    assert outside["outside_workdir"] is True and outside["created_this_session"] is False


@gitonly
def test_facts_symlink_and_parent_are_resolved(sandbox):
    repo = sandbox["repo"]
    # a symlink inside the repo pointing outside resolves to foreign
    link = repo / "link"
    os.symlink(str(sandbox["tmp"] / "outside_target"), str(link))
    own = ownership.compute("delete_file", {"path": str(link)}, SANDBOX_POLICY,
                            cwd=str(repo), created_paths=set())
    assert own["targets"][0]["outside_workdir"] is True
    # the parent of cwd is catastrophic
    parent = ownership.compute("run_shell", {"command": "rm -rf .."}, SANDBOX_POLICY,
                               cwd=str(repo), created_paths=set())
    assert parent["targets"][0]["catastrophic"] is True


def test_tmpdir_is_scratch():
    """The pack default includes the system temporary directory as a scratch root."""
    target = Path(tempfile.gettempdir()) / "fd-ip011-probe" / "x"
    own = ownership.compute("delete_file", {"path": str(target)}, POLICY,
                            cwd=str(Path(tempfile.gettempdir())), created_paths=set())
    assert own["targets"][0]["under_scratch"] is True


# ---- the twelve recorded fixtures (end to end, lane-free) ----

def _decide(tool, tool_input, cwd, created, axes):
    """Replicate the service's ownership path without the lane: static rules first,
    then apply_ownership over recorded axes, then evaluate_gate and the floor."""
    own = ownership.compute(tool, tool_input, SANDBOX_POLICY, cwd=cwd, created_paths=created)
    static = static_denial(tool, tool_input, SANDBOX_POLICY, own)
    if static:
        return static["verdict"], list(static["reasons"])
    applied = apply_ownership(axes, own, SANDBOX_POLICY)
    decision = evaluate_gate(applied["axes"], SANDBOX_POLICY)
    verdict = decision["verdict"]
    reasons = list(decision.get("reasons", [])) + list(applied["reasons"])
    if applied["floor"] == "deny" and verdict != "deny":
        verdict = "deny"
    elif applied["floor"] == "ask" and verdict == "allow":
        verdict = "ask"
    return verdict, reasons


@gitonly
def test_twelve_ownership_fixtures(sandbox):
    repo = sandbox["repo"]
    scratch = sandbox["scratch"]
    cwd = str(repo)
    (repo / "made").mkdir()
    created = {str(Path(os.path.realpath(repo / "made")))}
    high_destructive = _clean_axes(operation="destructive")
    high_destructive["destructive"] = _noul(0.9)

    scenarios = [
        ("owned_scratch_recursive", "run_shell", {"command": f"rm -rf {scratch}"}, set(), _clean_axes()),
        ("owned_created_recursive", "run_shell", {"command": f"rm -rf {repo / 'made'}"}, created, _clean_axes()),
        ("tracked_nonrecursive", "delete_file", {"path": str(repo / "tracked.txt")}, set(), _clean_axes()),
        ("tracked_recursive", "run_shell", {"command": f"rm -rf {repo / 'tracked.txt'}"}, set(), _clean_axes()),
        ("foreign_nonrecursive", "delete_file", {"path": str(sandbox["tmp"] / "elsewhere")}, set(), _clean_axes()),
        ("catastrophic_root", "run_shell", {"command": "rm -rf /"}, set(), _clean_axes()),
        ("catastrophic_home_tilde", "run_shell", {"command": "rm ~"}, set(), _clean_axes()),
        ("catastrophic_home_var", "run_shell", {"command": "rm -rf $HOME"}, set(), _clean_axes()),
        ("catastrophic_glob", "run_shell", {"command": "rm -rf *"}, set(), _clean_axes()),
        ("catastrophic_parent", "run_shell", {"command": "rm -rf .."}, set(), _clean_axes()),
        ("catastrophic_parent_via_tool", "delete_file", {"path": str(Path(repo).parent)}, set(), _clean_axes()),
        ("unknown_inside_untracked", "delete_file", {"path": str(repo / "untracked.txt")}, set(), high_destructive),
    ]
    expected = {
        "owned_scratch_recursive": "allow", "owned_created_recursive": "allow",
        "tracked_nonrecursive": "ask", "tracked_recursive": "deny",
        "foreign_nonrecursive": "deny", "catastrophic_root": "deny",
        "catastrophic_home_tilde": "deny", "catastrophic_home_var": "deny",
        "catastrophic_glob": "deny", "catastrophic_parent": "deny",
        "catastrophic_parent_via_tool": "deny", "unknown_inside_untracked": "ask",
    }
    results = {}
    for name, tool, tool_input, created_set, axes in scenarios:
        verdict, reasons = _decide(tool, tool_input, cwd, created_set, axes)
        results[name] = {"verdict": verdict, "reasons": reasons}
        assert verdict == expected[name], f"{name}: got {verdict} ({reasons}), want {expected[name]}"
    # key reasons present
    assert "owned_scratch_delete" in results["owned_scratch_recursive"]["reasons"]
    assert "delete_of_tracked_path" in results["tracked_nonrecursive"]["reasons"]
    assert "delete_outside_workdir" in results["foreign_nonrecursive"]["reasons"]
    assert "catastrophic removal" in results["catastrophic_root"]["reasons"]
    # The committed docs/ownership-fixtures.json must match these verdicts exactly.
    recorded = json.loads((Path(__file__).resolve().parents[1] / "docs" / "ownership-fixtures.json")
                          .read_text())["fixtures"]
    assert {k: v["verdict"] for k, v in recorded.items()} == expected


def test_no_cwd_never_exempts_a_recursive_delete_under_scratch(tmp_path, monkeypatch):
    # Post-release fix: with no working directory the exemption must not apply,
    # even when the target sits under a scratch root (on Linux the system temp
    # directory is /tmp, so `rm -rf /tmp/x` with no cwd was being exempted).
    scratch = tmp_path / "scratch"
    scratch.mkdir()
    monkeypatch.setenv("FLUID_SCRATCH_DIRS", str(scratch))
    tool_input = {"command": f"rm -rf {scratch / 'x'}"}
    without_cwd = ownership.compute("run_shell", tool_input, SANDBOX_POLICY, cwd=None, created_paths=set())
    assert without_cwd["targets"][0]["under_scratch"] is True
    assert ownership.owned_targets(without_cwd) is False
    denied = static_denial("run_shell", tool_input, SANDBOX_POLICY, without_cwd)
    assert denied and denied["rule"] == "recursive_force_remove"
    # with a cwd the same scratch target is owned and exempt
    with_cwd = ownership.compute("run_shell", tool_input, SANDBOX_POLICY, cwd=str(tmp_path), created_paths=set())
    assert ownership.owned_targets(with_cwd) is True
    assert static_denial("run_shell", tool_input, SANDBOX_POLICY, with_cwd) is None
