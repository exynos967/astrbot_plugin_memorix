/**
 * 统一错误文案提取（DRY：原 9 个 store + api.ts 各自定义同款实现）。
 * Error 取 message，其余 stringify，null/undefined 回退"未知错误"。
 */
export function errText(err: unknown): string {
  if (err instanceof Error) return err.message;
  return String(err ?? "未知错误");
}
