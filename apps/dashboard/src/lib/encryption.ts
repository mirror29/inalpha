/**
 * API Key 加密服务 —— AES-256-GCM 加密存储用户 LLM API Key。
 *
 * 安全设计：
 *  - 加密算法：AES-256-GCM（保密性 + 完整性 + 认证）
 *  - 密钥来源：环境变量 LLM_CONFIG_ENCRYPTION_KEY（32 字节 hex）
 *  - 降级：未配置时从 JWT_SECRET 派生（HKDF-SHA256）
 *  - API key 明文永不日志记录、永不在 API 响应中暴露
 *  - 解密后的 key 仅存在于请求级内存中
 *
 * 使用示例：
 *  const encrypted = encryptApiKey('sk-test-key-12345');
 *  // { ciphertext: '...', nonce: '...', tag: '...' }
 *  const plaintext = decryptApiKey(encrypted);
 *  // 'sk-test-key-12345'
 */
import "server-only";

const ALGORITHM = "AES-GCM";
const KEY_LENGTH = 256; // bits
const NONCE_LENGTH = 12; // bytes (96 bits, recommended for GCM)
const TAG_LENGTH = 16; // bytes (128 bits)

/**
 * 加密数据结构（存储到数据库）
 */
export interface EncryptedData {
  ciphertext: string; // base64 编码的密文
  nonce: string; // base64 编码的 12-byte nonce
  tag: string; // base64 编码的 16-byte auth tag
}

/**
 * 从环境变量加载加密密钥。
 *
 * 优先级：
 *  1. LLM_CONFIG_ENCRYPTION_KEY（推荐，独立密钥）
 *  2. 从 JWT_SECRET 派生（降级，兼容未配置场景）
 *
 * @returns 32 字节的密钥（Uint8Array）
 */
async function getEncryptionKey(): Promise<CryptoKey> {
  const keyHex =
    process.env.LLM_CONFIG_ENCRYPTION_KEY || process.env.JWT_SECRET;

  if (!keyHex) {
    throw new Error(
      "LLM_CONFIG_ENCRYPTION_KEY 或 JWT_SECRET 未配置。请在根目录 .env 填入。",
    );
  }

  // 如果是 hex 字符串（64 字符 = 32 字节），直接转换为 Uint8Array
  let keyBytes: Uint8Array;
  if (/^[0-9a-fA-F]{64}$/.test(keyHex)) {
    // Hex 编码
    keyBytes = new Uint8Array(
      keyHex.match(/.{1,2}/g)!.map((byte) => parseInt(byte, 16)),
    );
  } else {
    // 非十六进制，使用 HKDF-SHA256 派生
    const encoder = new TextEncoder();
    const rawKey = encoder.encode(keyHex);
    const salt = encoder.encode("inalpha-llm-config-encryption-v1");

    // HKDF-SHA256 派生 32 字节密钥
    const baseKey = await crypto.subtle.importKey(
      "raw",
      rawKey,
      { name: "HKDF" },
      false,
      ["deriveBits"],
    );

    const derivedBits = await crypto.subtle.deriveBits(
      {
        name: "HKDF",
        hash: "SHA-256",
        salt,
        info: encoder.encode("aes-256-gcm-key"),
      },
      baseKey,
      KEY_LENGTH,
    );

    keyBytes = new Uint8Array(derivedBits);
  }

  // 导入为 CryptoKey 对象
  return crypto.subtle.importKey(
    "raw",
    keyBytes,
    { name: ALGORITHM },
    false, // 不可导出
    ["encrypt", "decrypt"],
  );
}

/**
 * 加密 API Key。
 *
 * @param plaintext API key 明文
 * @returns 加密数据（密文、nonce、auth tag）
 */
export async function encryptApiKey(plaintext: string): Promise<EncryptedData> {
  const key = await getEncryptionKey();
  const encoder = new TextEncoder();

  // 生成随机 nonce（12 bytes）
  const nonce = crypto.getRandomValues(new Uint8Array(NONCE_LENGTH));

  // 加密
  const encrypted = await crypto.subtle.encrypt(
    {
      name: ALGORITHM,
      iv: nonce,
      tagLength: TAG_LENGTH * 8, // bits
    },
    key,
    encoder.encode(plaintext),
  );

  // 提取密文和 auth tag
  const encryptedBytes = new Uint8Array(encrypted);
  const ciphertext = encryptedBytes.slice(0, -TAG_LENGTH);
  const tag = encryptedBytes.slice(-TAG_LENGTH);

  // Base64 编码
  const toBase64 = (bytes: Uint8Array) =>
    Buffer.from(bytes).toString("base64");

  return {
    ciphertext: toBase64(ciphertext),
    nonce: toBase64(nonce),
    tag: toBase64(tag),
  };
}

/**
 * 解密 API Key。
 *
 * @param encrypted 加密数据（密文、nonce、auth tag）
 * @returns API key 明文
 */
export async function decryptApiKey(encrypted: EncryptedData): Promise<string> {
  const key = await getEncryptionKey();

  // Base64 解码
  const fromBase64 = (str: string) =>
    new Uint8Array(Buffer.from(str, "base64"));

  const nonce = fromBase64(encrypted.nonce);
  const ciphertext = fromBase64(encrypted.ciphertext);
  const tag = fromBase64(encrypted.tag);

  // 验证 nonce 长度
  if (nonce.length !== NONCE_LENGTH) {
    throw new Error(
      `Invalid nonce length: expected ${NONCE_LENGTH}, got ${nonce.length}`,
    );
  }

  // 合并密文和 auth tag（Web Crypto API 要求）
  const encryptedData = new Uint8Array(ciphertext.length + tag.length);
  encryptedData.set(ciphertext, 0);
  encryptedData.set(tag, ciphertext.length);

  // 解密
  try {
    const decrypted = await crypto.subtle.decrypt(
      {
        name: ALGORITHM,
        iv: nonce,
        tagLength: TAG_LENGTH * 8, // bits
      },
      key,
      encryptedData, // Uint8Array is valid BufferSource
    );

    const decoder = new TextDecoder();
    return decoder.decode(decrypted);
  } catch (err) {
    throw new Error(
      `Decryption failed: ${err instanceof Error ? err.message : String(err)}`,
    );
  }
}

/**
 * 生成掩码 API Key（用于前端显示）。
 *
 * 规则：
 *  - key ≤ 8 字符：显示 `***`
 *  - key > 8 字符：显示前 4 + `***` + 后 4
 *
 * @param key API key 明文
 * @returns 掩码后的字符串
 */
export function maskApiKey(key: string): string {
  if (key.length <= 8) {
    return "***";
  }
  return `${key.slice(0, 4)}***${key.slice(-4)}`;
}
