/** Operational proof and execution context are separate from the semantic verdict. */
export function operationalVerification(value: unknown) {
  const record = (v: unknown): Record<string, unknown> => v && typeof v === 'object' && !Array.isArray(v) ? v as Record<string, unknown> : {};
  const data = record(value), checks = record(data.checks);
  const keys = ['identity_verified', 'address_committed', 'stored_address_matches_request', 'update_read_back', 'invoice_effect_explained'];
  const available = data.schema === 'cae-billing-simulation-v1' && data.context === 'simulation';
  const verified = available && data.status === 'verified' && keys.every(key => checks[key] === true)
    && typeof data.receipt_id === 'string' && data.receipt_id.length > 0
    && typeof data.tool_calls === 'number' && Number.isFinite(data.tool_calls) && data.tool_calls >= 4;
  return { available, verified, checks, keys, context: 'Simulation',
    receipt: typeof data.receipt_id === 'string' ? data.receipt_id : '',
    toolCalls: typeof data.tool_calls === 'number' ? data.tool_calls : 0 };
}
