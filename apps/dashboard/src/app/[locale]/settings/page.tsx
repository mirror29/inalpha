/**
 * 用户 LLM 配置设置页面。
 *
 * 功能：
 *  - 多配置列表显示（掩码 key）
 *  - 新增/编辑/删除配置
 *  - 切换激活配置
 *  - 揭示完整 key（需验证）
 *  - 测试连接
 */
"use client";

import { useState, useEffect } from "react";
import type { LLMProvider, UserLLMConfigDisplay } from "@/lib/user-preferences";

interface SettingsResponse {
  configs: UserLLMConfigDisplay[];
  active_config_id?: string;
  preset_base_urls: Partial<Record<LLMProvider, string>>;
}

export default function SettingsPage() {
  const [settings, setSettings] = useState<SettingsResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  // 新增配置表单状态
  const [showAddForm, setShowAddForm] = useState(false);
  const [formData, setFormData] = useState({
    provider: "deepseek" as LLMProvider,
    model: "",
    api_key: "",
    custom_base_url: "",
    custom_provider_name: "",
    label: "",
  });

  // 揭示 key 验证弹窗
  const [revealModal, setRevealModal] = useState<{
    configId: string;
    verification: string;
  } | null>(null);
  const [revealedKey, setRevealedKey] = useState<string | null>(null);

  useEffect(() => {
    fetchSettings();
  }, []);

  async function fetchSettings() {
    try {
      setLoading(true);
      const res = await fetch("/api/user/settings");
      if (!res.ok) throw new Error("Failed to fetch settings");
      const data = await res.json();
      setSettings(data);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Unknown error");
    } finally {
      setLoading(false);
    }
  }

  async function handleAddConfig() {
    try {
      const res = await fetch("/api/user/settings", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(formData),
      });

      if (!res.ok) throw new Error("Failed to add config");

      setShowAddForm(false);
      setFormData({
        provider: "deepseek",
        model: "",
        api_key: "",
        custom_base_url: "",
        custom_provider_name: "",
        label: "",
      });
      await fetchSettings();
    } catch (err) {
      alert(err instanceof Error ? err.message : "Unknown error");
    }
  }

  async function handleActivateConfig(configId: string) {
    try {
      const res = await fetch("/api/user/settings/activate", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ config_id: configId }),
      });

      if (!res.ok) throw new Error("Failed to activate config");
      await fetchSettings();
    } catch (err) {
      alert(err instanceof Error ? err.message : "Unknown error");
    }
  }

  async function handleDeleteConfig(configId: string) {
    if (!confirm("确定要删除这个配置吗？")) return;

    try {
      const res = await fetch(`/api/user/settings/${configId}`, {
        method: "DELETE",
      });

      if (!res.ok) throw new Error("Failed to delete config");
      await fetchSettings();
    } catch (err) {
      alert(err instanceof Error ? err.message : "Unknown error");
    }
  }

  async function handleRevealKey(configId: string, verification: string) {
    try {
      const res = await fetch("/api/user/settings/reveal-key", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ config_id: configId, verification }),
      });

      if (!res.ok) {
        const data = await res.json();
        throw new Error(data.error || "Verification failed");
      }

      const data = await res.json();
      setRevealedKey(data.api_key);
      setRevealModal(null);

      // 30 秒后自动隐藏
      setTimeout(() => setRevealedKey(null), 30000);
    } catch (err) {
      alert(err instanceof Error ? err.message : "Unknown error");
    }
  }

  if (loading) {
    return (
      <div className="flex min-h-screen items-center justify-center">
        <div className="text-lg">加载中...</div>
      </div>
    );
  }

  if (error) {
    return (
      <div className="flex min-h-screen items-center justify-center">
        <div className="text-red-500">错误: {error}</div>
      </div>
    );
  }

  return (
    <div className="container mx-auto max-w-4xl p-6">
      <h1 className="mb-6 text-2xl font-bold">LLM 配置</h1>

      {/* 配置列表 */}
      <div className="mb-6 space-y-4">
        {settings?.configs.map((config) => (
          <div
            key={config.id}
            className={`rounded-lg border p-4 ${
              config.is_active ? "border-blue-500 bg-blue-50" : "border-gray-200"
            }`}
          >
            <div className="flex items-start justify-between">
              <div className="flex-1">
                <div className="mb-2 flex items-center gap-2">
                  <span className="font-semibold">
                    {config.custom_provider_name || config.provider}
                  </span>
                  {config.is_active && (
                    <span className="rounded bg-blue-500 px-2 py-1 text-xs text-white">
                      激活
                    </span>
                  )}
                </div>
                <div className="space-y-1 text-sm text-gray-600">
                  <div>模型: {config.model || "默认"}</div>
                  <div>API Key: {config.api_key_masked}</div>
                  {config.base_url && <div>端点: {config.base_url}</div>}
                  {config.label && <div>标签: {config.label}</div>}
                </div>
              </div>

              <div className="flex gap-2">
                {!config.is_active && (
                  <button
                    onClick={() => handleActivateConfig(config.id)}
                    className="rounded bg-blue-500 px-3 py-1 text-sm text-white hover:bg-blue-600"
                  >
                    激活
                  </button>
                )}
                <button
                  onClick={() => setRevealModal({ configId: config.id, verification: "" })}
                  className="rounded bg-gray-500 px-3 py-1 text-sm text-white hover:bg-gray-600"
                >
                  查看 Key
                </button>
                <button
                  onClick={() => handleDeleteConfig(config.id)}
                  className="rounded bg-red-500 px-3 py-1 text-sm text-white hover:bg-red-600"
                >
                  删除
                </button>
              </div>
            </div>
          </div>
        ))}

        {settings?.configs.length === 0 && (
          <div className="rounded-lg border border-gray-200 p-8 text-center text-gray-500">
            暂无配置，点击下方按钮添加
          </div>
        )}
      </div>

      {/* 揭示的完整 key */}
      {revealedKey && (
        <div className="mb-4 rounded-lg border border-yellow-200 bg-yellow-50 p-4">
          <div className="mb-2 font-semibold text-yellow-800">完整 API Key（30 秒后隐藏）:</div>
          <code className="break-all text-yellow-900">{revealedKey}</code>
        </div>
      )}

      {/* 新增配置按钮 */}
      {!showAddForm && (
        <button
          onClick={() => setShowAddForm(true)}
          className="rounded bg-green-500 px-4 py-2 text-white hover:bg-green-600"
        >
          + 新增配置
        </button>
      )}

      {/* 新增配置表单 */}
      {showAddForm && (
        <div className="rounded-lg border border-gray-200 p-6">
          <h2 className="mb-4 text-lg font-semibold">新增配置</h2>

          <div className="space-y-4">
            <div>
              <label className="mb-1 block text-sm font-medium">供应商</label>
              <select
                value={formData.provider}
                onChange={(e) => setFormData({ ...formData, provider: e.target.value as LLMProvider })}
                className="w-full rounded border border-gray-300 p-2"
              >
                <option value="deepseek">DeepSeek</option>
                <option value="anthropic">Anthropic</option>
                <option value="openai">OpenAI</option>
                <option value="gemini">Gemini</option>
                <option value="kimi">Kimi</option>
                <option value="zhipu">智谱 AI</option>
                <option value="custom">自定义端点</option>
              </select>
            </div>

            {formData.provider === "custom" && (
              <>
                <div>
                  <label className="mb-1 block text-sm font-medium">自定义端点 URL</label>
                  <input
                    type="text"
                    value={formData.custom_base_url}
                    onChange={(e) => setFormData({ ...formData, custom_base_url: e.target.value })}
                    placeholder="https://api.example.com/v1"
                    className="w-full rounded border border-gray-300 p-2"
                  />
                </div>
                <div>
                  <label className="mb-1 block text-sm font-medium">自定义名称</label>
                  <input
                    type="text"
                    value={formData.custom_provider_name}
                    onChange={(e) => setFormData({ ...formData, custom_provider_name: e.target.value })}
                    placeholder="某中转站"
                    className="w-full rounded border border-gray-300 p-2"
                  />
                </div>
              </>
            )}

            <div>
              <label className="mb-1 block text-sm font-medium">模型（可选）</label>
              <input
                type="text"
                value={formData.model}
                onChange={(e) => setFormData({ ...formData, model: e.target.value })}
                placeholder="留空使用默认旗舰模型"
                className="w-full rounded border border-gray-300 p-2"
              />
            </div>

            <div>
              <label className="mb-1 block text-sm font-medium">API Key *</label>
              <input
                type="password"
                value={formData.api_key}
                onChange={(e) => setFormData({ ...formData, api_key: e.target.value })}
                placeholder="sk-..."
                className="w-full rounded border border-gray-300 p-2"
              />
            </div>

            <div>
              <label className="mb-1 block text-sm font-medium">标签（可选）</label>
              <input
                type="text"
                value={formData.label}
                onChange={(e) => setFormData({ ...formData, label: e.target.value })}
                placeholder="DeepSeek 主力"
                className="w-full rounded border border-gray-300 p-2"
              />
            </div>

            <div className="flex gap-2">
              <button
                onClick={handleAddConfig}
                className="rounded bg-blue-500 px-4 py-2 text-white hover:bg-blue-600"
              >
                保存
              </button>
              <button
                onClick={() => setShowAddForm(false)}
                className="rounded bg-gray-300 px-4 py-2 text-gray-700 hover:bg-gray-400"
              >
                取消
              </button>
            </div>
          </div>
        </div>
      )}

      {/* 揭示 key 验证弹窗 */}
      {revealModal && (
        <div className="fixed inset-0 flex items-center justify-center bg-black bg-opacity-50">
          <div className="rounded-lg bg-white p-6">
            <h3 className="mb-4 text-lg font-semibold">验证身份</h3>
            <p className="mb-4 text-sm text-gray-600">请输入登录密码以查看完整 API Key</p>
            <input
              type="password"
              value={revealModal.verification}
              onChange={(e) => setRevealModal({ ...revealModal, verification: e.target.value })}
              placeholder="登录密码"
              className="mb-4 w-full rounded border border-gray-300 p-2"
            />
            <div className="flex gap-2">
              <button
                onClick={() => handleRevealKey(revealModal.configId, revealModal.verification)}
                className="rounded bg-blue-500 px-4 py-2 text-white hover:bg-blue-600"
              >
                确认
              </button>
              <button
                onClick={() => setRevealModal(null)}
                className="rounded bg-gray-300 px-4 py-2 text-gray-700 hover:bg-gray-400"
              >
                取消
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
