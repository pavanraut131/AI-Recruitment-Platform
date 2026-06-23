import axios from "axios";

const API_URL = process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000";

export const api = axios.create({
  baseURL: `${API_URL}/api/v1`,
  headers: { "Content-Type": "application/json" },
});

// ── Types ────────────────────────────────────────────────────
export interface Campaign {
  id: string;
  title: string;
  role: string;
  description?: string;
  required_skills: string[];
  status: string;
  created_at: string;
  updated_at: string;
  stats?: CampaignStats;
}

export interface CampaignStats {
  total_resumes: number;
  total_processed: number;
  total_failed: number;
  total_pending: number;
  total_quota_exceeded: number;
  avg_score?: number;
  strong_match: number;
  moderate_match: number;
  weak_match: number;
  rejected: number;
  top_skills: { skill: string; count: number }[];
}

export interface Resume {
  id: string;
  campaign_id: string;
  original_filename: string;
  status: string;
  retry_count: number;
  error_message?: string;
  file_size_bytes: number;
  uploaded_at: string;
}

export interface Candidate {
  id: string;
  resume_id: string;
  campaign_id: string;
  name?: string;
  email?: string;
  phone?: string;
  github_url?: string;
  linkedin_url?: string;
  skills: string[];
  education: { degree: string; institution: string; year?: string }[];
  work_experience: { company: string; role: string; duration: string }[];
  certifications: string[];
  projects: { name: string; description: string; technologies: string[] }[];
  years_of_experience?: number;
  summary?: string;
  strengths: string[];
  weaknesses: string[];
  missing_skills: string[];
  recommendation?: string;
  score?: number;
  category?: string;
  created_at: string;
  updated_at: string;
}

export interface UploadBatchResponse {
  batch_id: string;
  campaign_id: string;
  total_files: number;
  accepted_files: number;
  rejected_files: { filename: string; reason: string }[];
  resume_ids: string[];
  message: string;
}

export interface SSEUpdate {
  resume_id: string;
  status: string;
  filename?: string;
  candidate_name?: string;
  score?: number;
  category?: string;
  error?: string;
}

// ── API Functions ────────────────────────────────────────────

// Campaigns
export const getCampaigns = () => api.get<Campaign[]>("/campaigns").then((r) => r.data);
export const getCampaign = (id: string) => api.get<Campaign>(`/campaigns/${id}`).then((r) => r.data);
export const createCampaign = (data: Partial<Campaign>) => api.post<Campaign>("/campaigns", data).then((r) => r.data);
export const updateCampaign = (id: string, data: Partial<Campaign>) => api.put<Campaign>(`/campaigns/${id}`, data).then((r) => r.data);
export const deleteCampaign = (id: string) => api.delete(`/campaigns/${id}`);
export const getCampaignStats = (id: string) => api.get<CampaignStats>(`/campaigns/${id}/stats`).then((r) => r.data);

// Resumes
export const uploadResumes = (campaignId: string, files: File[], onProgress?: (pct: number) => void) => {
  const formData = new FormData();
  files.forEach((f) => formData.append("files", f));
  return api.post<UploadBatchResponse>(`/campaigns/${campaignId}/upload`, formData, {
    headers: { "Content-Type": "multipart/form-data" },
    onUploadProgress: (e) => {
      if (onProgress && e.total) onProgress(Math.round((e.loaded / e.total) * 100));
    },
  }).then((r) => r.data);
};
export const getCampaignResumes = (campaignId: string) =>
  api.get<Resume[]>(`/campaigns/${campaignId}/resumes`).then((r) => r.data);

// Candidates
export const getCandidates = (campaignId: string, params?: Record<string, string | number>) =>
  api.get<Candidate[]>(`/campaigns/${campaignId}/candidates`, { params }).then((r) => r.data);
export const getCandidate = (id: string) => api.get<Candidate>(`/candidates/${id}`).then((r) => r.data);
export const updateCandidateCategory = (id: string, category: string) =>
  api.put<Candidate>(`/candidates/${id}/category`, { category }).then((r) => r.data);
export const deleteCandidate = (id: string) => api.delete(`/candidates/${id}`);

// SSE
export const SSE_URL = (campaignId: string) =>
  `${API_URL}/api/v1/campaigns/${campaignId}/stream`;
