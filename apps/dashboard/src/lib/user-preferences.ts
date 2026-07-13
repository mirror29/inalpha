/**
 * 用户配置数据访问层 —— 多租户 LLM 配置 CRUD。
 *
 * 功能：
 *  - 多配置存储（不同供应商/同一供应商多 key）
 *  - 激活配置切换
 *  - API key 加密存储
 *  - 揭示完整 key（需二次验证）
 *
 * 数据结构（users.preferences->'llm'）：
 *  {
 *    configs: UserLLMConfig[],
 *    active_config_id: string
 *  }
 */
import "server-only";

import { getPool } from "./db";
import {
  decryptApiKey,
  encryptApiKey,
  maskApiKey,
  type EncryptedData,
} from "./encryption";

/**
 * LLM 供应商类型（移除 ollama）
 */
export type LLMProvider =
  | "deepseek"
  | "anthropic"
  | "openai"
  | "gemini"
  | "kimi"
  | "zhipu"
  | "custom";

/**
 * 用户 LLM 配置（数据库存储）
 */
export interface UserLLMConfig {
  id: string; // 配置唯一标识
  provider: LLMProvider;
  model?: string; // 可选，留空使用默认
  api_key_encrypted: string; // base64
  api_key_nonce: string; // base64
  api_key_tag: string; // base64
  custom_base_url?: string; // 自定义端点（中转站）
  custom_provider_name?: string; // 自定义供应商显示名
  label?: string; // 用户自定义标签
  created_at: string; // ISO 8601
  updated_at: string;
}

/**
 * 用户 LLM 配置输入（前端提交）
 */
export interface UserLLMConfigInput {
  provider: LLMProvider;
  model?: string;
  api_key: string; // 明文，将被加密
  custom_base_url?: string;
  custom_provider_name?: string;
  label?: string;
}

/**
 * 用户 LLM 配置显示（前端响应）
 */
export interface UserLLMConfigDisplay {
  id: string;
  provider: LLMProvider;
  model?: string;
  api_key_masked: string; // 掩码显示
  base_url?: string; // 实际使用的端点（默认或自定义）
  custom_provider_name?: string;
  label?: string;
  is_active: boolean;
  created_at: string;
  updated_at: string;
}

/**
 * 用户 LLM preferences 完整结构
 */
interface UserLLMPreferences {
  configs?: UserLLMConfig[];
  active_config_id?: string;
}

/**
 * 预设供应商默认端点
 */
export const PROVIDER_BASE_URLS: Partial<Record<LLMProvider, string>> = {
  deepseek: "https://api.deepseek.com",
  openai: "https://api.openai.com/v1",
  kimi: "https://api.moonshot.cn/v1",
  zhipu: "https://open.bigmodel.cn/api/paas/v4",
  // anthropic / gemini 使用原生 SDK，不走 OpenAI-compatible
};

/**
 * 生成唯一配置 ID。
 */
function generateConfigId(): string {
  return `cfg-${Date.now()}-${Math.random().toString(36).slice(2, 11)}`;
}

/**
 * 从数据库读取用户 preferences。
 *
 * @param subject 用户 subject
 * @returns 用户 preferences 对象
 */
async function getUserPreferences(subject: string): Promise<UserLLMPreferences> {
  const result = await getPool().query<{ preferences: UserLLMPreferences | null }>(
    "SELECT preferences FROM users WHERE subject = $1",
    [subject],
  );

  const preferences = result.rows[0]?.preferences;
  return preferences ?? {};
}

/**
 * 更新用户 preferences。
 *
 * @param subject 用户 subject
 * @param preferences 新的 preferences 对象
 */
async function updateUserPreferences(
  subject: string,
  preferences: UserLLMPreferences,
): Promise<void> {
  await getPool().query(
    `UPDATE users
     SET preferences = COALESCE(preferences, '{}'::jsonb) || $1::jsonb,
         updated_at = NOW()
     WHERE subject = $2`,
    [JSON.stringify(preferences), subject],
  );
}

/**
 * 获取用户所有 LLM 配置（掩码显示）。
 *
 * @param subject 用户 subject
 * @returns 配置列表 + 激活 ID + 预设端点
 */
export async function getUserLLMConfigs(subject: string): Promise<{
  configs: UserLLMConfigDisplay[];
  active_config_id?: string;
  preset_base_urls: typeof PROVIDER_BASE_URLS;
}> {
  const preferences = await getUserPreferences(subject);
  const configs = preferences.configs || [];
  const activeId = preferences.active_config_id;

  const displayConfigs: UserLLMConfigDisplay[] = await Promise.all(
    configs.map(async (config) => {
      const encrypted: EncryptedData = {
        ciphertext: config.api_key_encrypted,
        nonce: config.api_key_nonce,
        tag: config.api_key_tag,
      };

      // 解密后掩码（用于显示）
      let maskedKey: string;
      try {
        const plaintext = await decryptApiKey(encrypted);
        maskedKey = maskApiKey(plaintext);
      } catch {
        // 解密失败时显示占位符
        maskedKey = "***";
      }

      return {
        id: config.id,
        provider: config.provider,
        model: config.model,
        api_key_masked: maskedKey,
        base_url:
          config.custom_base_url || PROVIDER_BASE_URLS[config.provider],
        custom_provider_name: config.custom_provider_name,
        label: config.label,
        is_active: config.id === activeId,
        created_at: config.created_at,
        updated_at: config.updated_at,
      };
    }),
  );

  return {
    configs: displayConfigs,
    active_config_id: activeId,
    preset_base_urls: PROVIDER_BASE_URLS,
  };
}

/**
 * 新增 LLM 配置。
 *
 * @param subject 用户 subject
 * @param input 配置输入
 * @returns 新配置 ID
 */
export async function addUserLLMConfig(
  subject: string,
  input: UserLLMConfigInput,
): Promise<string> {
  // 加密 API key
  const encrypted = await encryptApiKey(input.api_key);

  const configId = generateConfigId();
  const now = new Date().toISOString();

  const newConfig: UserLLMConfig = {
    id: configId,
    provider: input.provider,
    model: input.model,
    api_key_encrypted: encrypted.ciphertext,
    api_key_nonce: encrypted.nonce,
    api_key_tag: encrypted.tag,
    custom_base_url: input.custom_base_url,
    custom_provider_name: input.custom_provider_name,
    label: input.label,
    created_at: now,
    updated_at: now,
  };

  const preferences = await getUserPreferences(subject);
  const configs = preferences.configs || [];

  // 如果是第一个配置，自动激活
  const isFirst = configs.length === 0;

  const updatedPreferences: UserLLMPreferences = {
    ...preferences,
    configs: [...configs, newConfig],
    active_config_id: isFirst ? configId : preferences.active_config_id,
  };

  await updateUserPreferences(subject, updatedPreferences);

  return configId;
}

/**
 * 更新 LLM 配置。
 *
 * @param subject 用户 subject
 * @param configId 配置 ID
 * @param input 部分配置输入
 */
export async function updateUserLLMConfig(
  subject: string,
  configId: string,
  input: Partial<UserLLMConfigInput>,
): Promise<void> {
  const preferences = await getUserPreferences(subject);
  const configs = preferences.configs || [];

  const index = configs.findIndex((c) => c.id === configId);
  if (index === -1) {
    throw new Error(`Config ${configId} not found`);
  }

  const existing = configs[index];
  const now = new Date().toISOString();

  // 如果更新了 API key，重新加密
  let encrypted: EncryptedData | undefined;
  if (input.api_key) {
    encrypted = await encryptApiKey(input.api_key);
  }

  const updated: UserLLMConfig = {
    ...existing,
    provider: input.provider ?? existing.provider,
    model: input.model ?? existing.model,
    custom_base_url: input.custom_base_url ?? existing.custom_base_url,
    custom_provider_name:
      input.custom_provider_name ?? existing.custom_provider_name,
    label: input.label ?? existing.label,
    updated_at: now,
    ...(encrypted && {
      api_key_encrypted: encrypted.ciphertext,
      api_key_nonce: encrypted.nonce,
      api_key_tag: encrypted.tag,
    }),
  };

  configs[index] = updated;

  await updateUserPreferences(subject, { ...preferences, configs });
}

/**
 * 删除 LLM 配置。
 *
 * @param subject 用户 subject
 * @param configId 配置 ID
 */
export async function deleteUserLLMConfig(
  subject: string,
  configId: string,
): Promise<void> {
  const preferences = await getUserPreferences(subject);
  const configs = preferences.configs || [];

  const filtered = configs.filter((c) => c.id !== configId);
  if (filtered.length === configs.length) {
    throw new Error(`Config ${configId} not found`);
  }

  // 如果删除的是激活配置，自动激活第一个（如果有）
  let activeId = preferences.active_config_id;
  if (activeId === configId) {
    activeId = filtered.length > 0 ? filtered[0].id : undefined;
  }

  await updateUserPreferences(subject, {
    ...preferences,
    configs: filtered,
    active_config_id: activeId,
  });
}

/**
 * 切换激活配置。
 *
 * @param subject 用户 subject
 * @param configId 配置 ID
 */
export async function activateUserLLMConfig(
  subject: string,
  configId: string,
): Promise<void> {
  const preferences = await getUserPreferences(subject);
  const configs = preferences.configs || [];

  if (!configs.find((c) => c.id === configId)) {
    throw new Error(`Config ${configId} not found`);
  }

  await updateUserPreferences(subject, {
    ...preferences,
    active_config_id: configId,
  });
}

/**
 * 解密激活配置的 API key（内部使用，不暴露给前端）。
 *
 * @param subject 用户 subject
 * @returns 解密后的完整配置（含明文 API key），或 null
 */
export async function decryptActiveUserApiKey(
  subject: string,
): Promise<(UserLLMConfig & { api_key: string }) | null> {
  const preferences = await getUserPreferences(subject);
  const configs = preferences.configs || [];
  const activeId = preferences.active_config_id;

  if (!configs.length || !activeId) {
    return null;
  }

  const active = configs.find((c) => c.id === activeId);
  if (!active) {
    return null;
  }

  // 解密 API key
  const encrypted: EncryptedData = {
    ciphertext: active.api_key_encrypted,
    nonce: active.api_key_nonce,
    tag: active.api_key_tag,
  };

  const apiKey = await decryptApiKey(encrypted);

  return { ...active, api_key: apiKey };
}
