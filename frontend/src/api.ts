// 後端 API 呼叫（同源；開發時由 Vite proxy 轉到 :8000）
/* eslint-disable @typescript-eslint/no-explicit-any */

export class ApiError extends Error {
  status: number;
  constructor(status: number, message: string) {
    super(message);
    this.status = status;
  }
}

import { staticRequest } from "./staticApi";

export const IS_STATIC = import.meta.env.VITE_STATIC === "1";

async function request<T>(method: string, url: string, body?: unknown): Promise<T> {
  if (IS_STATIC) return staticRequest(method, url, body) as Promise<T>;
  const res = await fetch(url, {
    method,
    headers: body !== undefined ? { "Content-Type": "application/json" } : undefined,
    body: body !== undefined ? JSON.stringify(body) : undefined,
  });
  const text = await res.text();
  let data: any = null;
  try {
    data = text ? JSON.parse(text) : null;
  } catch {
    data = text;
  }
  if (!res.ok) {
    const msg = (data && (data.detail || data.error || data.message)) || `HTTP ${res.status}`;
    throw new ApiError(res.status, typeof msg === "string" ? msg : JSON.stringify(msg));
  }
  return data as T;
}

export const api = {
  get: <T = any>(url: string) => request<T>("GET", url),
  post: <T = any>(url: string, body?: unknown) => request<T>("POST", url, body ?? {}),
  patch: <T = any>(url: string, body?: unknown) => request<T>("PATCH", url, body ?? {}),
  del: <T = any>(url: string) => request<T>("DELETE", url),
};

export type Score = {
  as_of_date: string;
  total: number | null;
  rank: number | null;
  eligible: boolean;
  coverage: number;
  components: Record<string, number | null>;
  exclusion_reasons: string[];
  positives: string[];
  negatives: string[];
  risk_flags: string[];
  weights: Record<string, number>;
  group: string;
};

export type Inst = {
  instrument_id: number;
  symbol: string;
  name: string;
  market: string;
  asset_type: string;
  etf_kind: string | null;
  industry: string | null;
  status: string;
  group: string;
  group_label: string;
  excluded_reason: string | null;
};

export type Meta = {
  demo_mode: boolean;
  latest_price_date: string;
  latest_score_date: string;
  fugle_enabled: boolean;
  ai_enabled: boolean;
  ai_model: string;
  scheduler: { enabled: boolean; time: string };
  job: { running: string | null; started_at: string | null; last: any };
  weights: Record<string, number>;
  component_labels: Record<string, string>;
  feature_labels: Record<string, string>;
  group_labels: Record<string, string>;
  disclaimer: string;
  static?: boolean;
  built_at?: string;
  progress?: { phase: string | null; done: number; total: number } | null;
};
