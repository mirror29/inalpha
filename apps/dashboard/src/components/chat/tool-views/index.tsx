"use client";

import type { ReactNode } from "react";

import {
  BarsView,
  TickerView,
  isBars,
  isTicker,
} from "./MarketViews";
import {
  AccountView,
  BacktestView,
  CandidateListView,
  CandidateView,
  PositionsView,
  StrategyRunListView,
  StrategyRunView,
  isAccount,
  isBacktest,
  isCandidate,
  isCandidateList,
  isPositionList,
  isStrategyRun,
  isStrategyRunList,
} from "./PaperViews";
import { SearchView, isSearch } from "./SearchView";
import {
  CustomFactorView,
  FactorScoreView,
  isCustomFactor,
  isFactorScore,
} from "./FactorViews";
import { ResearchView, isResearch } from "./ResearchView";
import { TradePlanView, isPlan } from "./TradeViews";
import {
  MarketNewsView,
  MoneyflowView,
  MoversView,
  SectorBoardView,
  isMarketNews,
  isMoneyflow,
  isMovers,
  isSectorBoard,
} from "./MarketAttributionViews";
import {
  FundamentalsView,
  StrategyIdListView,
  WebFetchView,
  isFundamentals,
  isStrategyIdList,
  isWebFetch,
} from "./MiscViews";

/**
 * 工具名 → 专属可视化视图的注册分发。
 *
 * 设计:
 *  - 工具名先归一(AG-UI 把 id 里的 `.` 换成了 `_`,两种写法都接);
 *  - 命中工具名后仍要过 shape guard —— 服务端字段演进 / 错误形态不符时返 null,
 *    由调用方(ToolChip)回落到通用 ToolOutput,**宁可降级不可渲染错**;
 *  - 工具名没命中时做一轮纯形态嗅探(列表 / ticker / 回测等特征足够独特),
 *    新工具只要返回同形态数据即自动获得专属视图;
 *  - 错误封套 {isError:true} 一律不接,通用视图的红色 ERROR 标头处理。
 *
 * 新增视图三步:tool-views/ 下建组件 + 导出 shape guard + 在这里挂名字/嗅探。
 */
export function resolveToolView(toolName: string, v: unknown): ReactNode | null {
  if (!v || typeof v !== "object") return null;
  if ((v as { isError?: unknown }).isError === true) return null;

  const name = toolName.replace(/\./g, "_");
  const body = unwrapList(v);

  switch (name) {
    case "data_get_ticker":
      return isTicker(v) ? <TickerView t={v} /> : null;
    case "data_get_bars":
    case "data_backfill_bars":
      return isBars(v) ? <BarsView d={v} /> : null;
    case "paper_get_candidate":
    case "paper_promote_candidate":
      return isCandidate(v) ? <CandidateView c={v} /> : null;
    case "paper_list_candidates":
      return isCandidateList(body) ? <CandidateListView list={body} /> : null;
    case "paper_start_strategy":
    case "paper_stop_strategy":
      return isStrategyRun(v) ? <StrategyRunView r={v} /> : null;
    case "paper_list_strategy_runs":
      return isStrategyRunList(body) ? <StrategyRunListView list={body} /> : null;
    case "paper_list_strategies":
      return isStrategyIdList(v) ? <StrategyIdListView v={v} /> : null;
    case "paper_run_backtest":
      return isBacktest(v) ? <BacktestView b={v} /> : null;
    case "paper_get_account":
      return isAccount(v) ? <AccountView a={v} /> : null;
    case "paper_list_positions":
      return isPositionList(body) ? <PositionsView list={body} /> : null;
    case "web_search":
    case "web_search_news":
      return isSearch(v) ? <SearchView s={v} /> : null;
    case "research_deep_dive":
      return isResearch(v) ? <ResearchView r={v} /> : null;
    case "trade_create_plan":
    case "trade_get_plan":
      return isPlan(v) ? <TradePlanView p={v} /> : null;
    case "factor_timing":
    case "factor_score":
      return isFactorScore(v) ? <FactorScoreView s={v} /> : null;
    case "factor_evaluate_candidate":
    case "factor_custom_score":
      return isCustomFactor(v) ? <CustomFactorView c={v} /> : null;
    case "data_get_market_news":
      return isMarketNews(v) ? <MarketNewsView v={v} /> : null;
    case "data_get_market_sectors":
      return isSectorBoard(v) ? <SectorBoardView v={v} /> : null;
    case "data_get_market_moneyflow":
      return isMoneyflow(v) ? <MoneyflowView v={v} /> : null;
    case "data_get_market_movers":
      return isMovers(v) ? <MoversView v={v} /> : null;
    case "data_get_fundamentals":
      // 基本面字段随市场而异 → 仅按工具名派发,不进形态嗅探(避免误命中其它 dict)。
      return isFundamentals(v) ? <FundamentalsView v={v} /> : null;
    case "web_fetch":
      return isWebFetch(v) ? <WebFetchView v={v} /> : null;
  }

  // 名字没命中 → 形态嗅探(特征从强到弱,避免误判)。
  if (isBars(v)) return <BarsView d={v} />;
  if (isBacktest(v)) return <BacktestView b={v} />;
  if (isCustomFactor(v)) return <CustomFactorView c={v} />;
  if (isFactorScore(v)) return <FactorScoreView s={v} />;
  if (isResearch(v)) return <ResearchView r={v} />;
  if (isSearch(v)) return <SearchView s={v} />;
  if (isPlan(v)) return <TradePlanView p={v} />;
  if (isSectorBoard(v)) return <SectorBoardView v={v} />;
  if (isMoneyflow(v)) return <MoneyflowView v={v} />;
  if (isMarketNews(v)) return <MarketNewsView v={v} />;
  if (isMovers(v)) return <MoversView v={v} />;
  if (isWebFetch(v)) return <WebFetchView v={v} />;
  if (isStrategyIdList(v)) return <StrategyIdListView v={v} />;
  if (isCandidate(v)) return <CandidateView c={v} />;
  if (isStrategyRun(v)) return <StrategyRunView r={v} />;
  if (isCandidateList(body)) return <CandidateListView list={body} />;
  if (isStrategyRunList(body)) return <StrategyRunListView list={body} />;
  if (isTicker(v)) return <TickerView t={v} />;
  return null;
}

/** 列表工具可能返回裸数组,也可能包一层 {candidates|runs|positions|items: [...]}。 */
function unwrapList(v: unknown): unknown {
  if (Array.isArray(v)) return v;
  if (v && typeof v === "object") {
    for (const k of ["candidates", "runs", "strategy_runs", "positions", "items"]) {
      const inner = (v as Record<string, unknown>)[k];
      if (Array.isArray(inner)) return inner;
    }
  }
  return v;
}
