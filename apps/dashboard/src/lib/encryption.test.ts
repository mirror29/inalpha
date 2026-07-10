/**
 * 加密服务单元测试。
 */
import { describe, it, expect } from "vitest";
import { encryptApiKey, decryptApiKey, maskApiKey } from "./encryption";

describe("Encryption Service", () => {
  it("should encrypt and decrypt correctly", async () => {
    const plaintext = "sk-test-key-1234567890";

    const encrypted = await encryptApiKey(plaintext);

    // 密文应该与明文不同
    expect(encrypted.ciphertext).not.toBe(plaintext);
    // nonce 应该是 base64 编码的 12 字节
    expect(encrypted.nonce).toBeTruthy();
    // tag 应该是 base64 编码的 16 字节
    expect(encrypted.tag).toBeTruthy();

    // 解密应该得到原始明文
    const decrypted = await decryptApiKey(encrypted);
    expect(decrypted).toBe(plaintext);
  });

  it("should produce different ciphertext for same plaintext (random nonce)", async () => {
    const plaintext = "sk-same-key";

    const encrypted1 = await encryptApiKey(plaintext);
    const encrypted2 = await encryptApiKey(plaintext);

    // 相同明文加密两次，密文应该不同（因为 nonce 随机）
    expect(encrypted1.ciphertext).not.toBe(encrypted2.ciphertext);
    expect(encrypted1.nonce).not.toBe(encrypted2.nonce);

    // 但解密后都应该得到原始明文
    const decrypted1 = await decryptApiKey(encrypted1);
    const decrypted2 = await decryptApiKey(encrypted2);
    expect(decrypted1).toBe(plaintext);
    expect(decrypted2).toBe(plaintext);
  });

  it("should fail decryption with wrong key", async () => {
    const plaintext = "sk-wrong-key-test";
    const encrypted = await encryptApiKey(plaintext);

    // 临时修改环境变量（模拟密钥变化）
    const originalKey = process.env.LLM_CONFIG_ENCRYPTION_KEY;
    process.env.LLM_CONFIG_ENCRYPTION_KEY = "different-32-byte-key-1234567890";

    try {
      await decryptApiKey(encrypted);
      // 如果没有抛出错误，测试失败
      expect(true).toBe(false);
    } catch (err) {
      // 应该抛出解密失败错误
      expect(err).toBeInstanceOf(Error);
      expect((err as Error).message).toContain("Decryption failed");
    } finally {
      // 恢复原始密钥
      process.env.LLM_CONFIG_ENCRYPTION_KEY = originalKey;
    }
  });

  it("should mask API key correctly", () => {
    // 长于 8 字符的 key
    expect(maskApiKey("sk-1234567890abcdef")).toBe("sk-1***cdef");

    // 等于 8 字符的 key
    expect(maskApiKey("12345678")).toBe("1234***5678");

    // 短于 8 字符的 key
    expect(maskApiKey("short")).toBe("***");
  });
});

describe("User Preferences", () => {
  // TODO: 添加用户配置 CRUD 测试（需要 mock 数据库）
});
