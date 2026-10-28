/** v0.3.0 renderer/preload contract. No backend bearer token or saved key crosses this boundary. */
export type ExecutionStatus = 'pending' | 'running' | 'completed' | 'partial_success' | 'failed' | 'cancelled';
export type LegacyExecutionStatus = 'queued' | 'partial' | 'demo_completed';
export type PipelineStageStatus = 'searching' | 'searched' | 'selected' | 'fulltexts_ready' | 'summarized';
export type Status = ExecutionStatus | LegacyExecutionStatus | PipelineStageStatus | string;
export type DeliveryStatus = 'result' | 'preliminary_notes' | 'no_result';
export type ExecutionMode = 'auto' | 'parallel' | 'sequential';
export type ResolvedExecutionMode = 'sequential' | 'concurrent';
export type PreferredPaperCount = 3 | 5 | 8;
export type PdfAcquisitionStatus = 'not_attempted' | 'downloaded' | 'unavailable' | 'failed';
export type PdfConversionStatus = 'not_attempted' | 'converted' | 'partial' | 'failed';
export type EvidenceLevel = 'metadata_only' | 'abstract_only' | 'partial_fulltext' | 'fulltext';
export type Action = 'research' | 'chat';
export interface PublicConfig {
  provider: string; base_url: string; model: string; api_key_configured: boolean;
  openalex_api_key_configured: boolean; thinking_enabled: boolean; http_proxy: string;
  default_top_k: number; default_execution_mode: ExecutionMode | 'concurrent';
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
  artifact_id: string; name: string; kind: 'pdf'|'markdown'|'summary'|'report'|'run'|'chunks'|'audit'|string;
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
export interface TargetCoverageEntry {
  target: string; normalized?: string;
  status?: 'title_direct' | 'title_related' | 'abstract_mention' | 'unresolved' | 'title_match' | 'not_retrieved' | string;
  query?: string | null; query_covered?: boolean;
  query_execution?: Array<{
    source?: string; planned?: string; executed?: string; mode?: string;
    field?: string | null; result_count?: number | null; error_code?: string | null;
  }>;
  identity_status?: string; identity_confirmed?: boolean; identity_basis?: string[];
  direct_title_paper_ids?: string[]; related_title_paper_ids?: string[];
  abstract_mention_paper_ids?: string[];
  selector_pool_direct_paper_ids?: string[];
  selector_pool_related_paper_ids?: string[];
  selected_paper_ids?: string[]; analysis_paper_ids?: string[];
  complete_fulltext_paper_ids?: string[]; partial_fulltext_paper_ids?: string[];
  summarized_paper_ids?: string[]; fulltext_summary_paper_ids?: string[];
  partial_summary_paper_ids?: string[]; report_paper_ids?: string[];
  complete_summary_paper_ids?: string[]; usable_partial_summary_paper_ids?: string[];
  failed_summary_paper_ids?: string[]; missing_summary_paper_ids?: string[];
  summary_status_counts?: Record<string, number>;
  coverage_reason?: string;
}
export interface TargetCoverage {
  explicit_targets?: string[]; targets?: TargetCoverageEntry[];
  query_covered_targets?: string[]; query_uncovered_targets?: string[];
  selector_pool_ids?: string[]; selected_paper_ids?: string[];
  analysis_paper_ids?: string[]; report_reference_ids?: string[];
  stage?: string; report_valid?: boolean;
}
export interface ReplacementAttempt {
  attempt_order?: number; replacement_paper_id?: string; original_paper_id?: string | null;
  status?: string; complete?: boolean; readable?: boolean;
  failure_code?: string | null; failure_codes?: string[];
  in_analysis?: boolean; summary_scope?: string | null; summary_complete?: boolean;
  target_direct?: boolean; open_access_url?: boolean;
  [key: string]: unknown;
}
export interface PageMapping {
  page_number: number; text?: string; start_char?: number; end_char?: number;
}
export interface EvidenceReference {
  paper_id: string; pdf_artifact_id?: string | null; markdown_artifact_id?: string | null;
  page_number?: number | null; page_label?: string | null; section?: string | null;
  quote?: string | null; claim?: string | null; start_char?: number | null; end_char?: number | null;
  evidence_type?: 'author_claim' | 'system_inference' | 'research_idea';
}
export interface Paper {
  paper_id: string; title: string; authors: string[]; year?: number | null;
  abstract?: string; doi?: string | null; arxiv_id?: string | null; arxiv_version?: string | null;
  paper_url?: string | null; pdf_url?: string | null; pdf_urls?: string[];
  sources: string[]; source_records?: Record<string, unknown>[]; citation_count?: number | null;
  initial_score?: number | null; final_score?: number | null;
  rank?: number | null; selection_reason?: string | null; ranking_reason?: string | null;
  ranking_details?: RankingDetails; status: string;
  pdf_path?: string | null; markdown_path?: string | null;
  pdf_artifact_id?: string | null; markdown_artifact_id?: string | null;
  acquisition_status?: PdfAcquisitionStatus | null; conversion_status?: PdfConversionStatus | null;
  reading_status?: EvidenceLevel | null; page_map?: PageMapping[];
  fulltext_quality?: Record<string, unknown>;
  metadata?: Record<string, unknown>; error?: string | null; errors?: ContractError[];
}
export interface Summary {
  paper_id: string; title: string; summary_scope: string; markdown: string;
  overview?: string; deep_analysis?: string;
  research_question?: string; core_idea?: string; methods?: string[]; experiments?: string[];
  findings?: string[]; limitations?: string[]; relevance?: string; evidence?: EvidenceReference[];
  author_claims?: string[]; system_inferences?: string[]; speculative_ideas?: string[];
  warnings?: string[]; errors?: ContractError[]; summary_path?: string | null;
  summary_artifact_id?: string | null;
}
export interface Finding { statement: string; evidence: EvidenceReference[]; }
export interface KeyPaper {
  paper_id: string; selection_reason?: string; evidence_level?: EvidenceLevel;
}
export interface NextStep { action: string; rationale?: string; }
export interface ResearchLimitation { description: string; impact?: string; paper_ids?: string[]; }
export interface ResearchBrief {
  findings: Finding[]; key_papers: KeyPaper[]; next_step: NextStep;
  limitations: ResearchLimitation[]; report_artifact_id?: string | null;
}
export interface ReportReference { paper_id: string; title: string; paper_url?: string | null; doi?: string | null; }
export interface ResearchReport {
  markdown: string; overview?: string; report_path?: string | null; report_artifact_id?: string | null;
  idea?: string; related_work?: string[]; comparison?: Record<string, unknown>[];
  researched_questions?: string[]; limitations?: string[]; research_directions?: string[];
  references?: ReportReference[]; errors?: ContractError[]; [key: string]: unknown;
}
export interface PipelineResult {
  run_id: string; idea: string; query: string; mode: string;
  execution_mode: ResolvedExecutionMode; requested_execution_mode?: ExecutionMode | null; top_k: number;
  status: Status; delivery_status?: DeliveryStatus | null; brief?: ResearchBrief | null;
  papers: Paper[]; selected_papers: Paper[]; summaries: Summary[];
  report?: ResearchReport | null;
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
  top_k?: number; execution_mode?: ExecutionMode | 'concurrent'; mode?: 'live'|'demo';
}
export interface Job {
  job_id: string; session_id: string; conversation_id?: string; action?: Action; idea: string;
  status: Status; delivery_status?: DeliveryStatus | null;
  result: PipelineResult | null; conversation?: Conversation; events: EventItem[];
  artifacts: Artifact[]; error: string | null; created_at: string; updated_at: string;
}
export interface ArtifactText { text: string; name: string; kind: string; truncated: boolean; }
export interface PdfArtifact { data: Uint8Array; name: string; kind: 'pdf'; }
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
  readPdfArtifact?(jobId: string, artifactId: string): Promise<PdfArtifact>;
  openArtifact(jobId: string, artifactId: string): Promise<void>;
  exportArtifact(jobId: string, artifactId: string): Promise<{ cancelled: boolean; path?: string }>;
  openExternal(url: string): Promise<void>;
  openDataDirectory(): Promise<void>;
  copyText(text: string): Promise<void>;
  loadBenchmark(): Promise<BenchmarkFile | null>;
}
