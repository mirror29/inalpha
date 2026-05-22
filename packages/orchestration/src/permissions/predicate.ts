/**
 * Predicate 解析 + 求值（ADR-0011 §matcher）。
 *
 * 语法子集（MVP）：
 *
 *   predicate := condition (&& condition)*
 *   condition := IDENT op VALUE
 *   op        := < | <= | > | >= | == | != | in | not in
 *   VALUE     := NUMBER | "STRING" | IDENT | [VALUE (,VALUE)*]
 *
 * 实现是手写小 tokenizer + recursive-descent，避免 eval / new Function 引入注入风险。
 *
 * 例子：
 *
 *   "notional<1000"                                  →  { conditions: [{notional, <, 1000}] }
 *   "size<0.05 && symbol in [BTC,ETH,SOL]"           →  两条 AND
 *   "delegationHop>1"                                →  单条件
 */
import type { Predicate, PredicateCondition } from "./types.js";

class ParseError extends Error {
  constructor(message: string, public readonly position: number) {
    super(`${message} (at position ${position})`);
    this.name = "ParseError";
  }
}

class Tokenizer {
  private pos = 0;

  constructor(private readonly src: string) {}

  peek(): string {
    return this.src[this.pos] ?? "";
  }

  eof(): boolean {
    return this.pos >= this.src.length;
  }

  skipWs(): void {
    while (this.pos < this.src.length && /\s/.test(this.src[this.pos]!)) {
      this.pos += 1;
    }
  }

  /** 读 IDENT：[a-zA-Z_][a-zA-Z0-9_]* */
  readIdent(): string {
    this.skipWs();
    const start = this.pos;
    if (!/[a-zA-Z_]/.test(this.peek())) {
      throw new ParseError(`expected identifier`, this.pos);
    }
    while (this.pos < this.src.length && /[a-zA-Z0-9_.]/.test(this.src[this.pos]!)) {
      this.pos += 1;
    }
    return this.src.slice(start, this.pos);
  }

  /** 读数值（可负） */
  readNumber(): number {
    this.skipWs();
    const start = this.pos;
    if (this.peek() === "-" || this.peek() === "+") this.pos += 1;
    while (this.pos < this.src.length && /[0-9.]/.test(this.src[this.pos]!)) {
      this.pos += 1;
    }
    const s = this.src.slice(start, this.pos);
    const n = Number(s);
    if (!Number.isFinite(n)) {
      throw new ParseError(`invalid number ${JSON.stringify(s)}`, start);
    }
    return n;
  }

  /** 读 op：<= < >= > == != in / not in */
  readOp(): PredicateCondition["op"] {
    this.skipWs();
    const two = this.src.substr(this.pos, 2);
    if (two === "<=" || two === ">=" || two === "==" || two === "!=") {
      this.pos += 2;
      return two as PredicateCondition["op"];
    }
    const one = this.peek();
    if (one === "<" || one === ">") {
      this.pos += 1;
      return one as PredicateCondition["op"];
    }
    // 关键字：in / not in
    if (this.src.startsWith("not", this.pos)) {
      this.pos += 3;
      this.skipWs();
      if (!this.src.startsWith("in", this.pos)) {
        throw new ParseError(`expected 'in' after 'not'`, this.pos);
      }
      this.pos += 2;
      return "not in";
    }
    if (this.src.startsWith("in", this.pos) && !/[a-zA-Z0-9_]/.test(this.src[this.pos + 2] ?? "")) {
      this.pos += 2;
      return "in";
    }
    throw new ParseError(`expected operator`, this.pos);
  }

  /** 读 VALUE（NUMBER / "STRING" / IDENT / [...]） */
  readValue(): number | string | (number | string)[] {
    this.skipWs();
    const c = this.peek();
    if (c === "[") {
      this.pos += 1;
      const out: (number | string)[] = [];
      this.skipWs();
      while (this.peek() !== "]") {
        const v = this.readSingleValue();
        out.push(v);
        this.skipWs();
        if (this.peek() === ",") {
          this.pos += 1;
          this.skipWs();
        } else if (this.peek() !== "]") {
          throw new ParseError(`expected ',' or ']' in list`, this.pos);
        }
      }
      this.pos += 1; // consume ]
      return out;
    }
    return this.readSingleValue();
  }

  private readSingleValue(): number | string {
    this.skipWs();
    const c = this.peek();
    if (c === '"' || c === "'") {
      this.pos += 1;
      const start = this.pos;
      while (this.pos < this.src.length && this.src[this.pos] !== c) this.pos += 1;
      const s = this.src.slice(start, this.pos);
      if (this.peek() !== c) throw new ParseError(`unterminated string`, start - 1);
      this.pos += 1;
      return s;
    }
    if (/[0-9\-+.]/.test(c)) {
      return this.readNumber();
    }
    // 裸 IDENT 当字符串字面量
    return this.readIdent();
  }

  matchAnd(): boolean {
    this.skipWs();
    if (this.src.startsWith("&&", this.pos)) {
      this.pos += 2;
      return true;
    }
    return false;
  }
}

/** 解析 predicate 字符串。空字符串返 null。 */
export function parsePredicate(src: string): Predicate | null {
  const trimmed = src.trim();
  if (!trimmed) return null;

  const t = new Tokenizer(trimmed);
  const conditions: PredicateCondition[] = [];

  while (true) {
    t.skipWs();
    if (t.eof()) break;
    const field = t.readIdent();
    const op = t.readOp();
    const value = t.readValue();

    if ((op === "in" || op === "not in") && !Array.isArray(value)) {
      throw new ParseError(`'${op}' requires a list value`, 0);
    }
    if (op !== "in" && op !== "not in" && Array.isArray(value)) {
      throw new ParseError(`numeric / equality op cannot take list value`, 0);
    }

    conditions.push({ field, op, value });

    if (!t.matchAnd()) break;
  }

  t.skipWs();
  if (!t.eof()) {
    throw new ParseError(`unexpected trailing input`, 0);
  }

  if (conditions.length === 0) return null;
  return { conditions };
}

/**
 * 在给定 ``input`` 上求值 predicate。``input`` 可能是 dict 或 Mastra inputData。
 *
 * 取字段值时支持点路径（``orderParams.quantity``），不存在视作 undefined。
 *
 * 比较语义：
 *
 * - 数值 op：仅当两侧都是 number 时比较；任一非 number → false
 * - ``==`` / ``!=``：宽松相等（字符串 vs 字符串、数字 vs 数字）
 * - ``in`` / ``not in``：lhs 必须是 scalar，rhs 是 list，做 includes
 */
export function evaluatePredicate(p: Predicate, input: unknown): boolean {
  for (const c of p.conditions) {
    if (!evaluateOne(c, input)) return false;
  }
  return true;
}

function evaluateOne(c: PredicateCondition, input: unknown): boolean {
  const lhs = getField(input, c.field);

  switch (c.op) {
    case "<":
    case "<=":
    case ">":
    case ">=": {
      const a = typeof lhs === "number" ? lhs : Number(lhs);
      const b = typeof c.value === "number" ? c.value : Number(c.value);
      if (!Number.isFinite(a) || !Number.isFinite(b)) return false;
      return c.op === "<"
        ? a < b
        : c.op === "<="
          ? a <= b
          : c.op === ">"
            ? a > b
            : a >= b;
    }
    case "==":
      return looseEq(lhs, c.value);
    case "!=":
      return !looseEq(lhs, c.value);
    case "in":
      return Array.isArray(c.value) && c.value.some((v) => looseEq(lhs, v));
    case "not in":
      return Array.isArray(c.value) && !c.value.some((v) => looseEq(lhs, v));
    default:
      return false;
  }
}

function looseEq(a: unknown, b: unknown): boolean {
  if (typeof a === "number" && typeof b === "number") return a === b;
  if (typeof a === "number") return a === Number(b);
  if (typeof b === "number") return Number(a) === b;
  return String(a) === String(b);
}

function getField(obj: unknown, path: string): unknown {
  if (obj === null || typeof obj !== "object") return undefined;
  const parts = path.split(".");
  let cur: unknown = obj;
  for (const p of parts) {
    if (cur === null || typeof cur !== "object") return undefined;
    cur = (cur as Record<string, unknown>)[p];
  }
  return cur;
}
