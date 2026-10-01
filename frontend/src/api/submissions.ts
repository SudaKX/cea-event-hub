/** 提交与附件接口。 */

import { API_BASE, http } from './client'
import type { Submission, SubmissionCreated } from '@/types/api'

export interface SubmitOptions {
  payload?: Record<string, unknown>
  files?: File[]
  kind?: string
  clientId?: string
  idempotencyKey?: string
  onUploadProgress?: (loaded: number, total: number | null) => void
  signal?: AbortSignal
}

function baseParams(kind?: string, clientId?: string) {
  const params: Record<string, string> = {}
  if (kind) params.kind = kind
  if (clientId) params.client_id = clientId
  return params
}

/**
 * 提交信息与文件。
 *
 * **活动页作者视角永远是一次调用**：带文件时走 `:files` 端点（它是超集），
 * 不带文件时走 JSON 端点。这个分叉是内部细节，不暴露给作者。
 */
export async function submit(
  eventId: string,
  options: SubmitOptions = {},
): Promise<SubmissionCreated> {
  const { payload = {}, files = [], kind, clientId, idempotencyKey, signal } = options

  const headers: Record<string, string> = {}
  if (idempotencyKey) headers['Idempotency-Key'] = idempotencyKey

  if (files.length === 0) {
    const { data } = await http.post<SubmissionCreated>(
      `/events/${eventId}/submissions`,
      payload,
      { params: baseParams(kind, clientId), headers, signal },
    )
    return data
  }

  const form = new FormData()
  // 字段放在名为 payload 的文本部分，文件放在可重复的 files 部分
  form.append('payload', JSON.stringify(payload))
  for (const file of files) form.append('files', file)

  const { data } = await http.post<SubmissionCreated>(
    `/events/${eventId}/submissions:files`,
    form,
    {
      params: baseParams(kind, clientId),
      headers,
      signal,
      onUploadProgress: (event) => {
        options.onUploadProgress?.(
          event.loaded,
          event.total ?? null,
        )
      },
    },
  )
  return data
}

export async function mySubmissions(eventId?: string): Promise<Submission[]> {
  const { data } = await http.get<{ submissions: Submission[] }>('/me/submissions', {
    params: eventId ? { event_id: eventId } : undefined,
  })
  return data.submissions
}

export interface AdminSubmissionFilters {
  kind?: string
  /** 审核状态码，取值见 @/domain/submission */
  status?: number
  submitter?: string
  page?: number
  page_size?: number
}

export async function listEventSubmissions(
  eventId: string,
  filters: AdminSubmissionFilters = {},
): Promise<{ submissions: Submission[]; total: number }> {
  const { data } = await http.get<{ submissions: Submission[]; total: number }>(
    `/admin/events/${eventId}/submissions`,
    { params: filters },
  )
  return data
}

export async function reviewSubmission(
  submissionId: number,
  status: number,
): Promise<Submission> {
  const { data } = await http.patch<{ submission: Submission }>(
    `/admin/submissions/${submissionId}`,
    { status },
  )
  return data.submission
}

export async function deleteSubmission(submissionId: number): Promise<void> {
  await http.delete(`/admin/submissions/${submissionId}`)
}

export async function deleteSubmissions(ids: number[]): Promise<number> {
  const { data } = await http.post<{ deleted: number }>('/admin/submissions:delete', {
    ids,
  })
  return data.deleted
}

/**
 * 附件下载地址。
 *
 * 指向鉴权端点而不是 `/data/**` —— 后者从不对外提供，这是唯一的读取路径。
 * 因为会话在 Cookie 里，直接用 `<a href>` 打开即可，无需自行附加凭据。
 */
export function attachmentUrl(submissionId: number, fileId: number): string {
  return `${API_BASE}/submissions/${submissionId}/files/${fileId}`
}
