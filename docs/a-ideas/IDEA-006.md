---
id: IDEA-006
title: "Contained execution: gate verdicts and session taint select an MXC containment profile, on macOS, Linux and Windows"
status: idea
created: 2026-10-11
updated: 2026-10-11
depends_on: IP-001 (GuardedRuntime takes the executor), IP-007 (proportionate taint, action levels), IP-008 (why AgentDojo cannot measure this)
source: "Upstream Microsoft eXecution Container (MXC) v1.0.0: repository README, backend guides (Seatbelt, Bubblewrap, LXC, Nanvix, Hyperlight, ProcessContainer, Windows Sandbox, WSLC), schema and access-denied logging docs, and the v1.0.0 release asset listing, all read 2026-10-11; the Claude Code sandboxing documentation read the same day. Written by the planning-and-verification session. No MXC binary has been run."
---

# IDEA-006 — Contained execution

Status: idea with grounded facts, no measurements. Not a plan. Nothing here is implemented, and no MXC executor has been run on any host. Every platform statement below comes from upstream documentation or the release listing, not from a test.

## The idea in one paragraph

The guard decides *whether* an action runs. Nothing today bounds *what* an allowed action can do once it runs. The gate is probabilistic and its lines are calibrated against a false-flag budget, so some wrong allows are expected by design, and the README already says an allow is not proof of safety. MXC is an OS-level sandbox SDK (filesystem, network and UI policy, kernel-enforced, with Seatbelt, Bubblewrap, ProcessContainer and VM backends behind one JSON request). The idea: policy code maps the gate verdict, the action level and the session taint to a named **containment profile**, and the executor runs the tool inside that profile. The model still never decides the action. Policy picks the profile, the OS enforces it, and a wrong allow is bounded by the profile instead of by luck.

## Design rules

- **Containment bounds a decision; it never replaces one.** Static denials still run first and still run with the lane down. A lane-down, incomplete, secret-exposure, constraint-violation or unresolved-choice ask stays an ask. Containment is not a reason to move a line: thresholds stay where calibration put them.
- **Taint closes the exfiltration channel.** An injection needs two things: an instruction the worker follows and a way out. While a session holds a flagged or pending result (IP-007), the next executions run with egress denied, whatever d1 says about `carries_out`. This holds even when d1 misses the injection, which is the case the guard cannot otherwise cover.
- **A contained run may replace an ask only where containment actually contains the risk.** A risk-band ask on a read-only action, or on a mutating action confined to the workspace, can run no-egress instead of stopping the worker. A destructive action inside the workspace cannot: the sandbox allows writes to the workspace, so `rm` on project files is not contained. Destructive actions keep today's verdicts. This substitution is a policy switch, off by default.
- **Fail closed on the backend.** If the requested backend is unavailable (probe fails, prerequisite missing, executor not configured), the verdict is ask. Never fall back to an uncontained run. Upstream already refuses rather than degrades (Bubblewrap: "neither ever falls back to sharing the host network namespace"), and the guard must not undo that one layer up.
- **Profiles live in policy YAML.** Python holds the mapping code, not the mapping. The profile table is versioned with the pack and marked uncalibrated.
- **Upstream, unmodified, pinned.** MXC has merged several commits a day since its 1.0.0 release (crates.io and npm 2026-10-06, GitHub release 2026-10-07). A fork exists under the project's organisation with no changes of its own; this idea does not use it. A fork is justified only by an engine change upstream will not take.

## Profiles (platform-neutral names)

| Profile | Filesystem | Network | Used when |
| --- | --- | --- | --- |
| `workspace` | Read-write: the task's working directory. Read-only: toolchain paths. Denied: the gate's `secret_paths` (`.env`, keys, `.netrc`, credentials) | Egress allowed | Allow, no taint |
| `no-egress` | As `workspace` | Egress denied, ingress denied, host loopback denied | Allow with taint; opt-in replacement for a risk-band ask on a read-only or workspace-mutating action |
| `proxy-only` | As `workspace` | Egress only to a local domain-allowlisting proxy | Allow for network tools (package install, fetch) where the host list is policy |
| `vm` | Guest filesystem only | Denied | Running model-written code, not shell commands (see the Linux and Windows VM notes) |

`deny` stays deny. A hard ask stays ask. The profile chosen, the backend, its version and the probe result join the audit record next to the verdict, so a later calibration pass can ask whether contained runs ever needed the containment.

## The three platform paths

### macOS — Seatbelt

| Item | Upstream fact |
| --- | --- |
| Backend | `seatbelt`, the only macOS backend. Stable. |
| Executor | `mxc-exec-mac`. The v1.0.0 release archive carries it for **arm64 only**; an Intel Mac builds from source (`build-mac.sh`, Rust 1.93). |
| Requires | macOS 15 or later. No root, no daemon, no install. |
| Isolation | The process tree. A Seatbelt profile generated from the JSON policy, applied with `sandbox_init()` between `fork()` and `exec()`, passed as a string with no temporary files. |
| Filesystem | `readonlyPaths`, `readwritePaths`, `deniedPaths`. Denied entries are emitted last and override every allow. |
| Network | Egress on or off, plus a loopback exception. **No hostname, IP, CIDR or port rules**; requests for them are rejected. Proxy-only egress is kernel-enforced (the sandbox can reach nothing but the proxy), but a client's use of the proxy is cooperative: a client that ignores `HTTP_PROXY` fails rather than escapes. |
| Denial records | No structured denied-access log from MXC on macOS. `--debug` prints the generated profile. Whether kernel sandbox denials are readable from the unified log for this purpose is unverified. |
| Profiles | `workspace`, `no-egress`, `proxy-only`. No `vm`. |

Two traps from the guide. `ingress.hostLoopback: "allow"` opens the host's loopback services to the sandbox, which matters on a host that serves local models; keep it deny. And proxy-only with `egress.default: "allow"` is rejected, so a proxy profile is always a deny-default profile.

### Linux — Bubblewrap by default, VM tiers experimental

| Item | Upstream fact |
| --- | --- |
| Backend | `bubblewrap`, the stable default. `lxc`, `microvm` (Nanvix) and `hyperlight` are alternatives. |
| Executor | `lxc-exec` for every Linux backend, in the release archive for x64 and arm64. Takes `--config <file>` or `--config-base64`. |
| Requires | bwrap 0.5.0 or newer and user namespaces. No root. The ruleless `no-egress` profile needs nothing else (`--unshare-net`). Any network-enabled mode (address rules, open egress, proxy-only) also needs `slirp4netns`, util-linux `unshare` with `--map-current-user` and `--keep-caps`, `nsenter`, `iptables`/`ip6tables` on the **nf_tables** backend, and the `nf_conntrack` module loaded. |
| Filesystem | Deny-by-default baseline. A denied directory is masked with an empty tmpfs and a denied file with `/dev/null`, so the workload sees an empty path rather than a permission error. |
| Network | Egress rules by **IP literal or CIDR only**; DNS names are rejected. Proxy-only egress is enforced. IPv6 is filtered but has no route: an IPv6 destination is unreachable even when allowed. |
| Denial records | None structured. Masking produces no denial event; a blocked connection surfaces as the workload's own error, which the post-result check already sees in the output. |
| Profiles | `workspace`, `no-egress`, `proxy-only`, and an extra `cidr-allow` if a fixed address list is ever wanted. |

VM tiers on Linux are both experimental and need `--experimental`. **Nanvix** needs KVM (`/dev/kvm` read-write for the invoking user), a build with `--with-microvm`, and staged runtime images; it cold-boots per run on Linux (warm snapshots are Windows-only). **Hyperlight** needs x86_64 with KVM, a `--with-hyperlight` build and schema `1.1.0-alpha`, and its `commandLine` is source code for a guest runtime (Python, Node and others), not a shell command. Both fit a "run this model-written script" tool, not general tool calls, so `vm` is a profile for code execution only. Hyperlight is unavailable on arm64 hosts. **LXC** needs root to filter egress or ingress and builds a distribution rootfs; it adds nothing the profiles need over Bubblewrap.

### Windows — ProcessContainer by default

| Item | Upstream fact |
| --- | --- |
| Backend | `processcontainer`, the default. `wslc` (Linux containers through WSL) is stable. `windows_sandbox`, `microvm` and `hyperlight` are experimental. |
| Executor | `wxc-exec.exe`, in the release archive for x64 and arm64. It never elevates itself. |
| Requires | Windows 11 at the process-isolation floors in upstream's OS-version table (24H2 from build 26100.9278, 25H2 from 26200.9278, 26H1 from 28000.2804). One-time host setup through `wxc-host-prep.exe` (administrator, one UAC prompt): `prepare-system-drive` and `prepare-null-device`. |
| Filesystem | AppContainer-based read-only, read-write and denied paths. |
| Network | The richest of the three. Per-container WFP filters allow or block by IP address or range, protocol and port, IPv4 and IPv6, with block outranking allow. Proxy-only egress (upstream's recommended "Model 2") points WinHTTP clients and the proxy environment variables at a loopback proxy, and WFP permits the container to reach only that endpoint. Quirk: private-network access is one bidirectional AppContainer capability, so allowing private-network egress also needs `ingress.default: "allow"`. |
| Denial records | **The only platform with structured denied-access events.** `captureDenials` with mode `block` (deny-and-record, the `learningModeLogging` capability) writes a JSON file of blocked accesses while enforcement stays on. `--audit` and `permissiveLearningMode` turn containment off and must never run an untrusted workload. |
| Profiles | `workspace`, `no-egress`, `proxy-only`. `vm` through Windows Sandbox (a disposable VM per run, experimental, best-effort teardown) or Nanvix and Hyperlight on WHP (experimental). |

Windows is also where MXC fills a gap rather than duplicating one: Claude Code's built-in sandbox covers macOS, Linux and WSL2, not native Windows.

## Claude Code workers: use the native sandbox, gate its bypass

Claude Code already sandboxes its Bash tool: Seatbelt on macOS, bubblewrap and socat on Linux and WSL2, with egress through a local proxy that checks each host against an allowed-domains list, and filesystem allow and deny settings (open source as `sandbox-runtime`). That proxy gives hostname filtering, which MXC's Seatbelt backend cannot. Do not nest MXC under it. The guard's job on this path is the bypass: the Bash tool accepts a `dangerouslyDisableSandbox` request, and the hook can deny that request while the session carries taint and ask for it otherwise. MXC is for the workers the guard wraps itself: `GuardedRuntime` callers, MCP-only workers and the local Qwen and GLM harness loops.

## Integration from Python

MXC ships Rust, .NET and Node SDKs and no Python SDK. In order of preference:

1. **The executor binary with a stable request.** Upstream documents executor use with the stable `1.0.0` JSON schema for callers that cannot embed the SDK, which is this project's case. The executor is a pinned path from the environment (for example `FD_MXC_EXEC`) or ignored local configuration, never a repository default. `--dry-run` validates a request without executing, so a startup probe can confirm the backend before the first gated call, alongside the existing lane warm-up.
2. **A Node sidecar on `@microsoft/mxc-sdk` 1.0.0**, only if streaming, PTY or persistent-container lifecycle is needed. It adds a runtime and a process boundary, and its platform probe caches failures for the process lifetime.
3. **Rejected: `ctypes` over `libmxc_ffi`.** The library is in the release archive, but it is the internal surface behind the Node and .NET SDKs, not a documented API.

The release archive is 536 MB (all platforms, both architectures, debug symbols). Record its checksum when it is pinned. Telemetry is off unless a run opts in, and it is a no-op outside Windows.

In code, `GuardedRuntime.run` already takes the executor as a callable (`fluid/service.py`), so a contained executor wraps it without touching the gate. The gate decision gains a `containment` field (profile, backend, pack version); the hook path for Claude Code is the bypass rule above.

## How to measure it

AgentDojo cannot. Its tools are Python functions over a simulated environment, so there is no process to contain, and CR-008's slice had no attack headroom besides. The measurement needs a harness that executes real commands in a scratch workspace, a canary secret, and a local sink that records whether the canary arrived (on the same host, never a fleet service). Whether the IP-005 harness can be pointed at real execution is to be checked. Arms: undefended, guard, guard plus containment, containment alone. Worker: the local Qwen and GLM models, which give attack headroom the cloud model did not. Read per arm: canary exfiltration, task utility, ask count (containment should lower it where the risk-band substitution is on), and per-call latency added by each backend's spawn.

## Decisions this would need from the maintainer

1. Contain every execution (`workspace` as the floor), or only tainted and risk-band executions. The first changes behaviour for every allowed call.
2. Whether a risk-band ask may ever become a `no-egress` run, under the destructive-action exclusion above.
3. The proxy for `proxy-only`: reuse `sandbox-runtime`'s proxy, or a small allowlisting proxy kept in this repository.
4. Platform order. macOS first is the cheapest start (no install, no root) and has the narrowest network policy; Linux adds address rules and the VM tier; Windows adds structured denial records for calibration.

## Bad bet if

- A line is lowered "because it runs sandboxed". Containment bounds the cost of a wrong allow; it does not make the allow right.
- A risk-band ask on a destructive in-workspace action is converted to a contained run. The sandbox lets it write the workspace.
- A missing backend falls back to an uncontained run.
- The guard nests MXC inside Claude Code's sandbox instead of gating that sandbox's bypass.
- The project carries a fork of MXC for a policy layer that belongs in this repository's YAML.
- `--audit` or permissive Learning Mode is used to tune a profile against an untrusted workload.
- Denial events are treated as calibration ground truth on macOS or Linux, where MXC does not produce them.

## References

- Microsoft eXecution Container (MXC): https://github.com/microsoft/mxc (v1.0.0 release, 2026-10-07; `mxc-sdk` on crates.io and `@microsoft/mxc-sdk` on npm, 1.0.0, 2026-10-06)
- MXC backend guides: `docs/backends/` in the upstream repository (Seatbelt, Bubblewrap, LXC, Nanvix, Hyperlight, ProcessContainer networking and OS-version support, host preparation, Windows Sandbox, WSLC)
- MXC configuration schema (stable 1.0.0, dev 1.1.0-alpha) and access-denied logging: `docs/schema.md`, `docs/logging-access-denied.md` in the upstream repository
- Claude Code, "Configure the sandboxed Bash tool": https://code.claude.com/docs/en/sandboxing
- Debenedetti et al., "Defeating Prompt Injections by Design" (CaMeL), capability restriction under untrusted data: https://arxiv.org/abs/2503.18813
- Debenedetti et al., "AgentDojo": https://arxiv.org/abs/2406.13352
