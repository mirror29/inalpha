import type { SupportedLocale } from "./seo";

export const SCHEMA_COPY: Record<
  SupportedLocale,
  {
    organizationDescription: string;
    websiteDescription: string;
    sourceDescription: string;
  }
> = {
  en: {
    organizationDescription:
      "Open-source quant agent framework — an oracle that keeps a ledger. Agents pick the factors that work now (time-series Rank IC), convene a panel of investing legends, write and evolve strategy code, and route every order through machine approval. One strategy codebase across backtest, paper, and the live runner, multi-market routing, Claude Code-style hooks/permissions/plan-exec.",
    websiteDescription:
      "Open-source quant agent framework — an oracle that keeps a ledger. Factor timing by Rank IC, a panel of investing legends, LLM-written self-evolving strategies, and machine-approved orders.",
    sourceDescription:
      "Open-source quant agent framework. Agents pick the factors that work now via time-series Rank IC, convene a panel of investing legends for opposing research, write and evolve strategy code, and route every order through machine approval.",
  },
  zh: {
    organizationDescription:
      "开源量化 agent 框架——一个会记账的神谕。Agent 自己挑当前有效的因子来择时（按时序 Rank IC），叫上投资大师团做对立研究，写策略、自进化；每笔下单都经机器审批。回测、模拟盘和 live runner 共用一份代码，全球多市场自动路由，外加 Claude Code 式 hooks/permissions/plan-exec。",
    websiteDescription:
      "开源量化 agent 框架——一个会记账的神谕。按 Rank IC 因子择时、投资大师团对立研究、LLM 写策略并自进化、每笔下单经机器审批。",
    sourceDescription:
      "开源量化 agent 框架。Agent 按时序 Rank IC 选当前有效因子来择时（factor.timing），叫上投资大师团做对立研究，写策略、自进化；每笔下单经机器审批。",
  },
};
