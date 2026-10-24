export type {
  Action,
  Artifact,
  ArtifactText,
  BenchmarkFile,
  ConfigUpdate,
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
  ServiceInfo,
  Status,
  Summary,
} from '../../../../shared/contracts';

import type { Conversation } from '../../../../shared/contracts';

export function isReadOnlyConversation(conversation: Conversation | null | undefined): boolean {
  return (conversation as (Conversation & { read_only?: boolean }) | null | undefined)?.read_only === true;
}
