// 在线会话记忆迁移 API 服务（对齐 /v1/chat-migrations 路由与 Schema 31 账本契约）。
import { api } from "@/services/api";

export interface MigrationChatCandidate {
  chat_id: string;
  display_name: string;
  platform: string;
  group_id: string;
  group_name: string;
  user_id: string;
  sender_name: string;
  unified_msg_origin: string;
  is_group: boolean;
  transcript_message_count: number;
  paragraph_count: number;
  fact_claim_count: number;
  episode_count: number;
  image_occurrence_count?: number;
  image_observation_count: number;
  total_records: number;
  last_active_at: number;
}

export interface MigrationChatCounts {
  paragraphs?: number;
  facts?: number;
  episodes?: number;
  image_occurrences?: number;
  image_observations?: number;
  [key: string]: unknown;
}

export interface MigrationTranscriptCounts {
  source?: { messages?: number; has_session?: number };
  target?: { messages?: number; has_session?: number };
}

export interface ChatMigrationJob {
  job_id: string;
  source_chat_id: string;
  target_chat_id: string;
  status: "previewed" | "completed" | "restored" | "failed" | string;
  migrate_transcript: boolean;
  expected_fingerprint: string;
  target_has_existing_data: boolean;
  source_counts: MigrationChatCounts;
  target_counts: MigrationChatCounts;
  transcript_counts: MigrationTranscriptCounts;
  applied_result: Record<string, unknown>;
  restored_result: Record<string, unknown>;
  note: string;
  requested_by: string;
  error: string;
  created_at: number;
  updated_at: number;
  can_execute: boolean;
  can_recover: boolean;
}

export interface ListMigrationChatsResponse {
  success: boolean;
  items: MigrationChatCandidate[];
  count: number;
  error?: string;
}

export interface ListMigrationJobsResponse {
  success: boolean;
  items: ChatMigrationJob[];
  count: number;
  error?: string;
}

export interface MigrationJobActionResponse {
  success: boolean;
  job?: ChatMigrationJob;
  result?: Record<string, unknown>;
  requires_confirmation?: boolean;
  already_completed?: boolean;
  error?: string;
}

export const chatMigrationApi = {
  listChats(query = "", limit = 100, scope?: string) {
    const params = new URLSearchParams();
    if (query.trim()) params.set("query", query.trim());
    params.set("limit", String(limit));
    const qs = params.toString();
    return api.get<ListMigrationChatsResponse>(
      `/v1/chat-migrations/chats${qs ? `?${qs}` : ""}`,
      { scope },
    );
  },

  listJobs(limit = 30, scope?: string) {
    return api.get<ListMigrationJobsResponse>(`/v1/chat-migrations?limit=${limit}`, { scope });
  },

  preview(
    payload: {
      source_chat_id: string;
      target_chat_id: string;
      migrate_transcript: boolean;
      note?: string;
    },
    scope?: string,
  ) {
    return api.post<MigrationJobActionResponse>("/v1/chat-migrations/preview", payload, { scope });
  },

  execute(
    jobId: string,
    payload: { confirmed: boolean; refresh_fingerprint?: boolean },
    scope?: string,
  ) {
    return api.post<MigrationJobActionResponse>(
      `/v1/chat-migrations/${encodeURIComponent(jobId)}/execute`,
      payload,
      { scope },
    );
  },

  recover(jobId: string, scope?: string) {
    return api.post<MigrationJobActionResponse>(
      `/v1/chat-migrations/${encodeURIComponent(jobId)}/recover`,
      {},
      { scope },
    );
  },
};
