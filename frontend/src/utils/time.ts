/** 时间格式化工具（从 legacy formatTs 迁移）。 */

/** 把时间戳（秒或毫秒）格式化为 `YYYY-MM-DD HH:mm`；非法值返回 "-"。 */
export function formatTs(ts: number | null | undefined): string {
  if (ts == null) return "-";
  const n = Number(ts);
  if (Number.isNaN(n)) return "-";
  // 兼容秒级时间戳（< 1e12 视为秒）
  const ms = n < 1e12 ? n * 1000 : n;
  const d = new Date(ms);
  if (Number.isNaN(d.getTime())) return "-";
  const pad = (x: number): string => String(x).padStart(2, "0");
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())} ${pad(d.getHours())}:${pad(d.getMinutes())}`;
}
