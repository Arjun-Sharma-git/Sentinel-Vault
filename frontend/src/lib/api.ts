// Typed client for the Sentinel Vault API.
//
// Auth is a server-side session in an HttpOnly cookie: the browser sends it automatically and
// JavaScript can't read it. Every request also carries X-Vault-Request, which the API requires
// on state-changing calls as CSRF protection.

import { readEventStream } from './sse'
import type {
  AskEvent,
  AuditRow,
  DocumentDetail,
  DocumentRow,
  Folder,
  GrantRow,
  GroupRow,
  Health,
  Me,
  MyFolder,
  SourceInfo,
  Relation,
  UserRow,
} from './types'

export type * from './types'

export class ApiError extends Error {
  readonly status: number

  constructor(status: number, message: string) {
    super(message)
    this.name = 'ApiError'
    this.status = status
  }
}

const BASE_HEADERS = { 'X-Vault-Request': '1' }

async function errorDetail(res: Response): Promise<string> {
  try {
    const { detail } = await res.json()
    if (typeof detail === 'string') return detail
    if (Array.isArray(detail)) return detail.map((d) => d.msg ?? JSON.stringify(d)).join('; ')
  } catch {
    // not JSON
  }
  return res.statusText || `Request failed (${res.status})`
}

async function request<T>(path: string, init: RequestInit = {}): Promise<T> {
  const isForm = init.body instanceof FormData
  const res = await fetch(path, {
    ...init,
    credentials: 'same-origin',
    headers: { ...BASE_HEADERS, ...(isForm || !init.body ? {} : { 'Content-Type': 'application/json' }), ...init.headers },
  })
  if (!res.ok) {
    if (res.status === 401 && !path.startsWith('/api/auth/')) window.location.assign('/login')
    throw new ApiError(res.status, await errorDetail(res))
  }
  return (res.status === 204 ? undefined : await res.json()) as T
}

const json = (method: string, body?: unknown): RequestInit => ({ method, body: JSON.stringify(body) })

export const api = {
  health: () => request<Health>('/api/health'),

  // auth
  login: (email: string, password: string) => request<Me>('/api/auth/login', json('POST', { email, password })),
  logout: () => request<void>('/api/auth/logout', { method: 'POST' }),
  me: () => request<Me>('/api/auth/me'),

  // library
  folders: () => request<MyFolder[]>('/api/folders'),
  sources: () => request<SourceInfo[]>('/api/sources'),
  folderDocuments: (folderId: string) => request<DocumentRow[]>(`/api/folders/${folderId}/documents`),
  document: (id: string) => request<DocumentDetail>(`/api/documents/${id}`),
  upload: (folderId: string, file: File) => {
    const form = new FormData()
    form.append('file', file)
    return request<DocumentRow>(`/api/folders/${folderId}/upload`, { method: 'POST', body: form })
  },
  deleteDocument: (id: string) => request<void>(`/api/documents/${id}`, { method: 'DELETE' }),

  // admin
  admin: {
    users: () => request<UserRow[]>('/api/admin/users'),
    createUser: (body: { email: string; name: string; password: string; is_admin: boolean; group_ids: string[] }) =>
      request<UserRow>('/api/admin/users', json('POST', body)),
    updateUser: (id: string, body: Partial<{ name: string; password: string; is_admin: boolean; is_active: boolean; group_ids: string[] }>) =>
      request<UserRow>(`/api/admin/users/${id}`, json('PATCH', body)),
    groups: () => request<GroupRow[]>('/api/admin/groups'),
    createGroup: (name: string, description: string) => request<GroupRow>('/api/admin/groups', json('POST', { name, description })),
    setMembers: (groupId: string, userIds: string[]) => request<GroupRow>(`/api/admin/groups/${groupId}/members`, json('PUT', userIds)),
    deleteGroup: (groupId: string) => request<void>(`/api/admin/groups/${groupId}`, { method: 'DELETE' }),
    folders: () => request<Folder[]>('/api/admin/folders'),
    createFolder: (path: string, description: string) => request<Folder>('/api/admin/folders', json('POST', { path, description })),
    grants: () => request<GrantRow[]>('/api/admin/grants'),
    grant: (body: { folder_id: string; subject_type: 'user' | 'group'; subject_id: string; relation: Relation }) =>
      request<GrantRow>('/api/admin/grants', json('POST', body)),
    revoke: (grantId: string) => request<void>(`/api/admin/grants/${grantId}`, { method: 'DELETE' }),
    access: (userId: string) => request<{ viewer: string[]; editor: string[] }>(`/api/admin/access/${userId}`),
    documents: () => request<{ documents: DocumentRow[]; jobs_pending: number; vectors: number }>('/api/admin/documents'),
    scan: () => request<Record<string, unknown>>('/api/admin/scan', { method: 'POST' }),
    reindex: (id: string) => request<{ status: string }>(`/api/admin/documents/${id}/reindex`, { method: 'POST' }),
    audit: (action?: string) => request<AuditRow[]>(`/api/admin/audit${action ? `?action=${encodeURIComponent(action)}` : ''}`),
  },
}

/** POST /api/ask and call `onEvent` for each streamed event. `sources` limits the search to
 * those knowledge sources; omitted, everything the user can read is searched. */
export async function ask(
  question: string,
  onEvent: (event: AskEvent) => void,
  signal?: AbortSignal,
  sources?: string[],
) {
  const res = await fetch('/api/ask', {
    method: 'POST',
    credentials: 'same-origin',
    headers: { ...BASE_HEADERS, 'Content-Type': 'application/json' },
    body: JSON.stringify(sources?.length ? { question, sources } : { question }),
    signal,
  })
  if (!res.ok || !res.body) {
    if (res.status === 401) window.location.assign('/login')
    throw new ApiError(res.status, await errorDetail(res))
  }
  await readEventStream(res.body, (data) => onEvent(JSON.parse(data) as AskEvent))
}
