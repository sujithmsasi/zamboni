// Client-side-only demo gate -- there is no backend session behind this.
// api/deps.py::get_current_user() is still the real actor identity used for
// audit trails (env-var stub, contracts.md D5's OIDC seam for later); this
// is a separate, local-only front door with a single static credential
// pair, purely to keep the app from being wide open when demoed. Do not
// treat isAuthenticated() as a security boundary -- it's a localStorage
// flag, trivially bypassed from devtools.
const STORAGE_KEY = 'zamboni_demo_auth';
const STORAGE_USER_KEY = 'zamboni_demo_auth_user';

export const DEMO_CREDENTIALS = {
  username: 'admin',
  password: 'Zamboni@2026',
} as const;

export function isAuthenticated(): boolean {
  return localStorage.getItem(STORAGE_KEY) === 'true';
}

export function login(username: string, password: string): boolean {
  const ok =
    username.trim().toLowerCase() === DEMO_CREDENTIALS.username &&
    password === DEMO_CREDENTIALS.password;
  if (ok) {
    localStorage.setItem(STORAGE_KEY, 'true');
    localStorage.setItem(STORAGE_USER_KEY, username.trim());
  }
  return ok;
}

export function logout(): void {
  localStorage.removeItem(STORAGE_KEY);
  localStorage.removeItem(STORAGE_USER_KEY);
}

export function getDemoUser(): string | null {
  return localStorage.getItem(STORAGE_USER_KEY);
}
