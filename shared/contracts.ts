/** v0.2.2 renderer/preload contract. No backend bearer token or saved key crosses this boundary. */
export type Status = 'queued' | 'running' | 'completed' | 'partial' | 'failed' | 'cancelled' | string;
export type Action = 'research' | 'chat';
export interface PublicConfig {
  provider: string; base_url: string; model: string; api_key_configured: boolean;
  openalex_api_key_configured: boolean; thinking_enabled: boolean; http_proxy: string;
  default_top_k: number; default_execution_mode: 'concurrent' | 'sequential';
  concurrency: number; llm_timeout: number; http_timeout: number;
}
export interface ConfigUpdate {
  base_url?: string; model?: string; api_key?: string; clear_api_key?: boolean;
  openalex_api_key?: string; clear_openalex_api_key?: boolean; thinking_enabled?: boolean;
  http_proxy?: string; default_top_k?: number; concurrency?: number;
  llm_timeout?: number; http_timeout?: number;
}
export interface ServiceInfo {
  version: string; api_version: number; pid: number; active_jobs: number;
  data_root: string; config_path: string;
}
export interface EventItem {
  seq: number; timestamp: string; kind: string; message?: string;
  stage?: string; data?: Record<string, unknown>;
}
export interface JobEvent { job_id: string; event?: EventItem; done?: boolean; error?: string; }
export interface Artifact {
  artifact_id: string; name: string; kind: 'pdf'|'markdown'|'summary'|'report'|'run';
  paper_id?: string | null; url?: string; download_url?: string;
}
export interface RankingDetails {
  ranker?: string; algorithm?: string; keyword_rank?: number; semantic_rank?: number;
  cosine_similarity?: number; rrf_rank?: number; rrf_score?: number; rrf_k?: number;
  rrf_weights?: number[]; model_name?: string; score_semantics?: string;
  title_coverage?: number; abstract_coverage?: number; topic_coverage?: number;
  relative_recency?: number; query_tokens?: string[]; title_hits?: string[];
  abstract_hits?: string[]; [key: string]: unknown;
}
export interface ContractError {
  code?: string; stage?: string; message: string; paper_id?: string | null;
  retryable?: boolean; details?: Record<string, unknown>; [key: string]: unknown;
}
export interface Paper {
  paper_id: string; title: string; authors: string[]; year?: number | null;
  abstract?: string; doi?: string | null; arxiv_id?: string | null; paper_url?: string | null;
  sources: string[]; initial_score?: number | null; final_score?: number | null;
  rank?: number | null; selection_reason?: string | null; ranking_reason?: string | null;
  ranking_details?: RankingDetails; status: string;
  pdf_path?: string | null; markdown_path?: string | null; fulltext_quality?: Record<string, unknown>;
  metadata?: Record<string, unknown>; error?: string | null; errors?: ContractError[];
}
export interface Summary {
  paper_id: string; title: string; summary_scope: string; markdown: string;
  overview?: string; deep_analysis?: string;
  research_question?: string; core_idea?: string; methods?: string[]; experiments?: string[];
  findings?: string[]; limitations?: string[]; relevance?: string; evidence?: Record<string, unknown>[];
  author_claims?: string[]; system_inferences?: string[]; speculative_ideas?: string[];
  warnings?: string[]; errors?: ContractError[]; summary_path?: string | null;
}
export interface ReportReference { paper_id: string; title: string; paper_url?: string | null; doi?: string | null; }
export interface PipelineResult {
  run_id: string; idea: string; query: string; mode: string; execution_mode: string; top_k: number;
  status: Status; papers: Paper[]; selected_papers: Paper[]; summaries: Summary[];
  report?: {
    markdown: string; overview?: string; report_path?: string | null; idea?: string;
    related_work?: string[]; comparison?: Record<string, unknown>[]; researched_questions?: string[];
    limitations?: string[]; research_directions?: string[]; references?: ReportReference[];
    errors?: ContractError[]; [key: string]: unknown;
  } | null;
  source_counts?: Record<string, number>; metrics: Record<string, unknown>;
  errors: ContractError[];
  timings?: { stage: string; duration_seconds?: number; wall_seconds?: number; request_count?: number; success_count?: number; error_count?: number; [key: string]: unknown }[];
  tool_calls?: { tool: string; status: string; duration_seconds: number; arguments: Record<string, unknown>; controller?: string; error?: string | null }[];
}
export interface Message {
  id: string; role: 'user'|'assistant'|'status'|string; content: string;
  kind: string; status?: Status; created_at?: string; job_id?: string;
}
export interface ConversationSummary {
  conversation_id: string; title: string; updated_at: string; status: Status; job_id: string | null;
  read_only?: boolean;
}
export interface Conversation extends ConversationSummary {
  messages: Message[]; result: PipelineResult | null; result_job_id?: string | null;
}
export interface SendMessage {
  text: string; conversation_id?: string | null; action: Action; query?: string;
  top_k?: number; execution_mode?: 'sequential'|'concurrent'; mode?: 'live'|'demo';
}
export interface Job {
  job_id: string; session_id: string; conversation_id?: string; action?: Action; idea: string;
  status: Status; result: PipelineResult | null; conversation?: Conversation; events: EventItem[];
  artifacts: Artifact[]; error: string | null; created_at: string; updated_at: string;
}
export interface ArtifactText { text: string; name: string; kind: string; truncated: boolean; }
export interface BenchmarkFile { name: string; data: Record<string, unknown>; }
export interface DesktopAPI {
  info(): Promise<ServiceInfo>;
  config(): Promise<PublicConfig>;
  saveConfig(values: ConfigUpdate): Promise<PublicConfig>;
  conversations(): Promise<ConversationSummary[]>;
  conversation(id: string): Promise<Conversation>;
  sendMessage(values: SendMessage): Promise<Job>;
  job(id: string): Promise<Job>;
  cancelJob(id: string): Promise<Job>;
  subscribeJob(id: string, after: number, callback: (event: JobEvent) => void): () => void;
  readArtifact(jobId: string, artifactId: string): Promise<ArtifactText>;
  openArtifact(jobId: string, artifactId: string): Promise<void>;
  exportArtifact(jobId: string, artifactId: string): Promise<{ cancelled: boolean; path?: string }>;
  openExternal(url: string): Promise<void>;
  openDataDirectory(): Promise<void>;
  copyText(text: string): Promise<void>;
  loadBenchmark(): Promise<BenchmarkFile | null>;
}
