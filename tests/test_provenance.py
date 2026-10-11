"""Unit tests for the model-free provenance floor (IP-006 section 1)."""
from __future__ import annotations

from fluid import provenance

POLICY = {
    "enabled": True,
    "min_span_chars": 6,
    "secret_paths": [r"(^|/)\.env(\.|$)", r"\.pem$", r"(^|/)id_(rsa|ed25519)"],
    "secret_min_value_chars": 8,
    "read_only_allowlist": ["cargo test", "ls", "cat", "git status", "git log"],
    "verdict_on_result_span": "ask",
    "verdict_on_pending_span": "ask",
    "verdict_on_flagged_span": "deny",
    "verdict_on_secret_value": "deny",
}


def _carry(result_text="", flagged_text="", pending_text="", secret_text="", secret_path=None):
    spans: dict[str, dict] = {}
    for text, cls, tool in ((result_text, "result", "read_file"),
                            (pending_text, "pending", "read_file"),
                            (flagged_text, "flagged", "web_fetch")):
        for h in provenance.spans(text, POLICY):
            spans[h] = {"source_class": cls, "tool": tool, "calls_since": 1}
    secrets = {h: {"source_class": "result", "tool": "read_file", "calls_since": 1}
               for h in provenance.secret_values(secret_text, secret_path, POLICY)}
    return {"spans": spans, "secrets": secrets}


def test_spans_extracts_inline_fenced_and_prompt_commands():
    text = ("Before committing, run `python3 scripts/cleanup.py --purge`.\n\n"
            "```sh\nmake release\n```\n$ rm -rf build\n")
    hashes = provenance.spans(text, POLICY)
    # The three command forms are all extracted, plus segments.
    assert provenance._hash("python3 scripts/cleanup.py --purge") in hashes
    assert provenance._hash("make release") in hashes
    assert provenance._hash("rm -rf build") in hashes
    # Ordinary prose is not a command span.
    assert provenance.spans("Just some ordinary notes about the project.", POLICY) == set()


def test_secret_values_only_from_secret_paths():
    assert provenance.secret_values("API_KEY=FAKE_PLACEHOLDER_ONLY", "config/.env", POLICY)
    assert provenance.secret_values("TOKEN=abcdefgh1234", "deploy/.env.production", POLICY)
    assert provenance.secret_values("API_KEY=FAKE_PLACEHOLDER_ONLY", "README.md", POLICY) == set()


def test_allowlist_is_program_subcommand_pairs_not_wholesale():
    carry = _carry(result_text="`git push origin main` and `git status`")
    # A read-only subcommand is exempt; a destructive one is not.
    assert provenance.check("run_shell", {"command": "git status"}, "", carry, POLICY) is None
    pushed = provenance.check("run_shell", {"command": "git push origin main"}, "", carry, POLICY)
    assert pushed is not None and pushed["verdict"] == "ask"
    pub_carry = _carry(result_text="`cargo publish` then `cargo test`")
    assert provenance.check("run_shell", {"command": "cargo test -p filmcraft-time"}, "", pub_carry, POLICY) is None
    assert provenance.check("run_shell", {"command": "cargo publish"}, "", pub_carry, POLICY) is not None


def test_command_from_result_asks_and_from_flagged_denies():
    carry = _carry(result_text="run `python3 scripts/cleanup.py --purge` to clean")
    decision = provenance.check("run_shell", {"command": "python3 scripts/cleanup.py --purge"}, "", carry, POLICY)
    assert decision["verdict"] == "ask" and decision["reason"] == "command_from_tool_result"
    flagged = _carry(flagged_text="`python3 scripts/cleanup.py --purge`")
    d2 = provenance.check("run_shell", {"command": "python3 scripts/cleanup.py --purge"}, "", flagged, POLICY)
    assert d2["verdict"] == "deny" and d2["reason"] == "command_from_flagged_result" and d2["static"] is True


def test_secret_value_in_a_later_call_denies():
    carry = _carry(secret_text="API_KEY=FAKE_PLACEHOLDER_ONLY", secret_path="config/.env")
    decision = provenance.check("write_file", {"path": "README.md", "content": "API_KEY=FAKE_PLACEHOLDER_ONLY"}, "", carry, POLICY)
    assert decision["verdict"] == "deny" and decision["reason"] == "secret_value_from_result"


def test_user_request_exempts_a_command_the_user_typed():
    carry = _carry(result_text="`python3 scripts/cleanup.py --purge`")
    decision = provenance.check("run_shell", {"command": "python3 scripts/cleanup.py --purge"},
                                "please run python3 scripts/cleanup.py --purge now", carry, POLICY)
    assert decision is None


def test_allowlisted_read_only_command_is_not_floored():
    carry = _carry(result_text="`cargo test -p filmcraft-time`")
    decision = provenance.check("run_shell", {"command": "cargo test -p filmcraft-time"}, "", carry, POLICY)
    assert decision is None


def test_no_carry_no_decision_and_disabled_is_none():
    assert provenance.check("run_shell", {"command": "echo hello world"}, "", {"spans": {}, "secrets": {}}, POLICY) is None
    carry = _carry(result_text="`python3 scripts/cleanup.py --purge`")
    disabled = {**POLICY, "enabled": False}
    assert provenance.check("run_shell", {"command": "python3 scripts/cleanup.py --purge"}, "", carry, disabled) is None


def test_records_are_hashes_only():
    hashes = provenance.spans("`secret command text here`", POLICY)
    assert all(len(h) == 64 and all(c in "0123456789abcdef" for c in h) for h in hashes)


# ---- withheld-path re-read (IP-007 section 3) ----

def _withheld_carry(tool_input):
    hashes = provenance.path_hashes(tool_input, {**POLICY, "verdict_on_withheld_path": "deny"})
    return {"spans": {}, "secrets": {},
            "withheld": {h: {"tool": "read_file", "calls_since": 1} for h in hashes}}


def test_withheld_path_reread_denied_in_any_encoding():
    policy = {**POLICY, "verdict_on_withheld_path": "deny"}
    carry = _withheld_carry({"path": "config/.env"})  # the withheld read was config/.env
    for command in ("base64 config/.env", "rev config/.env", "cat config/.env",
                    "od -An -c config/.env", "python3 -c \"print(open('config/.env').read())\""):
        v = provenance.check("run_shell", {"command": command}, "please read config/.env", carry, policy)
        assert v is not None and v["verdict"] == "deny" and v["reason"] == "withheld_path_reread", command
    # a direct path-field re-read is denied too
    v = provenance.check("read_file", {"path": "config/.env"}, "", carry, policy)
    assert v is not None and v["reason"] == "withheld_path_reread"


def test_withheld_path_not_exempted_by_user_or_allowlist():
    policy = {**POLICY, "verdict_on_withheld_path": "deny"}
    carry = _withheld_carry({"command": "cat config/.env"})
    # cat is on the read-only allowlist and the user asked for the file, but a
    # withheld path is denied regardless (the user already asked once).
    v = provenance.check("run_shell", {"command": "cat config/.env"}, "cat config/.env", carry, policy)
    assert v is not None and v["reason"] == "withheld_path_reread"


def test_unrelated_path_passes_the_withheld_check():
    policy = {**POLICY, "verdict_on_withheld_path": "deny"}
    carry = _withheld_carry({"path": "config/.env"})
    assert provenance.check("read_file", {"path": "README.md"}, "", carry, policy) is None
