"use client";

import { motion, useReducedMotion } from "motion/react";
import Image from "next/image";
import * as React from "react";

import sealIcon from "@/app/icon.png";
import { cn } from "@/lib/cn";

/**
 * Hero 主场景「关卡账簿」—— canvas 实绘，一眼读出项目在做什么：
 * 一条行情线从左侧持续流入（quant），穿过朱红审批阈线 = 审计关卡（机器审批），
 * 穿线瞬间落朱印（品牌 favicon）+ 出一行 mono 回执，右侧账簿逐条入账（审计链）。
 * 过线后线色由本轮 verdict 决定：走高 bull 绿（approved）/ 走低 bear 红（rejected）。
 * 阈线是行情图式的垂直虚线事件标记，不画具象鸟居——神道气质交给朱印与狐火。
 *
 * 工程约定：主题色实时读 CSS vars（监听 data-theme）；DPR 缩放；离屏 /
 * 后台标签页暂停 rAF；reduced-motion 一次性静态绘制（整线 + 阈线 + 静态印），
 * 不留空白（DESIGN.md §6.3）。纯装饰层 aria-hidden，永不进数据面。
 */

interface StampEvent {
  /** canvas CSS 像素坐标（与画布同坐标系）。 */
  x: number;
  y: number;
  /** 周期序号，key 重触发落章动画。 */
  seq: number;
  /** 穿线时刻的展示价（与头部随行报价同公式）。 */
  price: string;
  /** verdict：过线后走高 = approved（朱印），走低 = rejected（墨印）。 */
  approved: boolean;
}

interface Palette {
  accent: string;
  bull: string;
  seal: string;
  foxfire: string;
  down: string;
}

interface Pt {
  x: number;
  y: number;
}

interface SceneGeom {
  w: number;
  h: number;
  gateX: number;
  gateBaseY: number;
  gateH: number;
  openY: number;
  pts: Pt[];
  /** 本轮 verdict：过线后线条走高 = approved，走低 = rejected。 */
  up: boolean;
}

interface Ember {
  x: number;
  y: number;
  r: number;
  /** 上飘速度 px/s。 */
  speed: number;
  phase: number;
}

interface Ring {
  x: number;
  y: number;
  /** 场景时间（ms）。 */
  t: number;
}

/** 一轮行情线的时长。 */
const CYCLE_MS = 11000;
/** 周期最后 8% 整线淡出，给下一轮 walk 留呼吸。 */
const FADE_FROM = 0.92;
const EMBER_COUNT = 7;
const RING_MS = 700;

function hexToRgb(hex: string): [number, number, number] | null {
  let h = hex.replace("#", "").trim();
  if (h.length === 3)
    h = h
      .split("")
      .map((c) => c + c)
      .join("");
  if (!/^[0-9a-fA-F]{6}$/.test(h)) return null;
  const n = parseInt(h, 16);
  return [(n >> 16) & 255, (n >> 8) & 255, n & 255];
}

function rgba(hex: string, alpha: number): string {
  const rgb = hexToRgb(hex);
  if (!rgb) return hex;
  return `rgba(${rgb[0]},${rgb[1]},${rgb[2]},${alpha})`;
}

function readPalette(): Palette {
  const cs = getComputedStyle(document.documentElement);
  const pick = (name: string, fallback: string) =>
    cs.getPropertyValue(name).trim() || fallback;
  return {
    accent: pick("--accent", "#5fb3ff"),
    bull: pick("--bull", "#4ade80"),
    seal: pick("--seal", "#c8463c"),
    foxfire: pick("--foxfire", "#5fd3b0"),
    down: pick("--down", "#c8463c"),
  };
}

/** 展示价 —— 与头部随行报价同公式，保证落章回执和线头数字一致。 */
function priceAt(h: number, y: number, seq: number): string {
  return (1432 + (h * 0.5 - y) * 0.9 + seq * 3.7).toFixed(1);
}

/** 账簿第一栏的 alpha 名 —— 装饰用因子名池，按 seq 轮换（不是真实持仓）。 */
const ALPHA_NAMES = [
  "α·momentum",
  "α·meanrev",
  "α·carry",
  "α·value",
  "α·breakout",
  "α·volprem",
  "α·pairs",
  "α·macro",
] as const;

function alphaName(seq: number): string {
  return ALPHA_NAMES[((seq % ALPHA_NAMES.length) + ALPHA_NAMES.length) % ALPHA_NAMES.length];
}

/** 本轮 verdict —— 过线后线尾均值高于关口（canvas y 更小）= approved。 */
function walkOutcome(pts: Pt[], gateX: number): boolean {
  const gi = pts.findIndex((p) => p.x >= gateX);
  if (gi < 0) return true;
  const tail = pts.slice(gi);
  const mean = tail.reduce((s, p) => s + p.y, 0) / tail.length;
  return mean <= pts[gi].y;
}

/** momentum 随机游走；接近审批阈线时把线收向关口（边界只有一个口子）。
 *  行程止于 0.8w —— 过线变色后跑一小段就淡出，不穿到右侧账簿列底下。 */
function buildWalk(w: number, h: number, gateX: number, openY: number): Pt[] {
  const pts: Pt[] = [];
  const steps = 150;
  const x0 = -w * 0.04;
  const dx = (w * 0.8 - x0) / steps;
  let y = h * (0.36 + Math.random() * 0.2);
  let v = 0;
  for (let i = 0; i <= steps; i++) {
    const x = x0 + dx * i;
    v += (Math.random() - 0.5) * h * 0.022;
    v *= 0.9;
    y += v;
    const d = Math.abs(x - gateX);
    const pullZone = w * 0.2;
    if (d < pullZone) {
      const k = 1 - d / pullZone;
      y += (openY - y) * k * 0.22;
    }
    y = Math.min(h * 0.78, Math.max(h * 0.18, y));
    pts.push({ x, y });
  }
  return pts;
}

function makeEmbers(geom: SceneGeom): Ember[] {
  return Array.from({ length: EMBER_COUNT }, () => ({
    x: geom.gateX + (Math.random() - 0.5) * geom.gateH * 1.4,
    y: geom.gateBaseY - Math.random() * geom.gateH * 1.1,
    r: 1.1 + Math.random() * 1.5,
    speed: 6 + Math.random() * 13,
    phase: Math.random() * Math.PI * 2,
  }));
}

/**
 * 审批阈线 —— 抽象「关卡」标记：垂直朱红虚线 + 上下端短横 + 竖排 mono 标签，
 * 读法等同行情图上的垂直事件注记（threshold / event marker），临床而非简笔画。
 */
function drawGate(
  ctx: CanvasRenderingContext2D,
  x: number,
  topY: number,
  bottomY: number,
  color: string,
  labelFont: string,
  labelColor: string
) {
  ctx.save();
  ctx.strokeStyle = color;
  ctx.lineWidth = 1.2;
  ctx.setLineDash([2, 7]);
  ctx.beginPath();
  ctx.moveTo(x, topY);
  ctx.lineTo(x, bottomY);
  ctx.stroke();
  ctx.setLineDash([]);
  /* 上下端短横（量尺端点感） */
  ctx.lineWidth = 1.4;
  ctx.beginPath();
  ctx.moveTo(x - 8, topY);
  ctx.lineTo(x + 8, topY);
  ctx.moveTo(x - 8, bottomY);
  ctx.lineTo(x + 8, bottomY);
  ctx.stroke();
  /* 竖排标签，标明这是审批关卡（letterSpacing 不支持时静默退化） */
  ctx.translate(x + 5, topY + 18);
  ctx.rotate(Math.PI / 2);
  ctx.font = labelFont;
  (ctx as CanvasRenderingContext2D & { letterSpacing?: string }).letterSpacing = "0.22em";
  ctx.fillStyle = labelColor;
  ctx.fillText("AUDIT GATE", 0, 0);
  ctx.restore();
}

export function HeroScene({ className }: { className?: string }) {
  const reduce = useReducedMotion();
  const wrapRef = React.useRef<HTMLDivElement>(null);
  const canvasRef = React.useRef<HTMLCanvasElement>(null);
  const [stamp, setStamp] = React.useState<StampEvent | null>(null);
  /** 过关审计回执（含 rejected），新的在上，最多 4 条 —— 右侧账簿列。
      初始即有一条种子回执，首轮穿线前右侧不留空。 */
  const [ledger, setLedger] = React.useState<StampEvent[]>([
    { x: 0, y: 0, seq: -1, price: "1429.6", approved: true },
  ]);

  const pushStamp = React.useCallback((ev: StampEvent) => {
    setStamp(ev);
    setLedger((prev) => [ev, ...prev].slice(0, 4));
  }, []);

  React.useEffect(() => {
    const wrap = wrapRef.current;
    const canvas = canvasRef.current;
    if (!wrap || !canvas) return;
    const ctx = canvas.getContext("2d");
    if (!ctx) return;

    let palette = readPalette();
    const monoStack =
      getComputedStyle(document.documentElement)
        .getPropertyValue("--font-mono")
        .trim() || "ui-monospace, monospace";
    const monoFont = `11px ${monoStack}`;

    let geom: SceneGeom | null = null;
    let embers: Ember[] = [];
    let rings: Ring[] = [];
    let raf = 0;
    let running = false;
    let inView = true;
    /** 场景时间：暂停时不前进，恢复后动画无跳变。 */
    let elapsed = 0;
    let cycleStart = 0;
    let lastTs = 0;
    let seq = 0;
    let crossed = false;

    const dpr = () => Math.min(window.devicePixelRatio || 1, 2);

    function rebuild() {
      const w = wrap!.clientWidth;
      const h = wrap!.clientHeight;
      if (!w || !h) return;
      canvas!.width = Math.round(w * dpr());
      canvas!.height = Math.round(h * dpr());
      canvas!.style.width = `${w}px`;
      canvas!.style.height = `${h}px`;
      const gateX = w < 768 ? w * 0.72 : w * 0.68;
      const gateH = Math.max(180, Math.min(h * 0.52, w * 0.3, 430));
      const gateBaseY = h * 0.78;
      const openY = gateBaseY - gateH * 0.42;
      const pts = buildWalk(w, h, gateX, openY);
      geom = { w, h, gateX, gateBaseY, gateH, openY, pts, up: walkOutcome(pts, gateX) };
      embers = makeEmbers(geom);
    }

    function headAt(p: number): Pt {
      const pts = geom!.pts;
      const f = Math.min(p / FADE_FROM, 1) * (pts.length - 1);
      return pts[Math.min(Math.floor(f), pts.length - 1)];
    }

    function draw(p: number, tMs: number, animated: boolean) {
      if (!geom) return;
      const { w, h, gateX, gateBaseY, gateH, pts } = geom;
      /* 过线后的线色由本轮 verdict 决定：走高 bull 绿，走低 bear 红 */
      const postColor = geom.up ? palette.bull : palette.down;
      const d = dpr();
      ctx!.setTransform(d, 0, 0, d, 0, 0);
      ctx!.clearRect(0, 0, w, h);
      const fade = p > FADE_FROM ? 1 - (p - FADE_FROM) / (1 - FADE_FROM) : 1;

      /* 1 · 审批阈线（穿线时短暂提亮） */
      let glow = 0;
      for (const r of rings) {
        const k = (tMs - r.t) / RING_MS;
        if (k >= 0 && k < 1) glow = Math.max(glow, 1 - k);
      }
      drawGate(
        ctx!,
        gateX,
        gateBaseY - gateH,
        gateBaseY,
        rgba(palette.seal, 0.38 + glow * 0.32),
        monoFont,
        rgba(palette.seal, 0.45 + glow * 0.25)
      );

      const headI = Math.floor(Math.min(p / FADE_FROM, 1) * (pts.length - 1));
      const head = pts[Math.min(headI, pts.length - 1)];

      /* 3 · 幽灵 K 线柱（沿线采样，极低 alpha） */
      for (let i = 4; i < headI; i += 7) {
        const c = pts[i].x >= gateX ? postColor : palette.accent;
        const amp = Math.min(20, Math.abs(pts[i].y - pts[Math.max(0, i - 3)].y) * 1.6 + 6);
        const age = Math.max(0, (head.x - pts[i].x) / (w * 0.55));
        const alpha = Math.max(0, 1 - age) * 0.1 * fade;
        if (alpha <= 0.01) continue;
        ctx!.strokeStyle = rgba(c, alpha);
        ctx!.lineWidth = 3;
        ctx!.beginPath();
        ctx!.moveTo(pts[i].x, pts[i].y - amp);
        ctx!.lineTo(pts[i].x, pts[i].y + amp);
        ctx!.stroke();
      }

      /* 4 · 价格线（彗尾渐隐；过门前数据青，过门后 bull 绿） */
      for (let i = 1; i <= headI; i++) {
        const a = pts[i - 1];
        const b = pts[i];
        const age = Math.max(0, (head.x - b.x) / (w * 0.55));
        const alpha = Math.max(0, 1 - age) * 0.75 * fade;
        if (alpha <= 0.01) continue;
        ctx!.strokeStyle = rgba(b.x >= gateX ? postColor : palette.accent, alpha);
        ctx!.lineWidth = 1.6;
        ctx!.beginPath();
        ctx!.moveTo(a.x, a.y);
        ctx!.lineTo(b.x, b.y);
        ctx!.stroke();
      }

      /* 5 · 头部光点 + 光晕 + 随行报价 */
      if (animated && p <= FADE_FROM) {
        const c = head.x >= gateX ? postColor : palette.accent;
        const halo = ctx!.createRadialGradient(head.x, head.y, 0, head.x, head.y, 26);
        halo.addColorStop(0, rgba(c, 0.32 * fade));
        halo.addColorStop(1, rgba(c, 0));
        ctx!.fillStyle = halo;
        ctx!.beginPath();
        ctx!.arc(head.x, head.y, 26, 0, Math.PI * 2);
        ctx!.fill();
        ctx!.fillStyle = rgba(c, 0.95 * fade);
        ctx!.beginPath();
        ctx!.arc(head.x, head.y, 2.8, 0, Math.PI * 2);
        ctx!.fill();
        const price = (1432 + (h * 0.5 - head.y) * 0.9 + seq * 3.7).toFixed(1);
        ctx!.font = monoFont;
        ctx!.fillStyle = rgba(c, 0.6 * fade);
        ctx!.fillText(price, head.x + 12, head.y - 10);
      }

      /* 6 · 穿门扩散环 */
      for (const r of rings) {
        const k = (tMs - r.t) / RING_MS;
        if (k < 0 || k >= 1) continue;
        ctx!.strokeStyle = rgba(palette.seal, 0.45 * (1 - k));
        ctx!.lineWidth = 1.5;
        ctx!.beginPath();
        ctx!.arc(r.x, r.y, 6 + k * 54, 0, Math.PI * 2);
        ctx!.stroke();
      }
      rings = rings.filter((r) => tMs - r.t < RING_MS);

      /* 7 · 狐火余烬（阈线附近上飘 + 闪烁，纯装饰层用 --foxfire） */
      for (const e of embers) {
        const alpha = 0.16 + 0.2 * (0.5 + 0.5 * Math.sin(tMs / 560 + e.phase));
        ctx!.fillStyle = rgba(palette.foxfire, alpha);
        ctx!.beginPath();
        ctx!.arc(e.x, e.y, e.r, 0, Math.PI * 2);
        ctx!.fill();
      }
    }

    function updateEmbers(dtSec: number) {
      if (!geom) return;
      for (const e of embers) {
        e.y -= e.speed * dtSec;
        e.x += Math.sin(elapsed / 900 + e.phase) * 0.08;
        if (e.y < geom.gateBaseY - geom.gateH * 1.35) {
          e.y = geom.gateBaseY + 6;
          e.x = geom.gateX + (Math.random() - 0.5) * geom.gateH * 1.4;
        }
      }
    }

    function tick(ts: number) {
      if (!running) return;
      const dt = lastTs ? Math.min(50, ts - lastTs) : 16;
      lastTs = ts;
      elapsed += dt;
      updateEmbers(dt / 1000);
      let p = (elapsed - cycleStart) / CYCLE_MS;
      if (p >= 1) {
        seq += 1;
        crossed = false;
        cycleStart = elapsed;
        p = 0;
        if (geom) {
          geom.pts = buildWalk(geom.w, geom.h, geom.gateX, geom.openY);
          geom.up = walkOutcome(geom.pts, geom.gateX);
        }
      }
      if (geom && !crossed) {
        const head = headAt(p);
        if (head.x >= geom.gateX) {
          crossed = true;
          rings.push({ x: geom.gateX, y: head.y, t: elapsed });
          pushStamp({
            x: geom.gateX,
            y: head.y,
            seq,
            price: priceAt(geom.h, head.y, seq),
            approved: geom.up,
          });
        }
      }
      draw(p, elapsed, true);
      raf = requestAnimationFrame(tick);
    }

    function start() {
      if (running || !inView || document.hidden) return;
      running = true;
      lastTs = 0;
      raf = requestAnimationFrame(tick);
    }
    function stop() {
      running = false;
      cancelAnimationFrame(raf);
    }

    function drawStatic() {
      if (!geom) return;
      draw(FADE_FROM, 0, false);
      /* 静态呈现：一枚定格朱印 + 三条历史回执（含一条 rejected），不留空白。 */
      const rows = [2, 1, 0].map((s) => ({
        x: geom!.gateX,
        y: geom!.openY,
        seq: s,
        price: priceAt(geom!.h, geom!.openY - s * 14, s),
        approved: s !== 1,
      }));
      setStamp(rows[rows.length - 1]);
      setLedger(rows.reverse());
    }

    rebuild();
    if (reduce) drawStatic();
    else start();

    const ro = new ResizeObserver(() => {
      rebuild();
      if (reduce) drawStatic();
      else if (!running) draw((elapsed - cycleStart) / CYCLE_MS, elapsed, true);
    });
    ro.observe(wrap);

    const io = new IntersectionObserver((entries) => {
      inView = entries[0]?.isIntersecting ?? true;
      if (reduce) return;
      if (inView) start();
      else stop();
    });
    io.observe(wrap);

    const onVis = () => {
      if (reduce) return;
      if (document.hidden) stop();
      else start();
    };
    document.addEventListener("visibilitychange", onVis);

    const mo = new MutationObserver(() => {
      palette = readPalette();
      if (reduce) drawStatic();
    });
    mo.observe(document.documentElement, { attributes: true, attributeFilter: ["data-theme"] });

    return () => {
      stop();
      ro.disconnect();
      io.disconnect();
      mo.disconnect();
      document.removeEventListener("visibilitychange", onVis);
    };
  }, [reduce]);

  return (
    <div
      ref={wrapRef}
      aria-hidden
      className={cn(
        "pointer-events-none select-none opacity-[0.45] md:opacity-100",
        className
      )}
    >
      <canvas ref={canvasRef} className="absolute inset-0" />
      {/* 穿线落章：朱印 favicon（品牌印章）+ mono 审计回执（D2 临床面 → 等宽精确） */}
      {stamp ? (
        <motion.div
          key={stamp.seq}
          initial={reduce ? false : { opacity: 0 }}
          animate={reduce ? { opacity: 1 } : { opacity: [0, 1, 1, 0] }}
          transition={
            reduce ? { duration: 0 } : { duration: 2.6, times: [0, 0.1, 0.78, 1] }
          }
          className="absolute"
          style={{ left: stamp.x, top: stamp.y - 46 }}
        >
          <div className="-translate-x-1/2 -translate-y-1/2">
            {/* 印面自带手绘倾斜，不再额外 rotate；rejected 盖墨印（去色） */}
            <Image
              src={sealIcon}
              alt=""
              width={56}
              height={56}
              className={cn(
                "seal-stamp",
                stamp.approved ? "seal-glow" : "opacity-75 grayscale"
              )}
            />
          </div>
          <div
            className={cn(
              "absolute left-0 top-9 -translate-x-1/2 whitespace-nowrap font-mono text-[10px] uppercase tracking-[0.2em]",
              stamp.approved ? "text-seal/85" : "text-fg-muted/80"
            )}
          >
            {alphaName(stamp.seq)} · {stamp.approved ? "approved" : "rejected"}
          </div>
        </motion.div>
      ) : null}
      {/* 右侧「账簿」—— 每轮 verdict 逐条入账（有过有拒），呼应 tagline「keeps a ledger」 */}
      {ledger.length ? (
        <div className="absolute right-[3.5%] top-[24%] hidden w-60 flex-col font-mono text-[10.5px] md:flex">
          <div className="flex items-baseline justify-between border-b border-seal/35 pb-2">
            <span className="uppercase tracking-[0.3em] text-seal/80">Ledger</span>
            <span className="flex items-center gap-1.5 uppercase tracking-[0.2em] text-fg-muted/60">
              <span className="caret-blink inline-block size-1.5 rounded-full bg-seal/70" />
              live
            </span>
          </div>
          {ledger.map((r, i) => (
            <motion.div
              key={r.seq}
              layout
              initial={reduce ? false : { opacity: 0, y: -10 }}
              animate={{ opacity: Math.max(0.3, 1 - i * 0.22), y: 0 }}
              transition={{ duration: 0.5, ease: [0.22, 0.7, 0.22, 1] }}
              className="flex items-baseline gap-3 border-b border-fg/8 py-2.5 tracking-[0.08em] text-fg-muted"
            >
              <span className="text-seal/85">{alphaName(r.seq)}</span>
              <span className="ml-auto tabular-nums">{r.price}</span>
              <span
                className={cn(
                  "uppercase tracking-[0.18em]",
                  r.approved ? "text-bull/75" : "text-fox-red/80"
                )}
              >
                {r.approved ? "approved" : "rejected"}
              </span>
            </motion.div>
          ))}
        </div>
      ) : null}
    </div>
  );
}
