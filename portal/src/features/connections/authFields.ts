/**
 * Form fields per auth scheme — mirrors the OpenAPI `AuthConfig` oneOf members.
 * `secret: true` fields are write-only: they are sent once and never displayed again.
 */
import type { AuthType } from '../../api/types';

export interface AuthField {
  key: string;
  label: string;
  kind: 'text' | 'secret' | 'textarea' | 'secret-textarea' | 'select' | 'list';
  required?: boolean;
  options?: string[];
  placeholder?: string;
  default?: string;
  hint?: string;
}

export const AUTH_LABELS: Record<AuthType, string> = {
  api_key: 'API key',
  bearer: 'Bearer token',
  basic: 'Username and password',
  oauth2_client_credentials: 'OAuth 2.0 client credentials',
  oauth2_authorization_code: 'OAuth 2.0 sign-in (authorization code)',
  hmac: 'HMAC signature',
  mtls: 'Mutual TLS certificate',
  aws_sigv4: 'AWS Signature V4',
  azure_ad_client: 'Microsoft Entra ID (client secret)',
  none: 'No authentication',
};

export const AUTH_FIELDS: Record<AuthType, AuthField[]> = {
  api_key: [
    { key: 'key', label: 'API key', kind: 'secret', required: true },
    { key: 'placement', label: 'Send in', kind: 'select', options: ['header', 'query'], default: 'header' },
    { key: 'name', label: 'Header or parameter name', kind: 'text', default: 'Authorization', placeholder: 'x-api-key' },
    { key: 'prefix', label: 'Value prefix', kind: 'text', placeholder: 'Bearer ' },
  ],
  bearer: [{ key: 'token', label: 'Token', kind: 'secret', required: true }],
  basic: [
    { key: 'username', label: 'Username', kind: 'text', required: true },
    { key: 'password', label: 'Password', kind: 'secret', required: true },
  ],
  oauth2_client_credentials: [
    { key: 'token_url', label: 'Token URL', kind: 'text', required: true, placeholder: 'https://login.example.com/oauth2/token' },
    { key: 'client_id', label: 'Client ID', kind: 'text', required: true },
    { key: 'client_secret', label: 'Client secret', kind: 'secret', required: true },
    { key: 'scopes', label: 'Scopes', kind: 'list', hint: 'Comma separated.' },
    { key: 'audience', label: 'Audience', kind: 'text' },
  ],
  oauth2_authorization_code: [
    { key: 'client_id', label: 'Client ID', kind: 'text', required: true },
    { key: 'client_secret', label: 'Client secret', kind: 'secret', required: true },
    { key: 'scopes', label: 'Scopes', kind: 'list', hint: 'Comma separated, e.g. https://www.googleapis.com/auth/gmail.modify' },
    { key: 'authorize_url', label: 'Authorize URL', kind: 'text' },
    { key: 'token_url', label: 'Token URL', kind: 'text' },
  ],
  hmac: [
    { key: 'secret', label: 'Signing secret', kind: 'secret', required: true },
    { key: 'algorithm', label: 'Algorithm', kind: 'select', options: ['sha256', 'sha512'], default: 'sha256' },
    { key: 'header', label: 'Signature header', kind: 'text', default: 'X-Signature' },
  ],
  mtls: [
    { key: 'client_cert_pem', label: 'Client certificate (PEM)', kind: 'textarea', required: true, placeholder: '-----BEGIN CERTIFICATE-----' },
    { key: 'client_key_pem', label: 'Client private key (PEM)', kind: 'secret-textarea', required: true, placeholder: '-----BEGIN PRIVATE KEY-----' },
    { key: 'ca_cert_pem', label: 'CA certificate (PEM)', kind: 'textarea' },
  ],
  aws_sigv4: [
    { key: 'access_key_id', label: 'Access key ID', kind: 'text', required: true },
    { key: 'secret_access_key', label: 'Secret access key', kind: 'secret', required: true },
    { key: 'session_token', label: 'Session token', kind: 'secret' },
    { key: 'region', label: 'Region', kind: 'text', required: true, default: 'me-central-1' },
    { key: 'role_arn', label: 'Role ARN to assume', kind: 'text' },
  ],
  azure_ad_client: [
    { key: 'tenant_id', label: 'Tenant ID', kind: 'text', required: true },
    { key: 'client_id', label: 'Client ID', kind: 'text', required: true },
    { key: 'client_secret', label: 'Client secret', kind: 'secret', required: true },
    { key: 'scope', label: 'Scope', kind: 'text', placeholder: 'https://cognitiveservices.azure.com/.default' },
  ],
  none: [],
};

/** Field whose last four characters become the `secret_hint`. */
export const PRIMARY_SECRET: Record<AuthType, string | null> = {
  api_key: 'key',
  bearer: 'token',
  basic: 'password',
  oauth2_client_credentials: 'client_secret',
  oauth2_authorization_code: 'client_secret',
  hmac: 'secret',
  mtls: 'client_key_pem',
  aws_sigv4: 'secret_access_key',
  azure_ad_client: 'client_secret',
  none: null,
};

/** Build an `AuthConfig` payload from form values (drops empty optional values). */
export function buildAuth(type: AuthType, values: Record<string, string>): Record<string, unknown> {
  const out: Record<string, unknown> = { type };
  for (const f of AUTH_FIELDS[type]) {
    const v = (values[f.key] ?? f.default ?? '').trim();
    if (!v) continue;
    out[f.key] = f.kind === 'list' ? v.split(',').map((s) => s.trim()).filter(Boolean) : v;
  }
  return out;
}

export function missingAuth(type: AuthType, values: Record<string, string>): string[] {
  return AUTH_FIELDS[type].filter((f) => f.required && !(values[f.key] ?? f.default ?? '').trim()).map((f) => f.label);
}
