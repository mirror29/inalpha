"use client";

import { motion, useReducedMotion } from "motion/react";
import * as React from "react";

import { cn } from "@/lib/cn";

/**
 * Hero 主场景「関所の帳簿」—— canvas 实绘，一眼读出项目在做什么：
 * 一条行情线从左侧持续流入（quant），穿过朱红鸟居 = 审批关门（机器审批），
 * 穿门瞬间落朱印 + 出一行 mono 回执（审计链），过门后线色由数据青转 bull 绿。
 *
 * 工程约定：主题色实时读 CSS vars（监听 data-theme）；DPR 缩放；离屏 /
 * 后台标签页暂停 rAF；reduced-motion 一次性静态绘制（整线 + 鸟居 + 静态印），
 * 不留空白（DESIGN.md §6.3）。纯装饰层 aria-hidden，永不进数据面。
 */

interface StampEvent {
  /** canvas CSS 像素坐标（与画布同坐标系）。 */
  x: number;
  y: number;
  /** 周期序号，key 重触发落章动画。 */
  seq: number;
  /** 由 seq 派生的稳定伪随机 plan id（4 位 hex）。 */
  id: string;
}

interface Palette {
  accent: string;
  bull: string;
  seal: string;
  foxfire: string;
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
  };
}

function planId(seq: number): string {
  return (((seq + 7) * 2654435761) >>> 0).toString(16).padStart(8, "0").slice(0, 4);
}

/** momentum 随机游走；接近鸟居时把线收进门洞（边界只有一个口子）。 */
function buildWalk(w: number, h: number, gateX: number, openY: number): Pt[] {
  const pts: Pt[] = [];
  const steps = 150;
  const x0 = -w * 0.04;
  const dx = (w * 1.06 - x0) / steps;
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

/** 鸟居剪影（明神鳥居）—— 面填充而非线稿：低饱和朱红、有分量，不是简笔画。 */
function drawTorii(
  ctx: CanvasRenderingContext2D,
  cx: number,
  baseY: number,
  gh: number,
  color: string
) {
  const u = gh / 160;
  const X = (x: number) => cx + (x - 100) * u;
  const Y = (y: number) => baseY - (150 - y) * u;
  ctx.save();
  ctx.fillStyle = color;
  /* 笠木（顶梁，中部上拱、两端微翘） */
  ctx.beginPath();
  ctx.moveTo(X(8), Y(26));
  ctx.quadraticCurveTo(X(100), Y(10), X(192), Y(26));
  ctx.lineTo(X(190), Y(36));
  ctx.quadraticCurveTo(X(100), Y(22), X(10), Y(36));
  ctx.closePath();
  ctx.fill();
  /* 島木（次梁） */
  ctx.fillRect(X(22), Y(38), 156 * u, 8 * u);
  /* 額束（中央短柱） */
  ctx.fillRect(X(96), Y(46), 8 * u, 20 * u);
  /* 貫（中梁，两端出头） */
  ctx.fillRect(X(26), Y(66), 148 * u, 8 * u);
  /* 双柱（内倾 + 微收分） */
  const pillar = (txc: number, bxc: number) => {
    ctx.beginPath();
    ctx.moveTo(X(txc) - 3.6 * u, Y(36));
    ctx.lineTo(X(txc) + 3.6 * u, Y(36));
    ctx.lineTo(X(bxc) + 4.4 * u, Y(150));
    ctx.lineTo(X(bxc) - 4.4 * u, Y(150));
    ctx.closePath();
    ctx.fill();
  };
  pillar(54, 60);
  pillar(146, 140);
  ctx.restore();
}

export function HeroScene({ className }: { className?: string }) {
  const reduce = useReducedMotion();
  const wrapRef = React.useRef<HTMLDivElement>(null);
  const canvasRef = React.useRef<HTMLCanvasElement>(null);
  const [stamp, setStamp] = React.useState<StampEvent | null>(null);

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
      geom = { w, h, gateX, gateBaseY, gateH, openY, pts: buildWalk(w, h, gateX, openY) };
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
      const d = dpr();
      ctx!.setTransform(d, 0, 0, d, 0, 0);
      ctx!.clearRect(0, 0, w, h);
      const fade = p > FADE_FROM ? 1 - (p - FADE_FROM) / (1 - FADE_FROM) : 1;

      /* 1 · 鸟居（穿门时短暂提亮） */
      let glow = 0;
      for (const r of rings) {
        const k = (tMs - r.t) / RING_MS;
        if (k >= 0 && k < 1) glow = Math.max(glow, 1 - k);
      }
      drawTorii(ctx!, gateX, gateBaseY, gateH, rgba(palette.seal, 0.34 + glow * 0.28));

      const headI = Math.floor(Math.min(p / FADE_FROM, 1) * (pts.length - 1));
      const head = pts[Math.min(headI, pts.length - 1)];

      /* 3 · 幽灵 K 线柱（沿线采样，极低 alpha） */
      for (let i = 4; i < headI; i += 7) {
        const c = pts[i].x >= gateX ? palette.bull : palette.accent;
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
        ctx!.strokeStyle = rgba(b.x >= gateX ? palette.bull : palette.accent, alpha);
        ctx!.lineWidth = 1.6;
        ctx!.beginPath();
        ctx!.moveTo(a.x, a.y);
        ctx!.lineTo(b.x, b.y);
        ctx!.stroke();
      }

      /* 5 · 头部光点 + 光晕 + 随行报价 */
      if (animated && p <= FADE_FROM) {
        const c = head.x >= gateX ? palette.bull : palette.accent;
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

      /* 7 · 狐火余烬（鸟居附近上飘 + 闪烁，纯装饰层用 --foxfire） */
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
        if (geom) geom.pts = buildWalk(geom.w, geom.h, geom.gateX, geom.openY);
      }
      if (geom && !crossed) {
        const head = headAt(p);
        if (head.x >= geom.gateX) {
          crossed = true;
          rings.push({ x: geom.gateX, y: head.y, t: elapsed });
          setStamp({ x: geom.gateX, y: head.y, seq, id: planId(seq) });
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
      setStamp({ x: geom.gateX, y: geom.openY, seq: 0, id: planId(0) });
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
      {/* 穿门落章：Emblem 朱印 + mono 审计回执（D2 临床面 → 等宽精确） */}
      {stamp ? (
        <motion.div
          key={stamp.seq}
          initial={reduce ? false : { opacity: 0 }}
          animate={reduce ? { opacity: 1 } : { opacity: [0, 1, 1, 0] }}
          transition={
            reduce ? { duration: 0 } : { duration: 2.6, times: [0, 0.1, 0.78, 1] }
          }
          className="absolute"
          style={{ left: stamp.x, top: stamp.y - 36 }}
        >
          <div className="-translate-x-1/2 -translate-y-1/2">
            <span
              className="seal-stamp display-italic flex size-6 -rotate-6 items-center justify-center rounded-[3px] border-[1.5px] border-seal/80 text-[15px] leading-none text-seal/90"
              style={{ background: "color-mix(in oklab, var(--seal) 8%, transparent)" }}
            >
              α
            </span>
          </div>
          <div className="absolute left-0 top-4 -translate-x-1/2 whitespace-nowrap font-mono text-[10px] uppercase tracking-[0.2em] text-seal/85">
            plan #{stamp.id} · approved
          </div>
        </motion.div>
      ) : null}
    </div>
  );
}
