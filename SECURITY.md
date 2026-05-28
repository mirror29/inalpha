# Security Policy

> Inalpha is in **alpha** and **not recommended for use with real trading capital**.
> Security reports are still very welcome — especially on LLM tool-permission boundaries, secret handling, and the order-submission path.

## Reporting a vulnerability

**Please do not open a public GitHub issue** for security vulnerabilities.

Contact the maintainer privately through one of the following channels:

- Email: the public address on the repository's GitHub profile
- GitHub Security Advisory: `Repository → Security → Report a vulnerability`

When you report, please include as much of the following as you can:

1. **Vulnerability class** — e.g. LLM tool-permission bypass, secret leak, unauthorized order submission, dependency supply-chain issue
2. **Affected service / module**
3. **Minimal reproduction steps**
4. **Your assessment of the potential impact**

Reports in either English or 中文 are equally welcome.

## Response commitments

- **First response**: within 5 business days
- **Fix or mitigation**: 1–30 days depending on severity
- **Disclosure**: documented in release notes once the fix lands; your identity is kept private unless you explicitly ask otherwise

## Supported versions

| Version | Accepts security reports |
|---|---|
| `main` / unreleased | ✅ |
| Latest published alpha tag (e.g. `v0.1.0-alpha`) | ✅ (latest alpha tag only) |
| Older tags | ❌ |

## Scope

**In scope**

- Logic flaws that give an LLM a direct order-placement path (breaks the core safety model)
- Bypasses of `permissions` / `hooks` middleware
- Mishandling of secrets or tokens
- Any service-to-service privilege escalation
- Dependency supply-chain issues (known CVEs in pinned packages, etc.)

**Out of scope**

- Local misconfiguration (e.g. committing your `.env` to a public repository)
- Zero-days in third-party dependencies themselves — please report those upstream
- Trading strategy P&L performance (that's a strategy problem, not a security issue)

Thank you for disclosing responsibly.
