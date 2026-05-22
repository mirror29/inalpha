# 安全策略 / Security Policy

> Inalpha 处于 alpha 阶段，**不建议用于真实交易资金**。
> 仍欢迎你报告任何安全相关问题——尤其是涉及 LLM 工具权限边界、密钥处理、订单提交链路的。

## 报告漏洞

**请不要**在 GitHub 上开 public issue 来报告安全漏洞。

请通过以下任一渠道私下联系维护者：

- 邮件：（在仓库 GitHub profile 的公开邮箱）
- GitHub Security Advisory：`Repository → Security → Report a vulnerability`

报告内容请尽量包含：

1. 漏洞类型（如：LLM 工具权限绕过、密钥泄露、订单越权提交、依赖供应链问题）
2. 受影响 service / 模块
3. 最小复现步骤
4. 你认为的潜在影响

## 响应承诺

- **首次响应**：5 个工作日内
- **修复或缓解方案**：根据严重程度，1–30 天
- **披露**：修复后会在 release notes 中说明（不会公开你的身份，除非你明确希望）

## 受支持的版本

| 版本 | 是否接受安全报告 |
|---|---|
| `main` / unreleased | ✅ |
| 已发布的 alpha tag（如 `v0.1.0-alpha`） | ✅（只针对最新一个 alpha tag） |
| 更早的版本 | ❌ |

## 范围说明

**在范围内的**：

- 让 LLM 获得直接下单路径的逻辑漏洞（破坏核心安全模型）
- `permissions` / `hooks` 绕过
- 密钥 / token 处理不当
- 任何 service-to-service 越权
- 依赖供应链问题（已知 CVE 等）

**不在范围内的**：

- 用户在本地配置错误导致的问题（如把 `.env` commit 到 public repo）
- 第三方依赖本身的 0day（请直接上报到对应项目）
- 量化策略本身的盈亏表现（这是策略问题，不是安全问题）

感谢负责任地披露安全问题。
