// localStorage 安全封装：在模块初始化时探测一次真实 localStorage 可用性，
// 避免在 sandboxed iframe 中每次读写都反复触发 SecurityError 异常开销；
// 不可用或写入配额超限时自动降级到内存 Map。

const memoryFallback = new Map<string, string>();

function probeRealStorage(): boolean {
  if (typeof window === "undefined") return false;
  try {
    const probe = "__memorix_storage_probe__";
    window.localStorage.setItem(probe, "1");
    window.localStorage.removeItem(probe);
    return true;
  } catch {
    return false;
  }
}

const STORAGE_AVAILABLE = probeRealStorage();

/** 当前环境是否可安全访问真实 localStorage（模块初始化时探测并缓存）。 */
export function realStorageAvailable(): boolean {
  return STORAGE_AVAILABLE;
}

export const safeStorage = {
  getItem(key: string): string | null {
    if (STORAGE_AVAILABLE) {
      try {
        const val = window.localStorage.getItem(key);
        if (val !== null) return val;
      } catch {
        // 降级读取内存兜底
      }
    }
    return memoryFallback.has(key) ? memoryFallback.get(key)! : null;
  },

  setItem(key: string, value: string): void {
    if (STORAGE_AVAILABLE) {
      try {
        window.localStorage.setItem(key, value);
        memoryFallback.delete(key);
        return;
      } catch {
        // 配额满或受限，降级写入内存兜底
      }
    }
    memoryFallback.set(key, value);
  },

  removeItem(key: string): void {
    if (STORAGE_AVAILABLE) {
      try {
        window.localStorage.removeItem(key);
      } catch {
        // 忽略，继续清理内存兜底
      }
    }
    memoryFallback.delete(key);
  },
};

/** JSON 读写糖：失败返回 fallback，不抛错。 */
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
