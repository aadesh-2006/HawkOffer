/**
 * TypeScript API contracts matching HawkOffer FastAPI v1 Pydantic models.
 */

export interface PaginatedResponse<T> {
  items: T[];
  total: number;
  limit: number;
  offset: number;
}

export interface MessageResponse {
  message: string;
  detail?: string | null;
}

export interface BatchActionRequest {
  ids: number[];
}

export interface BatchActionResponse {
  affected_count: number;
  message: string;
}

export interface HealthResponse {
  status: "healthy" | "degraded" | "unhealthy" | string;
  database: "connected" | "disconnected" | string;
  scheduler: "running" | "stopped" | string;
  timestamp: string;
}

export interface WorkerScheduleInfo {
  job_id: string;
  scheduled: boolean;
  next_run_time: string | null;
  paused: boolean | null;
}

export interface SystemStatusResponse {
  app_name: string;
  env: string;
  is_running: boolean;
  is_lifecycle_gate_open: boolean;
  companies_count: number;
  active_companies_count: number;
  total_jobs_count: number;
  unread_notifications_count: number;
  pipeline_status: Record<string, WorkerScheduleInfo>;
  timestamp: string;
}

export type CompanyPlatform = "greenhouse" | "lever" | "ashby" | "workday" | "generic";

export interface CompanyRead {
  id: number;
  name: string;
  career_url: string;
  platform: CompanyPlatform | string;
  is_active: boolean;
  is_curated?: boolean;
  created_by_user_id?: number | null;
  created_at: string;
  updated_at: string;
}

export interface CompanyCreate {
  name: string;
  career_url: string;
  platform?: CompanyPlatform | string;
  is_active?: boolean;
}

export interface CompanyUpdate {
  name?: string | null;
  career_url?: string | null;
  platform?: CompanyPlatform | string | null;
  is_active?: boolean | null;
}

export interface ScanStateRead {
  id: number;
  company_id: number;
  scan_interval_minutes: number;
  status: "IDLE" | "RUNNING" | "FAILED" | "RATE_LIMITED" | string;
  last_scan_started_at: string | null;
  last_scan_completed_at: string | null;
  next_scan_at: string | null;
  consecutive_failures: number;
  last_error: string | null;
  total_scans: number;
  total_errors: number;
  created_at: string;
  updated_at: string;
}

export interface ScanStats {
  total_fetched: number;
  newly_inserted: number;
  already_existing: number;
  updated_records: number;
  failed_records: number;
  duration_ms: number;
}

export interface ScanTriggerResponse {
  company_id: number;
  status: string;
  message: string;
  stats?: ScanStats | Record<string, any> | null;
}

export interface JobRead {
  id: number;
  company_id: number;
  title: string;
  location: string | null;
  department: string | null;
  external_id: string | null;
  description: string | null;
  application_url: string;
  employment_type: string | null;
  job_type: string | null;
  graduation_year: number | null;
  posted_at: string | null;
  status: "NEW" | "ACTIVE" | "UPDATED" | "CLOSED" | "PROCESSED" | string;
  fingerprint_hash: string;
  content_hash: string;
  first_seen_at: string;
  last_seen_at: string;
  created_at: string;
  updated_at: string;
  semantic_score?: number | null;
  match_tier?: string | null;
  shared_skills?: string[] | null;
  missing_skills?: string[] | null;
}

export interface JobMatchDetail {
  job_id: number;
  profile_id: number;
  eligibility_status: string;
  deterministic_score: number;
  semantic_score?: number | null;
  blended_score: number;
  relevance_tier: string;
  shared_skills: string[];
  missing_skills: string[];
  is_sparse_description: boolean;
  reasons: string[];
  score_breakdown: Record<string, number>;
}

export interface ModelStatusInfo {
  status: "NOT_DOWNLOADED" | "DOWNLOADING" | "READY" | "DISABLED" | "ERROR";
  model_name: string;
  model_path?: string | null;
  dimension: number;
  is_ready: boolean;
  is_fallback: boolean;
  download_progress: number;
  error_message?: string | null;
}

export interface JobFilterParams {
  company_id?: number | null;
  status?: string | null;
  job_type?: string | null;
  employment_type?: string | null;
  graduation_year?: number | null;
  search?: string | null;
  limit?: number;
  offset?: number;
}

export type JobListParams = JobFilterParams;

export interface PreferenceProfileRead {
  id: number;
  name: string;
  graduation_year: number | null;
  preferred_roles: string[];
  preferred_locations: string[];
  preferred_skills: string[];
  preferred_employment_types: string[];
  required_employment_types: string[];
  preferred_workplace_types: string[];
  excluded_keywords: string[];
  excluded_title_keywords: string[];
  excluded_content_keywords: string[];
  required_roles: string[];
  required_locations: string[];
  require_graduation_year_match: boolean;
  minimum_match_score: number;
  resume_text?: string | null;
  semantic_enabled: boolean;
  is_active: boolean;
  revision: number;
  created_at: string;
  updated_at: string;
}

export interface PreferenceProfileCreate {
  name?: string;
  graduation_year?: number | null;
  preferred_roles?: string[];
  preferred_locations?: string[];
  preferred_skills?: string[];
  preferred_employment_types?: string[];
  required_employment_types?: string[];
  preferred_workplace_types?: string[];
  excluded_keywords?: string[];
  excluded_title_keywords?: string[];
  excluded_content_keywords?: string[];
  required_roles?: string[];
  required_locations?: string[];
  require_graduation_year_match?: boolean;
  minimum_match_score?: number;
  resume_text?: string | null;
  semantic_enabled?: boolean;
  is_active?: boolean;
}

export interface PreferenceProfileUpdate {
  name?: string | null;
  graduation_year?: number | null;
  preferred_roles?: string[] | null;
  preferred_locations?: string[] | null;
  preferred_skills?: string[] | null;
  preferred_employment_types?: string[] | null;
  required_employment_types?: string[] | null;
  preferred_workplace_types?: string[] | null;
  excluded_keywords?: string[] | null;
  excluded_title_keywords?: string[] | null;
  excluded_content_keywords?: string[] | null;
  required_roles?: string[] | null;
  required_locations?: string[] | null;
  require_graduation_year_match?: boolean | null;
  minimum_match_score?: number | null;
  resume_text?: string | null;
  semantic_enabled?: boolean | null;
  is_active?: boolean | null;
}

export type NotificationStatus = "UNREAD" | "READ" | "DISMISSED";
export type DeliveryStatus = "PENDING" | "DELIVERED" | "FAILED" | "NOT_REQUIRED";

export interface NotificationRead {
  id: number;
  job_id: number;
  profile_id: number | null;
  notification_type: string;
  status: NotificationStatus;
  delivery_status: DeliveryStatus;
  created_at: string;
  read_at: string | null;
  dismissed_at: string | null;
  delivered_at: string | null;
}

export interface UnreadCountResponse {
  unread_count: number;
  profile_id?: number | null;
}

export interface MonitoringStatusResponse {
  is_running: boolean;
  is_lifecycle_gate_open: boolean;
  pipeline_status: Record<string, WorkerScheduleInfo>;
  timestamp: string;
}

export interface WorkerTriggerResponse {
  worker_name: string;
  status: "completed" | "rejected" | string;
  message: string;
  result: Record<string, any> | null;
  timestamp: string;
}

export interface PauseResumeRequest {
  job_name?: string | null;
  company_id?: number | null;
}

export interface UserRead {
  id: number;
  email: string;
  full_name?: string | null;
  role: "user" | "admin" | string;
  is_active: boolean;
  is_verified: boolean;
  created_at: string;
  updated_at: string;
}

export interface UserRegisterRequest {
  email: string;
  password: string;
  full_name?: string | null;
}

export interface UserLoginRequest {
  email: string;
  password: string;
}

export interface TokenResponse {
  access_token: string;
  refresh_token: string;
  token_type: string;
  expires_in: number;
  user?: UserRead;
}

export interface TokenRefreshRequest {
  refresh_token: string;
}

export interface LogoutRequest {
  refresh_token: string;
}

export type ApplicationStatus = "SAVED" | "APPLIED" | "INTERVIEWING" | "OFFER" | "REJECTED" | "WITHDRAWN";

export interface ApplicationRead {
  id: number;
  user_id: number;
  job_id: number;
  status: ApplicationStatus | string;
  notes: string | null;
  applied_at: string | null;
  created_at: string;
  updated_at: string;
  job?: JobRead | null;
}

export interface ApplicationCreate {
  job_id: number;
  status?: ApplicationStatus | string;
  notes?: string | null;
  applied_at?: string | null;
}

export interface ApplicationUpdate {
  status?: ApplicationStatus | string | null;
  notes?: string | null;
  applied_at?: string | null;
}

export interface CompanySubscriptionRead {
  id: number;
  user_id: number;
  company_id: number;
  created_at: string;
}

export interface CompanySubscriptionCreate {
  company_id: number;
}

export interface CronMonitoringResponse {
  status: "completed" | "partial_failure" | "failed" | string;
  message: string;
  duration_ms: number;
  companies_targeted: number;
  companies_scraped: number;
  companies_failed: number;
  jobs_new: number;
  jobs_updated: number;
  jobs_existing: number;
  discovery_evaluated: number;
  candidates_created: number;
  notifications_promoted: number;
  pushes_attempted: number;
  pushes_delivered: number;
  errors: string[];
  timestamp: string;
}

export interface VapidPublicKeyResponse {
  public_key: string;
}

export interface PushKeys {
  p256dh: string;
  auth: string;
}

export interface PushSubscriptionCreate {
  endpoint: string;
  keys: PushKeys;
  user_agent?: string | null;
}

export interface PushSubscriptionRead {
  id: number;
  user_id: number;
  endpoint: string;
  p256dh?: string | null;
  auth?: string | null;
  user_agent: string | null;
  failure_count: number;
  created_at: string;
  last_used_at: string;
}



