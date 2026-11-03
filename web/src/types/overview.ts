import type { PipelineState } from "./pipeline";

/** Unix seconds, or an ISO timestamp with an explicit offset. Null means unknown. */
export type OverviewTime = number | string | null;
export type OverviewStageKey = "connected" | "collecting" | "stored" | "knowledge";

export interface OverviewStage {
  key: OverviewStageKey;
  label: string;
  state: PipelineState;
  detail: string;
  last_arrival_at: OverviewTime;
  age_seconds: number | null;
  count_24h: number | null;
  total: number | null;
  processed: number | null;
  pending: number | null;
  last_processed_at: OverviewTime;
}

export interface OverviewChannel {
  key: string;
  label: string;
  state: PipelineState;
  headline: string;
  stages: OverviewStage[];
  notes: string[];
}

/** GET /api/v1/overview. No budget or count may substitute 0 for null. */
export interface OverviewResponse {
  generated_at: OverviewTime;
  summary: {
    state: PipelineState;
    headline: string;
    problems: { channel: string; text: string; next_step: string }[];
  };
  channels: OverviewChannel[];
  model_budget: {
    deepseek_official: {
      currency: string | null;
      balance_micros: number | null;
      observed_at: OverviewTime;
    } | null;
    free_pools: {
      model: string;
      remaining_tokens: number | null;
      observed_at: OverviewTime;
      expires_on: string | null;
    }[];
    note: string;
  };
  history: {
    alibaba: {
      messages: number | null;
      conversations: number | null;
      earliest_sent_at: OverviewTime;
      latest_sent_at: OverviewTime;
      by_month: { month: string; count: number | null }[];
      completeness: string | null;
      detail: string;
    };
  };
}
