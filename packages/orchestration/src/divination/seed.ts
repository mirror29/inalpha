/**
 * 确定性随机数源 —— 玄学引擎的"卦象 / 牌面"抽取必须可复现。
 *
 * 为什么不用 `Math.random()`：
 * - 占卜语义上"同一问应得同一卦"，确定性才符合直觉；
 * - 项目纪律禁用不可控随机 / 时间，便于单测断言"同 seed 同结果"。
 *
 * 实现：FNV-1a 把 seed 字符串散列成 32 位整数，再喂给 mulberry32 PRNG。
 * 二者都是无依赖的经典小算法，分布对"娱乐性抽取"足够均匀。
 */

/**
 * 把任意字符串散列成 32 位无符号整数(FNV-1a)。
 *
 * @param str 任意 seed 字符串(如 `question` + `symbol`)
 * @returns 32 位无符号整数种子
 */
export function hashSeed(str: string): number {
  let h = 0x811c9dc5; // FNV offset basis
  for (let i = 0; i < str.length; i += 1) {
    h ^= str.charCodeAt(i);
    // FNV prime 乘法,用 Math.imul 保证 32 位回绕
    h = Math.imul(h, 0x01000193);
  }
  return h >>> 0;
}

/**
 * mulberry32 —— 极小的确定性 PRNG。给定 32 位种子,返回一个每次产出 [0,1) 的函数。
 *
 * @param seed 32 位整数种子(一般来自 {@link hashSeed})
 * @returns 调用一次返回一个 [0,1) 浮点数的生成器
 */
export function mulberry32(seed: number): () => number {
  let a = seed >>> 0;
  return function next(): number {
    a |= 0;
    a = (a + 0x6d2b79f5) | 0;
    let t = Math.imul(a ^ (a >>> 15), 1 | a);
    t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t;
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
}

/**
 * 便捷封装：从 seed 字符串直接拿到一个确定性 [0,1) 生成器。
 *
 * @param seedStr seed 字符串；空串也接受(散列成固定种子)
 * @returns [0,1) 生成器
 */
export function makeRng(seedStr: string): () => number {
  return mulberry32(hashSeed(seedStr));
}
