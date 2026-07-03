import { cjk } from "@streamdown/cjk";
import { code } from "@streamdown/code";
import { defaultRehypePlugins, type LinkSafetyConfig } from "streamdown";

/**
 * Streamdown 安全配置。
 *
 * 参照 Omnigent 的做法：
 * 1. 禁用远程图片加载（空白名单），防止通过图片 URL 渗出数据
 * 2. 保留其余默认 rehype 插件（sanitize、harden 等）
 */

/** Streamdown 内容插件包。CJK 优化 + 流式代码高亮。 */
export const STREAMDOWN_PLUGINS = { cjk, code } as const;

/** 链接安全：不禁用 target="_blank"，但不弹确认窗。 */
export const CHAT_LINK_SAFETY: LinkSafetyConfig = { enabled: false };

/**
 * 安全的 rehype 插件列表。
 * defaultRehypePlugins 是 Record<string, Pluggable>，key 为插件名。
 * 将 harden 插件的 allowedImagePrefixes 设为空数组，阻断所有远程图片加载。
 */
export function createSecureRehypePlugins() {
  return Object.entries(defaultRehypePlugins).map(
    ([key, plugin]): (typeof defaultRehypePlugins)[string] => {
      if (key !== "harden" || !Array.isArray(plugin)) return plugin;

      const [fn, options] = plugin;
      if (typeof options === "object" && options !== null) {
        return [fn, { ...(options as Record<string, unknown>), allowedImagePrefixes: [] }];
      }
      return plugin;
    },
  );
}
