export type UserRole = 'admin' | 'trader' | 'viewer'

export interface UserResponse {
  id: string
  username: string
  email: string
  role: UserRole
  is_active: boolean
  last_login: string | null
  created_at: string
}

export interface TokenResponse {
  access_token: string
  refresh_token: string
  token_type: string
  expires_in: number
  user: UserResponse
}

export interface RefreshTokenResponse {
  access_token: string
  refresh_token: string
  token_type: string
  expires_in: number
}

export const ROLE_LABELS: Record<UserRole, string> = {
  admin: 'Admin',
  trader: 'Trader',
  viewer: 'Viewer',
}

export function canControl(role: UserRole): boolean {
  return role === 'admin' || role === 'trader'
}

export function isAdmin(role: UserRole): boolean {
  return role === 'admin'
}
