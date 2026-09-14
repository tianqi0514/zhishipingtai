const API_ROOT = '/api/v1';

export function apiErrorMessage(detail: unknown, fallback = '请求处理失败'): string {
  if (typeof detail === 'string') return detail;
  if (detail && typeof detail === 'object' && 'issues' in detail && Array.isArray(detail.issues)) {
    // Only display the user-facing message, never serialize diagnostic payloads.
    const issue = detail.issues.find((item: unknown) => item && typeof item === 'object'
      && 'message' in item && typeof item.message === 'string' && item.message.trim());
    if (issue) return issue.message.trim();
  }
  return fallback;
}

export class ApiError extends Error {
  status: number;
  detail: unknown;

  constructor(status: number, detail: unknown) {
    super(apiErrorMessage(detail));
    this.status = status;
    this.detail = detail;
  }
}

type ApiRequestInit = Omit<RequestInit, 'body'> & { body?: unknown };

export async function api<T>(path: string, init: ApiRequestInit = {}): Promise<T> {
  const headers = new Headers(init.headers);
  let body = init.body as BodyInit | undefined;
  if (init.body !== undefined && !(init.body instanceof FormData)) {
    headers.set('Content-Type', 'application/json');
    body = JSON.stringify(init.body);
  }
  const response = await fetch(`${API_ROOT}${path}`, {
    ...init,
    body,
    headers,
    credentials: 'same-origin',
  });
  const contentType = response.headers.get('content-type') || '';
  const payload = contentType.includes('application/json') ? await response.json() : await response.text();
  if (!response.ok) {
    if (response.status === 401) window.location.href = '/';
    throw new ApiError(response.status, payload?.detail ?? payload);
  }
  return payload as T;
}
