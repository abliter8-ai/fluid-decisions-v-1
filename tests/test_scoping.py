import json

import pytest

from fluid.scoping import (
    build_requests,
    group_questions,
    merge_results,
    resolve_scope,
    strip_scopes,
)


def test_load_pack_rejects_malformed_scope(tmp_path, monkeypatch):
    import fluid.policy as policy
    pack_dir = tmp_path / "packs"
    pack_dir.mkdir()
    (pack_dir / "bad.yaml").write_text(
        "version: '0.1.0'\nquestions:\n  q: {type: noul, instructions: x, scope: []}\npolicy: {}\n",
        encoding="utf-8")
    monkeypatch.setattr(policy, "PACK_DIR", pack_dir)
    with pytest.raises(ValueError, match="scope"):
        policy.load_pack("bad")

    (pack_dir / "bad.yaml").write_text(
        "version: '0.1.0'\nquestions:\n  q: {type: noul, instructions: x, scope: null}\npolicy: {}\n",
        encoding="utf-8")
    with pytest.raises(ValueError, match="scope"):
        policy.load_pack("bad")


def test_resolve_scope_preserves_nested_structure_and_skips_missing():
    state = {"tool_name": "read", "tool_input": {"command": "cat", "path": "x"}, "extra": 1}
    assert resolve_scope(state, ["tool_name", "tool_input.command"]) == {
        "tool_name": "read", "tool_input": {"command": "cat"}}
    assert resolve_scope(state, ["missing"]) is None
    assert resolve_scope("text", ["tool_name"]) is None


def test_build_requests_groups_deterministically_and_strips_scope():
    state = {"a": 1, "b": {"c": 2}}
    questions = {
        "scoped_first": {"type": "noul", "scope": ["b"]},
        "whole": {"type": "noul"},
        "scoped_second": {"type": "noul", "scope": ["b"]},
    }
    groups = build_requests(state, questions)
    assert [group["scope"] for group in groups] == [None, ["b"]]
    assert list(groups[0]["questions"]) == ["whole"]
    assert list(groups[1]["questions"]) == ["scoped_first", "scoped_second"]
    assert "scope" not in groups[1]["questions"]["scoped_first"]
    assert "scope" not in strip_scopes(questions)["scoped_first"]


def test_group_questions_is_whole_state_first_even_when_seen_later():
    groups = group_questions({
        "scoped": {"type": "noul", "scope": ["b"]},
        "whole": {"type": "noul"},
        "same": {"type": "noul", "scope": ["b"]},
    })
    assert [(group["scope"], group["names"]) for group in groups] == [
        (None, ["whole"]), (("b",), ["scoped", "same"])
    ]


def test_unresolved_group_is_reported_without_result_or_state_content():
    groups = build_requests({"present": 1}, {"axis": {"type": "noul", "scope": ["absent"]}})
    merged = merge_results(groups, [])
    assert merged["answers"] == {}
    assert merged["scoping"]["unresolved"] == ["axis"]
    assert "present" not in json.dumps(merged)


def test_merge_results_sums_tokens_and_rejects_collisions():
    groups = build_requests({"a": 1, "b": 2}, {
        "one": {"type": "noul", "scope": ["a"]},
        "two": {"type": "noul", "scope": ["b"]},
    })
    result = merge_results(groups, [
        {"answers": {"one": {"type": "noul", "noul": .2}}, "usage": {"input_tokens": 3}, "lane": "max"},
        {"answers": {"two": {"type": "noul", "noul": .8}}, "usage": {"input_tokens": 4}, "lane": "max"},
    ])
    assert set(result["answers"]) == {"one", "two"}
    assert result["usage"]["input_tokens"] == 7
    with pytest.raises(ValueError, match="duplicate"):
        merge_results(groups, [
            {"answers": {"one": {}}, "usage": {}},
            {"answers": {"one": {}}, "usage": {}},
        ])


def test_media_forces_one_whole_state_group():
    groups = build_requests({"a": 1}, {"q": {"type": "noul", "scope": ["a"]}}, images=["x"])
    assert len(groups) == 1 and groups[0]["scope"] is None
