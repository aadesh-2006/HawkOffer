/**
 * Centralized typed API client for HawkOffer REST API v1.
 */

import {
  ApplicationCreate,
  ApplicationRead,
  ApplicationUpdate,
  BatchActionResponse,
  CompanyCreate,
  CompanyRead,
  CompanySubscriptionCreate,
  CompanySubscriptionRead,
  CompanyUpdate,
  HealthResponse,
  JobFilterParams,
  JobMatchDetail,
  JobRead,
  LogoutRequest,
  MessageResponse,
  ModelStatusInfo,
  MonitoringStatusResponse,
  NotificationRead,
  PaginatedResponse,
  PreferenceProfileCreate,
  PreferenceProfileRead,
  PreferenceProfileUpdate,
  PushSubscriptionCreate,
  PushSubscriptionRead,
  ScanStateRead,
  ScanTriggerResponse,
  SystemStatusResponse,
  TokenRefreshRequest,
  TokenResponse,
  UnreadCountResponse,
  UserLoginRequest,
  UserRead,
  UserRegisterRequest,
  VapidPublicKeyResponse,
  WorkerTriggerResponse,
} from "../types/api";

export const DEFAULT_API_BASE_URL = "http://127.0.0.1:8765/api/v1";

export class ApiClientError extends Error {
  public status: number;
  public detail: string;
  public raw: unknown;

  constructor(status: number, detail: string, raw?: unknown) {
    super(detail || `API Request failed with status ${status}`);
    this.name = "ApiClientError";
    this.status = status;
    this.detail = detail;
    this.raw = raw;
  }
}

export function getApiBaseUrl(): string {
  const custom = localStorage.getItem("hawkoffer_api_url");
  if (custom && custom.trim()) {
    return custom.trim().replace(/\/+$/, "");
  }
  return import.meta.env.VITE_API_BASE_URL || DEFAULT_API_BASE_URL;
}

export function setApiBaseUrl(url: string | null): void {
  if (url && url.trim()) {
    localStorage.setItem("hawkoffer_api_url", url.trim().replace(/\/+$/, ""));
  } else {
    localStorage.removeItem("hawkoffer_api_url");
  }
}

export function getAuthToken(): string | null {
  return localStorage.getItem("hawkoffer_access_token");
}

export function setAuthToken(token: string | null): void {
  if (token && token.trim()) {
    localStorage.setItem("hawkoffer_access_token", token.trim());
  } else {
    localStorage.removeItem("hawkoffer_access_token");
  }
}

export function getRefreshToken(): string | null {
  return localStorage.getItem("hawkoffer_refresh_token");
}

export function setRefreshToken(token: string | null): void {
  if (token && token.trim()) {
    localStorage.setItem("hawkoffer_refresh_token", token.trim());
  } else {
    localStorage.removeItem("hawkoffer_refresh_token");
  }
}

export function clearTokens(): void {
  localStorage.removeItem("hawkoffer_access_token");
  localStorage.removeItem("hawkoffer_refresh_token");
}

async function request<T>(path: string, options: RequestInit = {}, isRetry = false): Promise<T> {
  const baseUrl = getApiBaseUrl();
  const url = `${baseUrl}${path.startsWith("/") ? path : `/${path}`}`;

  const headers: Record<string, string> = {
    Accept: "application/json",
    ...(options.headers as Record<string, string>),
  };

  const token = getAuthToken();
  if (token && !headers["Authorization"]) {
    headers["Authorization"] = `Bearer ${token}`;
  }

  if (options.body && typeof options.body === "string" && !headers["Content-Type"]) {
    headers["Content-Type"] = "application/json";
  }

  let res: Response;
  try {
    res = await fetch(url, {
      ...options,
      headers,
    });
  } catch (err) {
    throw new ApiClientError(
      0,
      `Cannot connect to HawkOffer backend at ${baseUrl}. Ensure daemon is running on 127.0.0.1:8765.`,
      err
    );
  }

  // Transparent token refresh on 401 Unauthorized for authenticated endpoints
  if (
    res.status === 401 &&
    !isRetry &&
    !path.includes("/auth/login") &&
    !path.includes("/auth/register") &&
    !path.includes("/auth/refresh")
  ) {
    const refreshToken = getRefreshToken();
    if (refreshToken) {
      try {
        const refreshRes = await fetch(`${baseUrl}/auth/refresh`, {
          method: "POST",
          headers: {
            Accept: "application/json",
            "Content-Type": "application/json",
          },
          body: JSON.stringify({ refresh_token: refreshToken }),
        });
        if (refreshRes.ok) {
          const tokenData = await refreshRes.json();
          setAuthToken(tokenData.access_token);
          setRefreshToken(tokenData.refresh_token);
          headers["Authorization"] = `Bearer ${tokenData.access_token}`;
          return request<T>(path, { ...options, headers }, true);
        } else {
          clearTokens();
        }
      } catch {
        clearTokens();
      }
    }
  }

  if (!res.ok) {
    let errorDetail = `HTTP Error ${res.status}: ${res.statusText}`;
    let raw: unknown = null;
    try {
      const data = await res.json();
      raw = data;
      if (typeof data.detail === "string") {
        errorDetail = data.detail;
      } else if (Array.isArray(data.detail)) {
        errorDetail = data.detail.map((e: any) => e.msg || JSON.stringify(e)).join(", ");
      } else if (data.message) {
        errorDetail = data.message;
      }
    } catch {
      // Non-JSON response
    }
    throw new ApiClientError(res.status, errorDetail, raw);
  }

  if (res.status === 204) {
    return null as T;
  }

  return (await res.json()) as T;
}

// --------------------------------------------------------------------------
// SYSTEM API
// --------------------------------------------------------------------------

export const systemApi = {
  getHealth: () => request<HealthResponse>("/health"),
  getStatus: () => request<SystemStatusResponse>("/system/status"),
  getSemanticModelStatus: () => request<ModelStatusInfo>("/system/semantic-model"),
  downloadSemanticModel: () =>
    request<ModelStatusInfo>("/system/semantic-model/download", {
      method: "POST",
    }),
  toggleSemanticModel: (enabled: boolean) =>
    request<ModelStatusInfo>(`/system/semantic-model/toggle?enabled=${enabled}`, {
      method: "POST",
    }),
};

// --------------------------------------------------------------------------
// COMPANIES API
// --------------------------------------------------------------------------

export const companiesApi = {
  list: (params: { limit?: number; offset?: number; is_active?: boolean } = {}) => {
    const q = new URLSearchParams();
    if (params.limit !== undefined) q.set("limit", String(params.limit));
    if (params.offset !== undefined) q.set("offset", String(params.offset));
    if (params.is_active !== undefined) q.set("is_active", String(params.is_active));
    const qs = q.toString();
    return request<PaginatedResponse<CompanyRead>>(`/companies${qs ? `?${qs}` : ""}`);
  },

  get: (id: number) => request<CompanyRead>(`/companies/${id}`),

  create: (data: CompanyCreate) =>
    request<CompanyRead>("/companies", {
      method: "POST",
      body: JSON.stringify(data),
    }),

  update: (id: number, data: CompanyUpdate) =>
    request<CompanyRead>(`/companies/${id}`, {
      method: "PATCH",
      body: JSON.stringify(data),
    }),

  activate: (id: number) =>
    request<CompanyRead>(`/companies/${id}/activate`, {
      method: "POST",
    }),

  deactivate: (id: number) =>
    request<CompanyRead>(`/companies/${id}/deactivate`, {
      method: "POST",
    }),

  triggerScan: (id: number) =>
    request<ScanTriggerResponse>(`/companies/${id}/trigger`, {
      method: "POST",
    }),

  getScanState: (id: number) => request<ScanStateRead>(`/companies/${id}/scan-state`),

  delete: (id: number) =>
    request<MessageResponse>(`/companies/${id}`, {
      method: "DELETE",
    }),
};

// --------------------------------------------------------------------------
// PREFERENCE PROFILES API
// --------------------------------------------------------------------------

export const profilesApi = {
  list: (limit = 50, offset = 0) =>
    request<PaginatedResponse<PreferenceProfileRead>>(`/profiles?limit=${limit}&offset=${offset}`),

  get: (id: number) => request<PreferenceProfileRead>(`/profiles/${id}`),

  create: (data: PreferenceProfileCreate) =>
    request<PreferenceProfileRead>("/profiles", {
      method: "POST",
      body: JSON.stringify(data),
    }),

  update: (id: number, data: PreferenceProfileUpdate) =>
    request<PreferenceProfileRead>(`/profiles/${id}`, {
      method: "PATCH",
      body: JSON.stringify(data),
    }),

  activate: (id: number) =>
    request<PreferenceProfileRead>(`/profiles/${id}/activate`, {
      method: "POST",
    }),

  deactivate: (id: number) =>
    request<PreferenceProfileRead>(`/profiles/${id}/deactivate`, {
      method: "POST",
    }),

  delete: (id: number) =>
    request<MessageResponse>(`/profiles/${id}`, {
      method: "DELETE",
    }),
};

// --------------------------------------------------------------------------
// JOBS API
// --------------------------------------------------------------------------

export const jobsApi = {
  list: (params: JobFilterParams = {}) => {
    const q = new URLSearchParams();
    if (params.company_id !== undefined && params.company_id !== null) {
      q.set("company_id", String(params.company_id));
    }
    if (params.status) q.set("status", params.status);
    if (params.job_type) q.set("job_type", params.job_type);
    if (params.employment_type) q.set("employment_type", params.employment_type);
    if (params.graduation_year) q.set("graduation_year", String(params.graduation_year));
    if (params.search) q.set("search", params.search);
    if (params.limit !== undefined) q.set("limit", String(params.limit));
    if (params.offset !== undefined) q.set("offset", String(params.offset));

    const qs = q.toString();
    return request<PaginatedResponse<JobRead>>(`/jobs${qs ? `?${qs}` : ""}`);
  },

  get: (id: number) => request<JobRead>(`/jobs/${id}`),

  getMatchDetail: (jobId: number, profileId?: number) =>
    request<JobMatchDetail>(`/jobs/${jobId}/match${profileId ? `?profile_id=${profileId}` : ""}`),
};

// --------------------------------------------------------------------------
// NOTIFICATIONS API
// --------------------------------------------------------------------------

export const notificationsApi = {
  list: (params: {
    profile_id?: number | null;
    status?: string | null;
    include_dismissed?: boolean;
    limit?: number;
    offset?: number;
  } = {}) => {
    const q = new URLSearchParams();
    if (params.profile_id !== undefined && params.profile_id !== null) {
      q.set("profile_id", String(params.profile_id));
    }
    if (params.status) q.set("status", params.status);
    if (params.include_dismissed) q.set("include_dismissed", "true");
    if (params.limit !== undefined) q.set("limit", String(params.limit));
    if (params.offset !== undefined) q.set("offset", String(params.offset));

    const qs = q.toString();
    return request<PaginatedResponse<NotificationRead>>(`/notifications${qs ? `?${qs}` : ""}`);
  },

  getUnreadCount: (profileId?: number | null) => {
    const qs = profileId ? `?profile_id=${profileId}` : "";
    return request<UnreadCountResponse>(`/notifications/unread-count${qs}`);
  },

  get: (id: number) => request<NotificationRead>(`/notifications/${id}`),

  markRead: (id: number) =>
    request<NotificationRead>(`/notifications/${id}/read`, {
      method: "POST",
    }),

  markUnread: (id: number) =>
    request<NotificationRead>(`/notifications/${id}/unread`, {
      method: "POST",
    }),

  dismiss: (id: number) =>
    request<NotificationRead>(`/notifications/${id}/dismiss`, {
      method: "POST",
    }),

  restore: (id: number) =>
    request<NotificationRead>(`/notifications/${id}/restore`, {
      method: "POST",
    }),

  batchMarkRead: (ids: number[]) =>
    request<BatchActionResponse>("/notifications/batch/read", {
      method: "POST",
      body: JSON.stringify({ ids }),
    }),

  batchDismiss: (ids: number[]) =>
    request<BatchActionResponse>("/notifications/batch/dismiss", {
      method: "POST",
      body: JSON.stringify({ ids }),
    }),
};

// --------------------------------------------------------------------------
// MONITORING & PIPELINE API
// --------------------------------------------------------------------------

export const monitoringApi = {
  getStatus: () => request<MonitoringStatusResponse>("/monitoring/status"),

  triggerDiscovery: () =>
    request<WorkerTriggerResponse>("/monitoring/discovery/trigger", {
      method: "POST",
    }),

  triggerReevaluation: () =>
    request<WorkerTriggerResponse>("/monitoring/reevaluation/trigger", {
      method: "POST",
    }),

  triggerDrain: () =>
    request<WorkerTriggerResponse>("/monitoring/drain/trigger", {
      method: "POST",
    }),

  pause: (target: { job_name?: string; company_id?: number }) =>
    request<MessageResponse>("/monitoring/pause", {
      method: "POST",
      body: JSON.stringify(target),
    }),

  resume: (target: { job_name?: string; company_id?: number }) =>
    request<MessageResponse>("/monitoring/resume", {
      method: "POST",
      body: JSON.stringify(target),
    }),
};

// --------------------------------------------------------------------------
// AUTHENTICATION & USER API
// --------------------------------------------------------------------------

export const authApi = {
  register: (payload: UserRegisterRequest) =>
    request<UserRead>("/auth/register", {
      method: "POST",
      body: JSON.stringify(payload),
    }),

  login: (payload: UserLoginRequest) =>
    request<TokenResponse>("/auth/login", {
      method: "POST",
      body: JSON.stringify(payload),
    }),

  refresh: (payload: TokenRefreshRequest) =>
    request<TokenResponse>("/auth/refresh", {
      method: "POST",
      body: JSON.stringify(payload),
    }),

  logout: (payload: LogoutRequest) =>
    request<MessageResponse>("/auth/logout", {
      method: "POST",
      body: JSON.stringify(payload),
    }),

  getMe: () => request<UserRead>("/auth/me"),
};

// --------------------------------------------------------------------------
// APPLICATIONS API (TENANT-SCOPED)
// --------------------------------------------------------------------------

export const applicationsApi = {
  list: (params?: { skip?: number; limit?: number }) => {
    const qs = new URLSearchParams();
    if (params?.skip !== undefined) qs.set("skip", String(params.skip));
    if (params?.limit !== undefined) qs.set("limit", String(params.limit));
    const query = qs.toString();
    return request<ApplicationRead[]>(`/applications${query ? `?${query}` : ""}`);
  },

  create: (payload: ApplicationCreate) =>
    request<ApplicationRead>("/applications", {
      method: "POST",
      body: JSON.stringify(payload),
    }),

  get: (applicationId: number) =>
    request<ApplicationRead>(`/applications/${applicationId}`),

  update: (applicationId: number, payload: ApplicationUpdate) =>
    request<ApplicationRead>(`/applications/${applicationId}`, {
      method: "PATCH",
      body: JSON.stringify(payload),
    }),

  delete: (applicationId: number) =>
    request<MessageResponse>(`/applications/${applicationId}`, {
      method: "DELETE",
    }),
};

// --------------------------------------------------------------------------
// COMPANY SUBSCRIPTIONS API (TENANT-SCOPED)
// --------------------------------------------------------------------------

export const subscriptionsApi = {
  list: () => request<CompanySubscriptionRead[]>("/subscriptions"),

  subscribe: (payload: CompanySubscriptionCreate) =>
    request<CompanySubscriptionRead>("/subscriptions", {
      method: "POST",
      body: JSON.stringify(payload),
    }),

  unsubscribe: (companyId: number) =>
    request<MessageResponse>(`/subscriptions/${companyId}`, {
      method: "DELETE",
    }),
};

// --------------------------------------------------------------------------
// WEB PUSH API (RFC 8291 / RFC 8292)
// --------------------------------------------------------------------------

export const pushApi = {
  getVapidPublicKey: () =>
    request<VapidPublicKeyResponse>("/push/vapid-public-key"),

  subscribe: (payload: PushSubscriptionCreate) =>
    request<PushSubscriptionRead>("/push/subscribe", {
      method: "POST",
      body: JSON.stringify(payload),
    }),

  listSubscriptions: () =>
    request<PushSubscriptionRead[]>("/push/subscriptions"),

  unsubscribe: (endpoint: string) =>
    request<MessageResponse>("/push/unsubscribe", {
      method: "POST",
      body: JSON.stringify({ endpoint }),
    }),
};


