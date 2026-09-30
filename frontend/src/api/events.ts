/** 活动与内容接口。 */

import { http } from './client'
import type {
  ContentDeployResult,
  ContentList,
  EventAdmin,
  EventPublic,
} from '@/types/api'

export async function listPublicEvents(): Promise<EventPublic[]> {
  const { data } = await http.get<{ events: EventPublic[] }>('/events')
  return data.events
}

export async function getPublicEvent(eventId: string): Promise<EventPublic> {
  const { data } = await http.get<{ event: EventPublic }>(`/events/${eventId}`)
  return data.event
}

export async function listAdminEvents(status?: string): Promise<EventAdmin[]> {
  const { data } = await http.get<{ events: EventAdmin[] }>('/admin/events', {
    params: status ? { status } : undefined,
  })
  return data.events
}

export async function getAdminEvent(eventId: string): Promise<EventAdmin> {
  const { data } = await http.get<{ event: EventAdmin }>(`/admin/events/${eventId}`)
  return data.event
}

export interface EventCreatePayload {
  id: string
  title: string
  summary?: string
  entry_path?: string
  submission_requires_login?: boolean
  submissions_open_at?: string | null
  submissions_close_at?: string | null
  max_submissions?: number | null
}

export async function createEvent(payload: EventCreatePayload): Promise<EventAdmin> {
  const { data } = await http.post<{ event: EventAdmin }>('/admin/events', payload)
  return data.event
}

export async function updateEvent(
  eventId: string,
  changes: Partial<Omit<EventCreatePayload, 'id'>> & { status?: string },
): Promise<EventAdmin> {
  // event_id 刻意不出现在可改字段里：它同时是 URL、内容目录名与数据目录名
  const { data } = await http.patch<{ event: EventAdmin }>(
    `/admin/events/${eventId}`,
    changes,
  )
  return data.event
}

export async function deleteEvent(eventId: string): Promise<void> {
  await http.delete(`/admin/events/${eventId}`)
}

export async function listContent(eventId: string): Promise<ContentList> {
  const { data } = await http.get<ContentList>(`/admin/events/${eventId}/content`)
  return data
}

export async function deployContent(
  eventId: string,
  archive: File,
): Promise<ContentDeployResult> {
  const form = new FormData()
  form.append('file', archive)
  const { data } = await http.post<ContentDeployResult>(
    `/admin/events/${eventId}/content`,
    form,
  )
  return data
}
