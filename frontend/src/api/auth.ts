/** 认证相关接口。 */

import { http } from './client'
import type { User } from '@/types/api'

export interface LoginPayload {
  username: string
  password: string
}

export interface RegisterPayload {
  username: string
  password: string
  display_name?: string
  email?: string
  invite_code?: string
}

export async function login(payload: LoginPayload): Promise<User> {
  const { data } = await http.post<{ user: User }>('/auth/login', payload)
  return data.user
}

export async function register(payload: RegisterPayload): Promise<User> {
  const { data } = await http.post<{ user: User }>('/auth/register', payload)
  return data.user
}

export async function logout(): Promise<void> {
  await http.post('/auth/logout')
}

export async function me(): Promise<User> {
  const { data } = await http.get<{ user: User }>('/auth/me')
  return data.user
}

export async function changePassword(
  currentPassword: string,
  newPassword: string,
): Promise<void> {
  await http.post('/auth/password', {
    current_password: currentPassword,
    new_password: newPassword,
  })
}

/** 发起自助找回。无论邮箱是否存在都返回成功（防账号枚举）。 */
export async function forgotPassword(email: string): Promise<void> {
  await http.post('/auth/forgot-password', { email })
}

/** 凭一次性令牌重置口令。两条签发路径共用这个端点。 */
export async function resetPassword(token: string, newPassword: string): Promise<void> {
  await http.post('/auth/reset', { token, new_password: newPassword })
}

export async function requestEmailVerification(): Promise<void> {
  await http.post('/auth/verify-email/request')
}

export async function verifyEmail(token: string): Promise<void> {
  await http.post('/auth/verify-email', { token })
}
