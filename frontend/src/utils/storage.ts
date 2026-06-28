// localStorage 安全封装：修复 legacy H7 —— legacy 中 storage 不可用时永久置 null 不再重试。
// 本实现：每次访问重新尝试真实 localStorage（重试探针），失败降级内存 Map，
// 即使用户清配额后恢复，下次访问自动回到真实 storage，无需刷新页面。

const memoryFallback = new Map<string, string>();

/** 当前是否可安全使用真实 localStorage（每次调用重试，不缓存"不可用"结论）。 */
export function realStorageAvailable(): boolean {
  try {
    const probe = "__memorix_storage_probe__";
    window.localStorage.setItem(probe, "1");
    window.localStorage.removeItem(probe);
    return true;
  } catch {
    return false;
  }
}

export const safeStorage = {
  getItem(key: string): string | null {
    if (realStorageAvailable()) {
      try {
        return window.localStorage.getItem(key);
      } catch {
        // 落到内存兜底
      }
    }
    return memoryFallback.has(key) ? memoryFallback.get(key)! : null;
  },

  setItem(key: string, value: string): void {
    if (realStorageAvailable()) {
      try {
        window.localStorage.setItem(key, value);
        return;
      } catch {
        // 配额满或被禁用，落到内存兜底
      }
    }
    memoryFallback.set(key, value);
  },

  removeItem(key: string): void {
    if (realStorageAvailable()) {
      try {
        window.localStorage.removeItem(key);
      } catch {
        // 忽略，继续清内存
      }
    }
    memoryFallback.delete(key);
  },
};

/** JSON 读写糖：失败返回 default，不抛错。 */
export function readJSON<T>(key: string, fallback: T): T {
  const raw = safeStorage.getItem(key);
  if (!raw) return fallback;
  try {
    return JSON.parse(raw) as T;
  } catch {
    return fallback;
  }
}

export function writeJSON(key: string, value: unknown): void {
  try {
    safeStorage.setItem(key, JSON.stringify(value));
  } catch {
    // 序列化失败（循环引用等）静默忽略，避免阻塞 UI
  }
}
