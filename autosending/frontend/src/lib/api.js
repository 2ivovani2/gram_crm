import axios from "axios";

const API_URL = process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000";

const api = axios.create({
  baseURL: API_URL,
  timeout: 30000,
});

// Attach Bearer token on every request
api.interceptors.request.use((config) => {
  if (typeof window !== "undefined") {
    const token = localStorage.getItem("token");
    if (token) config.headers.Authorization = `Bearer ${token}`;
  }
  return config;
});

// On 401 — clear expired session and redirect to login (skip if no token: auth call failed, not session expiry)
api.interceptors.response.use(
  (response) => response,
  (error) => {
    if (error.response?.status === 401 && typeof window !== "undefined") {
      const hadToken = !!localStorage.getItem("token");
      if (hadToken) {
        localStorage.removeItem("token");
        const basePath = process.env.NEXT_PUBLIC_BASE_PATH || "";
        window.location.href = `${basePath}/login/`;
      }
    }
    return Promise.reject(error);
  }
);

// ── Auth ──────────────────────────────────────────────────────────────────────
export const authWithTelegram = (data) => api.post("/api/auth/telegram", data).then(r => r.data);
export const getMe            = ()     => api.get("/api/auth/me").then(r => r.data);

// ── Accounts ──────────────────────────────────────────────────────────────────
export const getAccounts   = ()         => api.get("/api/accounts").then(r => r.data);
export const addAccount    = (data)     => api.post("/api/accounts", data).then(r => r.data);
export const deleteAccount = (id)       => api.delete(`/api/accounts/${id}`).then(r => r.data);
export const sendCode      = (id)       => api.post(`/api/accounts/${id}/send-code`).then(r => r.data);
export const verifyCode    = (id, data) => api.post(`/api/accounts/${id}/verify-code`, data).then(r => r.data);
export const updateProfile = (id, data) => api.patch(`/api/accounts/${id}/profile`, data).then(r => r.data);
export const uploadPhoto   = (id, file) => {
  const fd = new FormData();
  fd.append("file", file);
  return api.post(`/api/accounts/${id}/photo`, fd, { headers: { "Content-Type": "multipart/form-data" } }).then(r => r.data);
};
export const syncAccount   = (id)       => api.get(`/api/accounts/${id}/sync`).then(r => r.data);
export const importSession = (file, apiId, apiHash) => {
  const fd = new FormData();
  fd.append("file", file);
  fd.append("api_id", apiId);
  fd.append("api_hash", apiHash);
  return api.post("/api/accounts/import-session", fd, { timeout: 30000 }).then(r => r.data);
};

// ── Channels ──────────────────────────────────────────────────────────────────
export const getChannels      = ()       => api.get("/api/channels").then(r => r.data);
export const addChannel       = (data)   => api.post("/api/channels", data).then(r => r.data);
export const toggleChannel    = (id)     => api.patch(`/api/channels/${id}`).then(r => r.data);
export const deleteChannel    = (id)     => api.delete(`/api/channels/${id}`).then(r => r.data);
export const searchChannels   = (params) => api.get("/api/channels/search", { params, timeout: 20000 }).then(r => r.data);
export const bulkAddChannels  = (urls)   => api.post("/api/channels/bulk-add", { urls }).then(r => r.data);

// ── Messages ──────────────────────────────────────────────────────────────────
export const getMessages   = ()         => api.get("/api/messages").then(r => r.data);
export const addMessage    = (data)     => api.post("/api/messages", data).then(r => r.data);
export const updateMessage = (id, data) => api.patch(`/api/messages/${id}`, data).then(r => r.data);
export const deleteMessage = (id)       => api.delete(`/api/messages/${id}`).then(r => r.data);

// ── Campaigns ─────────────────────────────────────────────────────────────────
export const getCampaigns   = ()     => api.get("/api/campaigns").then(r => r.data);
export const createCampaign = (data) => api.post("/api/campaigns", data).then(r => r.data);
export const startCampaign  = (id)   => api.post(`/api/campaigns/${id}/start`).then(r => r.data);
export const stopCampaign   = (id)   => api.post(`/api/campaigns/${id}/stop`).then(r => r.data);
export const deleteCampaign = (id)   => api.delete(`/api/campaigns/${id}`).then(r => r.data);

// ── Stats & Logs ──────────────────────────────────────────────────────────────
export const getStats = ()    => api.get("/api/stats").then(r => r.data);
export const getLogs  = (p)   => api.get("/api/logs", { params: p }).then(r => r.data);

export default api;
