// Types for the Sentinel Vault API (backend/app/api). Keep in step with the Pydantic schemas.

export type Me = {
  id: string
  email: string
  name: string
  is_admin: boolean
  is_active: boolean
  created_at: string
  groups: string[]
}

export type UserRow = Omit<Me, 'groups'> & { group_ids: string[] }

export type GroupRow = { id: string; name: string; description: string; member_ids: string[] }

export type Folder = {
  id: string
  path: string
  name: string
  description: string
  parent_id: string | null
  documents: number
}

/** A folder as the signed-in user sees it, with the knowledge source it belongs to. */
export type MyFolder = Folder & { can_upload: boolean; source: string | null }

/** A knowledge source the signed-in user can read something in (GET /api/sources). */
export type SourceInfo = {
  name: string
  title: string
  description: string
  homepage: string | null
  license: string | null
  folder: string
  folders: number
  documents: number
  suggestions: string[]
}

export type Relation = 'viewer' | 'editor'

export type GrantRow = {
  id: string
  folder_id: string
  subject_type: 'user' | 'group'
  subject_id: string
  relation: Relation
  created_at: string
}

export type DocumentRow = {
  id: string
  folder_id: string
  rel_path: string
  title: string
  mime_type: string
  size_bytes: number
  status: 'pending' | 'indexing' | 'indexed' | 'failed'
  error: string | null
  chunk_count: number
  source: 'scan' | 'upload'
  indexed_at: string | null
  updated_at: string
}

export type DocumentDetail = DocumentRow & { chunks: { id: string; heading: string; text: string }[] }

export type AuditRow = {
  id: number
  ts: string
  actor_id: string | null
  actor: string | null
  action: string
  target: string
  outcome: 'ok' | 'denied' | 'error'
  detail: Record<string, unknown>
}

export type Health = { status: string; authz: string; gate: string; llm: string; embeddings: string; vectors: number }

// ---- The /api/ask event stream -----------------------------------------------------------

/** 'pending' exists only in the UI: no event yet for that step. */
export type StepStatus = 'pending' | 'running' | 'pass' | 'warn' | 'fail' | 'skip'
export type StepId = 'gate' | 'access' | 'retrieve' | 'generate' | 'verify' | 'audit'

export type Citation = {
  n: number
  chunk_id: string
  document_id: string
  title: string
  heading: string
  folder: string
  path: string
  snippet: string
}

export type StepEvent = {
  type: 'step'
  id: StepId
  title: string
  status: Exclude<StepStatus, 'pending'>
  summary: string
  // Shape depends on the step; see StepDetail in components/Trace.tsx.
  data: Record<string, any> | null
  ms: number | null
}

export type AskEvent =
  | { type: 'start'; request_id: string }
  | StepEvent
  | { type: 'token'; text: string }
  | { type: 'reset' }
  | {
      type: 'answer'
      allowed: boolean
      text: string
      citations: Citation[]
      reasons?: string[]
      no_answer?: boolean
      grounded?: boolean
    }
  | { type: 'done'; request_id: string; outcome: 'answered' | 'denied'; latency_ms: number }
