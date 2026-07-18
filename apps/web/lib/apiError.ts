type ApiErrorItem = {
  loc?: unknown;
  msg?: unknown;
  message?: unknown;
};

function formatErrorItem(value: unknown): string | null {
  if (typeof value === 'string' && value.trim()) return value.trim();
  if (!value || typeof value !== 'object') return null;

  const item = value as ApiErrorItem;
  const message = typeof item.msg === 'string'
    ? item.msg
    : typeof item.message === 'string'
      ? item.message
      : null;
  if (!message) return null;

  const location = Array.isArray(item.loc)
    ? item.loc.filter((part) => part !== 'body').map(String).join('.')
    : '';
  return location ? `${location}: ${message}` : message;
}

export function apiErrorMessage(text: string, status: number): string {
  const fallback = text || `Request failed with ${status}`;
  try {
    const parsed = JSON.parse(text) as { detail?: unknown; message?: unknown };
    if (typeof parsed.detail === 'string' && parsed.detail.trim()) return parsed.detail;
    if (Array.isArray(parsed.detail)) {
      const messages = parsed.detail.map(formatErrorItem).filter((item): item is string => Boolean(item));
      if (messages.length) return messages.join('; ');
    }
    const detailMessage = formatErrorItem(parsed.detail);
    if (detailMessage) return detailMessage;
    if (typeof parsed.message === 'string' && parsed.message.trim()) return parsed.message;
  } catch {
    // Keep the plain-text fallback.
  }
  return fallback;
}
