/** 邀请码相关接口。 */
import { http } from './client'
import type { InvitationCode, PlatformSwitches } from '@/types/api'

/* ------------------------------------------------------------------ */
/* 用户侧：自己名下的码                                                */
/* ------------------------------------------------------------------ */

export async function listMyInvitations(): Promise<InvitationCode[]> {
  const { data } = await http.get<{ invitations: InvitationCode[] }>('/invitations')
  return data.invitations
}

export async function issueInvitation(name: string): Promise<InvitationCode> {
  const { data } = await http.post<{ invitation: InvitationCode }>('/invitations', {
    name,
  })
  return data.invitation
}

export async function deleteInvitation(id: number): Promise<void> {
  await http.delete(`/invitations/${id}`)
}

/* ------------------------------------------------------------------ */
/* 管理侧：全部                                                        */
/* ------------------------------------------------------------------ */

export interface CreateInvitationPayload {
  token?: string
  name: string
  days: number
  max_uses: number
}

export async function listAllInvitations(): Promise<InvitationCode[]> {
  const { data } = await http.get<{ invitations: InvitationCode[] }>(
    '/admin/invitations',
  )
  return data.invitations
}

export async function createInvitation(
  payload: CreateInvitationPayload,
): Promise<InvitationCode> {
  const { data } = await http.post<{ invitation: InvitationCode }>(
    '/admin/invitations',
    payload,
  )
  return data.invitation
}

export async function revokeInvitation(id: number): Promise<InvitationCode> {
  const { data } = await http.post<{ invitation: InvitationCode }>(
    `/admin/invitations/${id}/revoke`,
  )
  return data.invitation
}

export async function readSwitches(): Promise<PlatformSwitches> {
  const { data } = await http.get<PlatformSwitches>('/admin/switches')
  return data
}

export async function writeSwitch(
  key: keyof PlatformSwitches,
  enabled: boolean,
): Promise<PlatformSwitches> {
  const { data } = await http.put<PlatformSwitches>('/admin/switches', {
    key,
    enabled,
  })
  return data
}
