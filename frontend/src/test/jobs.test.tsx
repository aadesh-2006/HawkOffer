import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { AppProvider } from "../context/AppContext";
import { JobsFeedScreen } from "../components/jobs/JobsFeedScreen";
import { JobDetailModal } from "../components/jobs/JobDetailModal";
import { jobsApi } from "../services/api";

const { mockJob, mockCompany } = vi.hoisted(() => ({
  mockJob: {
    id: 42,
    company_id: 1,
    title: "Distributed Systems Intern",
    location: "New York, NY",
    department: "Core Systems",
    external_id: "ext-42",
    description: "Detailed distributed systems responsibilities and qualifications.",
    application_url: "https://boards.greenhouse.io/job/42",
    employment_type: "Internship",
    job_type: "internship",
    graduation_year: 2026,
    posted_at: null,
    status: "ACTIVE",
    semantic_score: 88.0,
    match_tier: "EXCELLENT",
    fingerprint_hash: "1".repeat(64),
    content_hash: "2".repeat(64),
    first_seen_at: new Date().toISOString(),
    last_seen_at: new Date().toISOString(),
    created_at: new Date().toISOString(),
    updated_at: new Date().toISOString(),
  },
  mockCompany: {
    id: 1,
    name: "Datadog",
    career_url: "https://boards.greenhouse.io/datadog",
    platform: "greenhouse",
    is_active: true,
    created_at: new Date().toISOString(),
    updated_at: new Date().toISOString(),
  },
}));

vi.mock("../services/api", () => ({
  getApiBaseUrl: () => "http://127.0.0.1:8765/api/v1",
  setApiBaseUrl: vi.fn(),
  systemApi: {
    getHealth: vi.fn().mockResolvedValue({ status: "healthy", database: "connected", scheduler: "running", timestamp: "" }),
    getStatus: vi.fn().mockResolvedValue({ is_running: true, is_lifecycle_gate_open: true, companies_count: 1, active_companies_count: 1, total_jobs_count: 1, unread_notifications_count: 0, pipeline_status: {}, timestamp: "" }),
  },
  notificationsApi: {
    getUnreadCount: vi.fn().mockResolvedValue({ unread_count: 0 }),
  },
  companiesApi: {
    list: vi.fn().mockResolvedValue({
      items: [mockCompany],
      total: 1,
      limit: 50,
      offset: 0,
    }),
    get: vi.fn().mockResolvedValue(mockCompany),
  },
  jobsApi: {
    list: vi.fn().mockResolvedValue({
      items: [mockJob],
      total: 1,
      limit: 25,
      offset: 0,
    }),
    get: vi.fn().mockResolvedValue(mockJob),
    getMatchDetail: vi.fn().mockResolvedValue({
      job_id: 42,
      profile_id: 1,
      eligibility_status: "ELIGIBLE",
      deterministic_score: 85,
      semantic_score: 88.0,
      blended_score: 86.5,
      relevance_tier: "EXCELLENT",
      shared_skills: ["Python", "Distributed Systems"],
      missing_skills: ["Kubernetes"],
      is_sparse_description: false,
      reasons: ["Strong match"],
      score_breakdown: {},
    }),
  },
}));

describe("JobsFeedScreen and JobDetailModal", () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it("renders jobs feed table with job title and company details", async () => {
    render(
      <AppProvider>
        <JobsFeedScreen />
        <JobDetailModal />
      </AppProvider>
    );

    await waitFor(() => {
      expect(screen.getByText("Distributed Systems Intern")).toBeInTheDocument();
      expect(screen.getAllByText("Datadog")[0]).toBeInTheDocument();
      expect(screen.getByText("New York, NY")).toBeInTheDocument();
    });
  });

  it("debounces search input and requests filtered jobs", async () => {
    render(
      <AppProvider>
        <JobsFeedScreen />
      </AppProvider>
    );

    const searchInput = screen.getByTestId("input-job-search");
    fireEvent.change(searchInput, { target: { value: "Distributed" } });

    await waitFor(
      () => {
        expect(jobsApi.list).toHaveBeenCalledWith(
          expect.objectContaining({ search: "Distributed" })
        );
      },
      { timeout: 1000 }
    );
  });

  it("opens job details modal when inspect button is clicked", async () => {
    render(
      <AppProvider>
        <JobsFeedScreen />
        <JobDetailModal />
      </AppProvider>
    );

    await waitFor(() => {
      expect(screen.getByTestId("btn-inspect-job-42")).toBeInTheDocument();
    });

    fireEvent.click(screen.getByTestId("btn-inspect-job-42"));

    await waitFor(() => {
      expect(jobsApi.get).toHaveBeenCalledWith(42);
      expect(screen.getByText("Detailed distributed systems responsibilities and qualifications.")).toBeInTheDocument();
      expect(screen.getByText("Apply Now")).toBeInTheDocument();
    });
  });

  it("renders semantic fit score and explainable skill breakdown in modal", async () => {
    render(
      <AppProvider>
        <JobsFeedScreen />
        <JobDetailModal />
      </AppProvider>
    );

    await waitFor(() => {
      expect(screen.getByText("88% fit")).toBeInTheDocument();
      expect(screen.getByTestId("btn-inspect-job-42")).toBeInTheDocument();
    });

    fireEvent.click(screen.getByTestId("btn-inspect-job-42"));

    await waitFor(() => {
      expect(jobsApi.getMatchDetail).toHaveBeenCalledWith(42);
      expect(screen.getByText("Semantic Matching & Fit Analysis")).toBeInTheDocument();
      expect(screen.getByText("EXCELLENT MATCH")).toBeInTheDocument();
      expect(screen.getByText("88% Similarity")).toBeInTheDocument();
      expect(screen.getByText("Distributed Systems")).toBeInTheDocument();
      expect(screen.getByText("Kubernetes")).toBeInTheDocument();
    });
  });
});
