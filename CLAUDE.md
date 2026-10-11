# Fluid Decisions

Read [AGENTS.md](AGENTS.md) for the project instructions and scope. It is the shared source of truth for implementing agents, including the community self-hosting requirement.

## Privacy — applies even in a private repository

Never put the maintainer's name, personal aliases, usernames, email addresses, hostnames, device names, private network addresses or domains, account identifiers, or identifying filesystem paths into code, documentation or other repository artifacts. This includes agent instructions, examples, fixtures, generated logs, reports, screenshots and metadata. Local access and existing documents do not authorize disclosure.

Use neutral labels, repository-relative paths, environment-variable references and generic placeholders. Keep real endpoints, paths and raw captures in ignored local configuration or outside the repository. Sanitize nested tool output before saving evidence; preserve measurements and label redactions without recording a mapping to real identities.

Before committing or publishing, review added lines, complete new artifacts, commit messages and publication metadata for personal and infrastructure identifiers as well as credentials. Fix disclosures before proceeding; a secret-only scan is insufficient. Follow the full [AGENTS.md privacy rule](AGENTS.md#privacy--required-for-every-repository-artifact). Do not claim existing files or Git history have been sanitized merely because these instructions changed.
