import { describe, it, expect, beforeEach, afterEach, vi } from "vitest";
import {
  ApiClientError,
  getApiBaseUrl,
  setApiBaseUrl,
  systemApi,
  companiesApi,
  jobsApi,
  profilesApi,
  notificationsApi,
  monitoringApi,
} from "../services/api";

describe("API Service Client", () => {
  const originalFetch = global.fetch;

  beforeEach(() => {
    localStorage.clear();
    setApiBaseUrl(null);
  });

  afterEach(() => {
    global.fetch = originalFetch;
    vi.restoreAllMocks();
  });

  it("handles base URL get and set with local storage", () => {
    expect(getApiBaseUrl()).toBe("http://127.0.0.1:8765/api/v1");

    setApiBaseUrl("http://localhost:9000/api/v1/");
    expect(getApiBaseUrl()).toBe("http://localhost:9000/api/v1");

    setApiBaseUrl(null);
    expect(getApiBaseUrl()).toBe("http://127.0.0.1:8765/api/v1");
  });

  it("constructs ApiClientError with status, detail, and raw response", () => {
    const error = new ApiClientError(404, "Job not found", { detail: "Job not found" });
    expect(error.name).toBe("ApiClientError");
    expect(error.status).toBe(404);
    expect(error.detail).toBe("Job not found");
    expect(error.message).toBe("Job not found");
  });

  it("fetches system health successfully", async () => {
    const mockHealth = {
      status: "healthy",
      database: "connected",
      scheduler: "running",
      timestamp: new Date().toISOString(),
    };

    global.fetch = vi.fn().mockResolvedValue({
      ok: true,
      status: 200,
      json: async () => mockHealth,
    });

    const res = await systemApi.getHealth();
    expect(res.status).toBe("healthy");
    expect(res.database).toBe("connected");
  });

  it("throws ApiClientError with error details on HTTP 400/404", async () => {
    global.fetch = vi.fn().mockResolvedValue({
      ok: false,
      status: 404,
      json: async () => ({ detail: "Company with ID 999 not found" }),
    });

    await expect(companiesApi.get(999)).rejects.toThrow("Company with ID 999 not found");
  });

  it("constructs query strings accurately for jobs filtering", async () => {
    const mockJobsResponse = {
      items: [],
      total: 0,
      limit: 25,
      offset: 0,
    };

    const fetchMock = vi.fn().mockResolvedValue({
      ok: true,
      status: 200,
      json: async () => mockJobsResponse,
    });
    global.fetch = fetchMock;

    await jobsApi.list({
      company_id: 2,
      status: "ACTIVE",
      job_type: "internship",
      graduation_year: 2026,
      search: "systems",
      limit: 25,
      offset: 50,
    });

    expect(fetchMock).toHaveBeenCalledTimes(1);
    const calledUrl = fetchMock.mock.calls[0][0] as string;
    expect(calledUrl).toContain("company_id=2");
    expect(calledUrl).toContain("status=ACTIVE");
    expect(calledUrl).toContain("job_type=internship");
    expect(calledUrl).toContain("graduation_year=2026");
    expect(calledUrl).toContain("search=systems");
    expect(calledUrl).toContain("limit=25");
    expect(calledUrl).toContain("offset=50");
  });

  it("posts batch actions for notifications", async () => {
    const mockBatchRes = {
      affected_count: 3,
      message: "Marked 3 notifications as read",
    };

    const fetchMock = vi.fn().mockResolvedValue({
      ok: true,
      status: 200,
      json: async () => mockBatchRes,
    });
    global.fetch = fetchMock;

    const res = await notificationsApi.batchMarkRead([1, 2, 3]);
    expect(res.affected_count).toBe(3);
    expect(fetchMock).toHaveBeenCalledWith(
      expect.stringContaining("/notifications/batch/read"),
      expect.objectContaining({
        method: "POST",
        body: JSON.stringify({ ids: [1, 2, 3] }),
      })
    );
  });

  it("triggers monitoring discovery with POST request", async () => {
    const mockTriggerRes = {
      worker_name: "job_discovery",
      status: "completed",
      message: "Discovery completed",
      result: { claimed_count: 5, promoted_count: 2 },
      timestamp: new Date().toISOString(),
    };

    global.fetch = vi.fn().mockResolvedValue({
      ok: true,
      status: 200,
      json: async () => mockTriggerRes,
    });

    const res = await monitoringApi.triggerDiscovery();
    expect(res.status).toBe("completed");
    expect(res.result?.claimed_count).toBe(5);
  });

  it("fetches profiles with list", async () => {
    const mockProfiles = {
      items: [{ id: 1, name: "Default Profile", is_active: true, revision: 1 }],
      total: 1,
      limit: 50,
      offset: 0,
    };
    global.fetch = vi.fn().mockResolvedValue({
      ok: true,
      status: 200,
      json: async () => mockProfiles,
    });

    const res = await profilesApi.list();
    expect(res.items).toHaveLength(1);
    expect(res.items[0].name).toBe("Default Profile");
  });
});
