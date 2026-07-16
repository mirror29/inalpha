/**
 * LLM 配置弹窗 —— 使用 shadcn Dialog 组件
 */
"use client";

import { useState, useEffect, useCallback } from "react";
import { Plus, Trash2, Key, Settings, AlertTriangle } from "lucide-react";
import type { LLMProvider, UserLLMConfigDisplay } from "@/lib/user-preferences";
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
} from "@/components/ui/alert-dialog";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Select } from "@/components/ui/select";
import { toast } from "@/components/ui/sonner";

interface SettingsResponse {
  configs: UserLLMConfigDisplay[];
  active_config_id?: string;
  preset_base_urls: Partial<Record<LLMProvider, string>>;
}

const LS_DISMISSED = "inalpha-llm-config-dismissed";

/**
 * 清除「不再自动弹出」标记（供侧边栏调用）。
 */
export function clearLLMConfigDismissed(): void {
  localStorage.removeItem(LS_DISMISSED);
}

export function LLMConfigModal({ open, onClose }: { open: boolean; onClose: () => void }) {
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
  const [saving, setSaving] = useState(false);

  // 删除确认弹窗
  const [deleteTarget, setDeleteTarget] = useState<UserLLMConfigDisplay | null>(null);
  const [deleting, setDeleting] = useState(false);

  const fetchSettings = useCallback(async () => {
    try {
      setLoading(true);
      setError(null);
      const res = await fetch("/api/user/settings");
      if (!res.ok) throw new Error("Failed to fetch settings");
      const data = await res.json();
      setSettings(data);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Unknown error");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    if (open) {
      fetchSettings();
      setShowAddForm(false);
      setDeleteTarget(null);
    }
  }, [open, fetchSettings]);

  async function handleAddConfig() {
    if (!formData.api_key.trim()) return;
    setSaving(true);
    try {
      const res = await fetch("/api/user/settings", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(formData),
      });
      if (!res.ok) throw new Error("保存失败");
      const { id } = (await res.json()) as { id?: string };
      if (!id) throw new Error("保存失败：未返回配置 ID");
      const activateRes = await fetch("/api/user/settings/activate", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ config_id: id }),
      });
      if (!activateRes.ok) throw new Error("配置已保存，但激活失败");
      setShowAddForm(false);
      setFormData({ provider: "deepseek", model: "", api_key: "", custom_base_url: "", custom_provider_name: "", label: "" });
      await fetchSettings();
      toast.success("配置已保存");
    } catch (err) {
      toast.error(err instanceof Error ? err.message : "保存失败");
    } finally {
      setSaving(false);
    }
  }

  async function handleActivateConfig(configId: string) {
    // 先本地更新状态，避免闪烁
    setSettings(prev => {
      if (!prev) return prev;
      return {
        ...prev,
        configs: prev.configs.map(c => ({
          ...c,
          is_active: c.id === configId,
        })),
        active_config_id: configId,
      };
    });

    try {
      await fetch("/api/user/settings/activate", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ config_id: configId }),
      });
      toast.success("已切换配置");
    } catch {
      // 失败时回滚
      fetchSettings();
      toast.error("切换失败");
    }
  }

  async function handleDeleteConfig() {
    if (!deleteTarget) return;
    setDeleting(true);
    try {
      const res = await fetch(`/api/user/settings/${deleteTarget.id}`, { method: "DELETE" });
      if (!res.ok) throw new Error("删除失败");
      setDeleteTarget(null);
      await fetchSettings();
      toast.success("配置已删除");
    } catch (err) {
      toast.error(err instanceof Error ? err.message : "删除失败");
    } finally {
      setDeleting(false);
    }
  }

  return (
    <>
      <Dialog open={open} onOpenChange={(v) => !v && onClose()}>
        <DialogContent className="max-w-lg max-h-[80vh] overflow-y-auto">
          <DialogHeader>
            <DialogTitle className="flex items-center gap-2">
              <Settings className="size-4 text-cyan" strokeWidth={1.75} />
              LLM 配置
            </DialogTitle>
          </DialogHeader>

          {loading && (
            <div className="flex items-center justify-center py-8">
              <div className="text-sm text-fg-muted">加载中...</div>
            </div>
          )}

          {error && (
            <div className="rounded-md border border-fox-red/30 bg-fox-red/10 px-4 py-3 text-sm text-fox-red">
              加载失败: {error}
            </div>
          )}

          {!loading && !error && (
            <div className="flex flex-col gap-4">
              {/* 配置列表 */}
              <div className="space-y-3">
                {settings?.configs.map((config) => (
                  <button
                    key={config.id}
                    type="button"
                    onClick={() => !config.is_active && handleActivateConfig(config.id)}
                    className={`w-full rounded-lg border px-4 py-3 text-left transition-colors ${
                      config.is_active
                        ? "border-cyan/40 bg-cyan/[0.06] cursor-default"
                        : "border-border-subtle hover:border-cyan/30 hover:bg-cyan/[0.02] cursor-pointer"
                    }`}
                  >
                    <div className="flex items-start justify-between gap-2">
                      <div className="min-w-0 flex-1">
                        <div className="mb-1 flex items-center gap-2">
                          <span className="text-sm font-medium text-fg">
                            {config.custom_provider_name || config.provider}
                          </span>
                          {config.is_active && (
                            <span className="rounded-full bg-cyan/15 px-2 py-0.5 text-[10px] font-medium uppercase tracking-wider text-cyan">
                              当前
                            </span>
                          )}
                        </div>
                        <div className="space-y-0.5 text-xs text-fg-muted">
                          <div>模型: {config.model || "默认"}</div>
                          <div>Key: {config.api_key_masked}</div>
                        </div>
                      </div>

                      <div className="flex shrink-0 gap-1.5">
                        <Button
                          variant="ghost"
                          size="icon"
                          onClick={(e) => {
                            e.stopPropagation();
                            setDeleteTarget(config);
                          }}
                          title="删除"
                          className="size-7 hover:text-fox-red hover:bg-fox-red/10"
                        >
                          <Trash2 className="size-3.5" strokeWidth={1.75} />
                        </Button>
                      </div>
                    </div>
                  </button>
                ))}

                {(!settings || settings.configs.length === 0) && (
                  <div className="flex flex-col items-center gap-3 rounded-lg border border-dashed border-border-subtle py-8">
                    <Key className="size-8 text-fg-muted/40" strokeWidth={1.5} />
                    <p className="text-sm text-fg-muted">暂无配置，请添加 API Key</p>
                  </div>
                )}
              </div>

              {/* 新增配置表单 */}
              {showAddForm ? (
                <div className="space-y-4 rounded-lg border border-border-subtle p-4">
                  <div className="space-y-2">
                    <Label htmlFor="provider">供应商</Label>
                    <Select
                      id="provider"
                      value={formData.provider}
                      onChange={(e) => setFormData({ ...formData, provider: e.target.value as LLMProvider })}
                    >
                      <option value="deepseek">DeepSeek</option>
                      <option value="anthropic">Anthropic</option>
                      <option value="openai">OpenAI</option>
                      <option value="gemini">Gemini</option>
                      <option value="kimi">Kimi</option>
                      <option value="zhipu">智谱 AI</option>
                      <option value="custom">自定义端点</option>
                    </Select>
                  </div>

                  {formData.provider === "custom" && (
                    <>
                      <div className="space-y-2">
                        <Label htmlFor="custom_base_url">自定义端点 URL</Label>
                        <Input
                          id="custom_base_url"
                          type="text"
                          value={formData.custom_base_url}
                          onChange={(e) => setFormData({ ...formData, custom_base_url: e.target.value })}
                          placeholder="https://api.example.com/v1"
                        />
                      </div>
                      <div className="space-y-2">
                        <Label htmlFor="custom_provider_name">自定义名称</Label>
                        <Input
                          id="custom_provider_name"
                          type="text"
                          value={formData.custom_provider_name}
                          onChange={(e) => setFormData({ ...formData, custom_provider_name: e.target.value })}
                          placeholder="某中转站"
                        />
                      </div>
                    </>
                  )}

                  <div className="space-y-2">
                    <Label htmlFor="model">模型（可选）</Label>
                    <Input
                      id="model"
                      type="text"
                      value={formData.model}
                      onChange={(e) => setFormData({ ...formData, model: e.target.value })}
                      placeholder="留空使用默认"
                    />
                  </div>

                  <div className="space-y-2">
                    <Label htmlFor="api_key">API Key *</Label>
                    <Input
                      id="api_key"
                      type="password"
                      value={formData.api_key}
                      onChange={(e) => setFormData({ ...formData, api_key: e.target.value })}
                      placeholder="sk-..."
                    />
                  </div>

                  <div className="flex gap-2 pt-2">
                    <Button onClick={handleAddConfig} disabled={saving || !formData.api_key.trim()}>
                      {saving ? "保存中..." : "保存"}
                    </Button>
                    <Button variant="outline" onClick={() => setShowAddForm(false)}>
                      取消
                    </Button>
                  </div>
                </div>
              ) : (
                <Button variant="outline" onClick={() => setShowAddForm(true)} className="w-full border-dashed">
                  <Plus className="size-4" strokeWidth={1.75} />
                  新增配置
                </Button>
              )}
            </div>
          )}
        </DialogContent>
      </Dialog>

      {/* 删除确认弹窗 */}
      <AlertDialog open={!!deleteTarget} onOpenChange={(v) => !v && setDeleteTarget(null)}>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle className="flex items-center gap-2">
              <AlertTriangle className="size-5 text-fox-red" strokeWidth={1.75} />
              确认删除
            </AlertDialogTitle>
            <AlertDialogDescription>
              确定要删除配置 <span className="font-medium text-fg">{deleteTarget?.custom_provider_name || deleteTarget?.provider}</span> 吗？
              <br />
              <span className="text-xs">此操作无法撤销。</span>
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel>取消</AlertDialogCancel>
            <AlertDialogAction onClick={handleDeleteConfig} disabled={deleting}>
              {deleting ? "删除中..." : "删除"}
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </>
  );
}
