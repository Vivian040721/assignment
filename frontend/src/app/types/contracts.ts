export type {
  Action,
  Artifact,
  ArtifactText,
  BenchmarkFile,
  ConfigUpdate,
  ContractError,
  Conversation,
  ConversationSummary,
  DesktopAPI,
  EventItem,
  Job,
  JobEvent,
  Message,
  Paper,
  PipelineResult,
  PublicConfig,
  RankingDetails,
  ReplacementAttempt,
  ReportReference,
  ServiceInfo,
  Status,
  Summary,
  TargetCoverage,
  TargetCoverageEntry,
} from '../../../../shared/contracts';

import type { Conversation } from '../../../../shared/contracts';

export function isReadOnlyConversation(conversation: Conversation | null | undefined): boolean {
  return (conversation as (Conversation & { read_only?: boolean }) | null | undefined)?.read_only === true;
}
