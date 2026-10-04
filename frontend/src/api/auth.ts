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
  /** **必填**：注册要经邮箱验证才算完成，而验证的对象就是它 */
  email: string
}

export interface RegistrationStarted {
  /**
   * 这是一次**重入** —— 同一对用户名与邮箱已有一条待验证的占位，本次没有新建、
   * 也没有重发邮件。界面据此把文案改成"我们已经发过一封"。
   */
  ongoing: boolean
}

export async function login(payload: LoginPayload): Promise<User> {
  const { data } = await http.post<{ user: User }>('/auth/login', payload)
  return data.user
}

/**
 * 提交注册（两阶段的第一步）。
 *
 * **返回的不是用户** —— 账号要到邮件链接被打开才创建。这里只拿到"请求已受理"，
 * 以及它是不是一次重入。
 */
export async function register(payload: RegisterPayload): Promise<RegistrationStarted> {
  const { data } = await http.post<RegistrationStarted>('/auth/register', payload)
  return data
}

/** 凭邮件里的链接完成注册（两阶段的第二步）。成功后**不会自动登录**。 */
export async function verifyRegistration(token: string): Promise<void> {
  await http.post('/auth/register/verify', { token })
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

/** 凭一次性令牌重置密码。两条签发路径共用这个端点。 */
export async function resetPassword(token: string, newPassword: string): Promise<void> {
  await http.post('/auth/reset', { token, new_password: newPassword })
}

export async function requestEmailVerification(): Promise<void> {
  await http.post('/auth/verify-email/request')
}

export async function verifyEmail(token: string): Promise<void> {
  await http.post('/auth/verify-email', { token })
}
