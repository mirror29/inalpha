/**
 * LLM 配置弹窗 —— 使用 shadcn Dialog 组件
 */
"use client";

import { useTranslations } from "next-intl";
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
import { Select, SelectTrigger, SelectValue, SelectContent, SelectItem } from "@/components/ui/select";
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
  const t = useTranslations("llm");
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
      if (!res.ok) throw new Error(t("loadFailed", { error: res.status }));
      const data = await res.json();
      setSettings(data);
    } catch (err) {
      setError(err instanceof Error ? err.message : t("loadFailed", { error: "unknown" }));
    } finally {
      setLoading(false);
    }
  }, [t]);

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
      if (!res.ok) throw new Error(t("saveFailed"));
      const { id } = (await res.json()) as { id?: string };
      if (!id) throw new Error(t("saveMissingId"));
      const activateRes = await fetch("/api/user/settings/activate", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ config_id: id }),
      });
      if (!activateRes.ok) throw new Error(t("saveActivationFailed"));
      setShowAddForm(false);
      setFormData({ provider: "deepseek", model: "", api_key: "", custom_base_url: "", custom_provider_name: "", label: "" });
      await fetchSettings();
      toast.success(t("saved"));
    } catch (err) {
      toast.error(err instanceof Error ? err.message : t("saveFailed"));
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
      toast.success(t("activated"));
    } catch {
      // 失败时回滚
      fetchSettings();
      toast.error(t("activationFailed"));
    }
  }

  async function handleDeleteConfig() {
    if (!deleteTarget) return;
    setDeleting(true);
    try {
      const res = await fetch(`/api/user/settings/${deleteTarget.id}`, { method: "DELETE" });
      if (!res.ok) throw new Error(t("deleteFailed"));
      setDeleteTarget(null);
      await fetchSettings();
      toast.success(t("deleted"));
    } catch (err) {
      toast.error(err instanceof Error ? err.message : t("deleteFailed"));
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
              {t("title")}
            </DialogTitle>
          </DialogHeader>

          {loading && (
            <div className="flex items-center justify-center py-8">
              <div className="text-sm text-fg-muted">{t("loading")}</div>
            </div>
          )}

          {error && (
            <div className="rounded-md border border-fox-red/30 bg-fox-red/10 px-4 py-3 text-sm text-fox-red">
              {error}
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
                              {t("active")}
                            </span>
                          )}
                        </div>
                        <div className="space-y-0.5 text-xs text-fg-muted">
                          <div>{t("model", { model: config.model || t("defaultModel") })}</div>
                          <div>{t("key", { key: config.api_key_masked })}</div>
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
                          title={t("delete")}
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
                    <p className="text-sm text-fg-muted">{t("noConfigs")}</p>
                  </div>
                )}
              </div>

              {/* 新增配置表单 */}
              {showAddForm ? (
                <div className="space-y-4 rounded-lg border border-border-subtle p-4">
                  <div className="space-y-2">
                    <Label htmlFor="provider">{t("provider")}</Label>
                    <Select
                      value={formData.provider}
                      onValueChange={(value) => setFormData({ ...formData, provider: value as LLMProvider })}
                    >
                      <SelectTrigger id="provider"><SelectValue /></SelectTrigger>
                      <SelectContent>
                        <SelectItem value="deepseek">DeepSeek</SelectItem>
                        <SelectItem value="anthropic">Anthropic</SelectItem>
                        <SelectItem value="openai">OpenAI</SelectItem>
                        <SelectItem value="gemini">Gemini</SelectItem>
                        <SelectItem value="kimi">Kimi</SelectItem>
                        <SelectItem value="zhipu">智谱 AI</SelectItem>
                        <SelectItem value="custom">{t("customProvider")}</SelectItem>
                      </SelectContent>
                    </Select>
                  </div>

                  {formData.provider === "custom" && (
                    <>
                      <div className="space-y-2">
                        <Label htmlFor="custom_base_url">{t("customEndpoint")}</Label>
                        <Input
                          id="custom_base_url"
                          type="text"
                          value={formData.custom_base_url}
                          onChange={(e) => setFormData({ ...formData, custom_base_url: e.target.value })}
                          placeholder="https://api.example.com/v1"
                        />
                      </div>
                      <div className="space-y-2">
                        <Label htmlFor="custom_provider_name">{t("customName")}</Label>
                        <Input
                          id="custom_provider_name"
                          type="text"
                          value={formData.custom_provider_name}
                          onChange={(e) => setFormData({ ...formData, custom_provider_name: e.target.value })}
                          placeholder={t("customNamePlaceholder")}
                        />
                      </div>
                    </>
                  )}

                  <div className="space-y-2">
                    <Label htmlFor="model">{t("modelOptional")}</Label>
                    <Input
                      id="model"
                      type="text"
                      value={formData.model}
                      onChange={(e) => setFormData({ ...formData, model: e.target.value })}
                      placeholder={t("modelPlaceholder")}
                    />
                  </div>

                  <div className="space-y-2">
                    <Label htmlFor="api_key">{t("apiKey")}</Label>
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
                      {saving ? t("saving") : t("save")}
                    </Button>
                    <Button variant="outline" onClick={() => setShowAddForm(false)}>
                      {t("cancel")}
                    </Button>
                  </div>
                </div>
              ) : (
                <Button variant="outline" onClick={() => setShowAddForm(true)} className="w-full border-dashed">
                  <Plus className="size-4" strokeWidth={1.75} />
                  {t("addConfig")}
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
              {t("confirmDelete")}
            </AlertDialogTitle>
            <AlertDialogDescription>
              {t("deleteDescription", {
                provider: deleteTarget?.custom_provider_name || deleteTarget?.provider || "",
              })}
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel>{t("cancel")}</AlertDialogCancel>
            <AlertDialogAction onClick={handleDeleteConfig} disabled={deleting}>
              {deleting ? t("deleting") : t("delete")}
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </>
  );
}
