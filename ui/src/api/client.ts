import type { Envelope, ErrorDetail } from './types';

export class ApiError extends Error {
  code: string;

  constructor(detail: ErrorDetail) {
    super(detail.message);
    this.code = detail.code;
  }
}

export interface Paged<T> {
  data: T;
  pagination: { page: number; size: number; total: number } | null;
}

async function unwrap<T>(res: Response): Promise<Paged<T>> {
  let body: Envelope<T>;
  try {
    body = await res.json();
  } catch {
    throw new ApiError({ code: 'BAD_RESPONSE', message: `${res.status} ${res.statusText}` });
  }
  if (!res.ok || body.error) {
    throw new ApiError(body.error ?? { code: String(res.status), message: res.statusText });
  }
  return { data: body.data, pagination: body.pagination };
}

export async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(`/api${path}`, {
    headers: { 'Content-Type': 'application/json', ...init?.headers },
    ...init,
  });
  const { data } = await unwrap<T>(res);
  return data;
}

export async function requestPaged<T>(path: string, init?: RequestInit): Promise<Paged<T>> {
  const res = await fetch(`/api${path}`, {
    headers: { 'Content-Type': 'application/json', ...init?.headers },
    ...init,
  });
  return unwrap<T>(res);
}

export async function requestMultipart<T>(path: string, formData: FormData): Promise<T> {
  const res = await fetch(`/api${path}`, { method: 'POST', body: formData });
  const { data } = await unwrap<T>(res);
  return data;
}

/** Builds a query string from filter/pagination params, skipping null/undefined/empty. */
export function qs(params: Record<string, string | number | boolean | null | undefined>): string {
  const search = new URLSearchParams();
  for (const [key, value] of Object.entries(params)) {
    if (value === null || value === undefined || value === '') continue;
    search.set(key, String(value));
  }
  const str = search.toString();
  return str ? `?${str}` : '';
}
