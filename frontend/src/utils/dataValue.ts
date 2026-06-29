/**
 * data-* 属性对称编解码。
 *
 * 修复 legacy 非对称：legacy dataValue = escapeHtml(encodeURIComponent(x))，
 * readDataValue = decodeURIComponent —— 解码端未还原 escapeHtml，仅因 escapeHtml
 * 对 encodeURIComponent 输出的 %xx 是 no-op 才"凑巧"对称；一旦源值含被 escapeHtml
 * 改写的字符就会失配。新实现去掉多余的 escapeHtml，编解码严格对称。
 *
 * Vue 模板内优先用事件直传对象（彻底规避该编码），本工具仅用于少数确需把标量
 * 塞进 data-* 的场景。
 */
export function encodeDataValue(value: unknown): string {
  return encodeURIComponent(String(value ?? ""));
}

export function decodeDataValue(value: string | undefined | null): string {
  return decodeURIComponent(String(value ?? ""));
}
