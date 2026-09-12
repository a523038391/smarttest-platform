export interface AuthUser {
  id: string
  username: string
  display_name: string
  role: 'ADMIN'
  is_active: boolean
}

export interface AuthStatus {
  setup_required: boolean
  auth_required: boolean
  authenticated: boolean
  user: AuthUser | null
}

export interface LoginCredentials {
  username: string
  password: string
}

export interface AdminSetupInput extends LoginCredentials {
  display_name: string
}