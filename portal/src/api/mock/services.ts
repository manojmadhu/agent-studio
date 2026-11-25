/** `/catalog/services` data served by the mock backend. */
import type { FieldSpec, ServiceDefinition } from '../types';

const t = (key: string, label: string, x: Partial<FieldSpec> = {}): FieldSpec => ({ key, label, type: 'text', ...x });

export const SERVICES: ServiceDefinition[] = [
  { id: 'gmail', name: 'Gmail', category: 'mail', auth_types: ['oauth2_authorization_code'], settings_fields: [t('account', 'Mailbox address', { required: true })] },
  { id: 'microsoft_365_mail', name: 'Microsoft 365 mail', category: 'mail', auth_types: ['oauth2_authorization_code', 'azure_ad_client'], settings_fields: [t('account', 'Mailbox address', { required: true })] },
  { id: 'whatsapp_business', name: 'WhatsApp Business', category: 'messaging', auth_types: ['bearer'], settings_fields: [t('phone_number', 'Business number', { required: true }), t('phone_number_id', 'Phone number ID', { monospace: true, required: true }), t('business_account_id', 'Business account ID', { monospace: true })] },
  { id: 'slack', name: 'Slack', category: 'messaging', auth_types: ['bearer', 'oauth2_authorization_code'], settings_fields: [t('workspace', 'Workspace')] },
  { id: 'anthropic', name: 'Anthropic', category: 'model_provider', auth_types: ['api_key'], settings_fields: [t('account', 'Account or workspace')] },
  { id: 'openai', name: 'OpenAI', category: 'model_provider', auth_types: ['api_key', 'bearer'], settings_fields: [t('organization', 'Organization ID', { monospace: true }), t('project', 'Project')] },
  { id: 'azure_openai', name: 'Azure OpenAI', category: 'model_provider', auth_types: ['api_key', 'azure_ad_client'], settings_fields: [t('endpoint', 'Endpoint', { monospace: true, required: true, hint: 'https://<resource>.openai.azure.com' }), t('api_version', 'API version', { monospace: true, default: '2025-04-01' })] },
  { id: 'google_gemini', name: 'Google Gemini', category: 'model_provider', auth_types: ['api_key'], settings_fields: [t('project', 'Project')] },
  { id: 'ollama', name: 'Ollama', category: 'model_provider', auth_types: ['none', 'bearer'], settings_fields: [t('base_url', 'Base URL', { monospace: true, required: true, default: 'http://localhost:11434' })] },
  { id: 'openai_compatible', name: 'OpenAI-compatible endpoint', category: 'model_provider', auth_types: ['bearer', 'api_key', 'none'], settings_fields: [t('base_url', 'Base URL', { monospace: true, required: true })] },
  { id: 'postgresql', name: 'PostgreSQL', category: 'database', auth_types: ['basic', 'mtls'], settings_fields: [t('host', 'Host', { monospace: true, required: true }), { key: 'port', label: 'Port', type: 'number', default: 5432 }, t('database', 'Database', { monospace: true, required: true }), { key: 'sslmode', label: 'SSL mode', type: 'select', options: ['require', 'verify-full', 'disable'], default: 'require' }] },
  { id: 'mssql', name: 'Microsoft SQL Server', category: 'database', auth_types: ['basic', 'azure_ad_client'], settings_fields: [t('host', 'Host', { monospace: true, required: true }), t('database', 'Database', { monospace: true, required: true })] },
  { id: 'amazon_s3', name: 'Amazon S3', category: 'storage', auth_types: ['aws_sigv4'], settings_fields: [t('bucket', 'Bucket', { monospace: true, required: true }), t('region', 'Region', { monospace: true, default: 'me-central-1' })] },
  { id: 'azure_blob', name: 'Azure Blob Storage', category: 'storage', auth_types: ['azure_ad_client', 'api_key'], settings_fields: [t('account', 'Storage account', { required: true }), t('container', 'Container', { required: true })] },
  { id: 'sftp', name: 'SFTP', category: 'storage', auth_types: ['basic', 'mtls'], settings_fields: [t('host', 'Host', { monospace: true, required: true }), { key: 'port', label: 'Port', type: 'number', default: 22 }] },
  { id: 'qdrant', name: 'Qdrant', category: 'vector_store', auth_types: ['api_key', 'none'], settings_fields: [t('url', 'URL', { monospace: true, required: true, default: 'http://localhost:6333' })] },
  { id: 'pgvector', name: 'pgvector', category: 'vector_store', auth_types: ['basic'], settings_fields: [t('host', 'Host', { monospace: true, required: true }), t('database', 'Database', { monospace: true, required: true })] },
  { id: 'azure_ai_search', name: 'Azure AI Search', category: 'vector_store', auth_types: ['api_key', 'azure_ad_client'], settings_fields: [t('endpoint', 'Endpoint', { monospace: true, required: true })] },
  { id: 'redis', name: 'Redis', category: 'database', auth_types: ['basic', 'none'], settings_fields: [t('url', 'URL', { monospace: true, required: true })] },
  { id: 'http_api', name: 'HTTP API', category: 'api', auth_types: ['api_key', 'bearer', 'basic', 'oauth2_client_credentials', 'hmac', 'mtls', 'aws_sigv4', 'azure_ad_client', 'none'], settings_fields: [t('base_url', 'Base URL', { monospace: true, required: true })] },
  { id: 'mcp', name: 'MCP server', category: 'mcp', auth_types: ['bearer', 'api_key', 'oauth2_client_credentials', 'none'], settings_fields: [t('url', 'URL', { monospace: true })] },
  { id: 'document_worker', name: 'Document worker', category: 'worker', auth_types: ['api_key', 'bearer', 'mtls', 'none'], settings_fields: [t('base_url', 'Base URL', { monospace: true })] },
];
