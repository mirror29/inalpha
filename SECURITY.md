# Security Policy / 安全政策

> Inalpha is in **alpha** and **not recommended for use with real trading capital**.
> Security reports are still very welcome — especially on LLM tool-permission boundaries, secret handling, and the order-submission path.
>
> Inalpha 处于 **alpha** 阶段，**不建议接入真实交易资金**。
> 我们非常欢迎安全报告——尤其是 LLM tool 权限边界、密钥处理、下单链路相关的问题。

## Reporting a vulnerability / 报告漏洞

**Please do not open a public GitHub issue** for security vulnerabilities.
**请不要**为安全漏洞开 public GitHub issue。

Contact the maintainer privately through one of the following channels / 请通过以下任一渠道私下联系维护者：

- Email: the public address on the repository's GitHub profile
  Email：仓库 GitHub profile 上的公开邮箱
- GitHub Security Advisory: `Repository → Security → Report a vulnerability`

When you report, please include as much of the following as you can / 报告时请尽量包含：

1. **Vulnerability class** — e.g. LLM tool-permission bypass, secret leak, unauthorized order submission, dependency supply-chain issue
   **漏洞类型**——如 LLM tool 权限绕过、密钥泄漏、未授权下单、依赖供应链问题
2. **Affected service / module** / **受影响的 service / 模块**
3. **Minimal reproduction steps** / **最小复现步骤**
4. **Your assessment of the potential impact** / **你对潜在影响的评估**

Reports in either English or 中文 are equally welcome.
中英文报告同样欢迎。

## Response commitments / 响应承诺

- **First response** / **首次响应**: within 5 business days / 5 个工作日内
- **Fix or mitigation** / **修复或缓解**: 1–30 days depending on severity / 视严重程度 1–30 天
- **Disclosure** / **披露**: documented in release notes once the fix lands; your identity is kept private unless you explicitly ask otherwise
  修复落地后写入 release notes；除非你明确要求，否则不公开你的身份

## Supported versions / 受支持版本

| Version / 版本 | Accepts security reports / 接受安全报告 |
|---|---|
| `main` / unreleased / 未发布 | ✅ |
| Latest published alpha tag / 最新 alpha tag（e.g. `v0.1.0-alpha`） | ✅ (latest alpha tag only / 仅最新) |
| Older tags / 更早的 tag | ❌ |

## Scope / 范围

**In scope** / **在范围内**

- Logic flaws that give an LLM a direct order-placement path (breaks the core safety model)
  让 LLM 获得直接下单路径的逻辑缺陷（破坏核心安全模型）
- Bypasses of `permissions` / `hooks` middleware
  绕过 `permissions` / `hooks` 中间件
- Mishandling of secrets or tokens
  密钥或 token 处理不当
- Any service-to-service privilege escalation
  任何 service 间的权限提升
- Dependency supply-chain issues (known CVEs in pinned packages, etc.)
  依赖供应链问题（pinned 包的已知 CVE 等）

**Out of scope** / **不在范围内**

- Local misconfiguration (e.g. committing your `.env` to a public repository)
  本地配置失误（如把自己的 `.env` 提交到公开仓库）
- Zero-days in third-party dependencies themselves — please report those upstream
  第三方依赖自身的 0-day——请报给上游
- Trading strategy P&L performance (that's a strategy problem, not a security issue)
  交易策略盈亏表现（那是策略问题，不是安全问题）

Thank you for disclosing responsibly.
感谢负责任地披露。
