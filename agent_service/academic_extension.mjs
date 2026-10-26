// Explicit image-owned Pi extension. No shell or arbitrary remote URL tool.
import { Type } from '@sinclair/typebox';

export default function (pi) {
  const call = async (name, payload, signal) => {
    const origin = process.env.AGENT_TOOL_PROXY_BASE_URL;
    const token = process.env.AGENT_MODEL_API_KEY;
    if (!origin || !token) throw new Error('academic_bridge_unavailable');
    const response = await fetch(origin + '/tools/academic', {
      method: 'POST', redirect: 'error', signal,
      headers: {'Authorization': 'Bearer ' + token, 'Content-Type': 'application/json'},
      body: JSON.stringify({tool: name, payload}),
    });
    if (!response.ok) throw new Error('academic_bridge_rejected');
    const raw = await response.text();
    if (raw.length > 2 * 1024 * 1024) throw new Error('academic_bridge_output_limit');
    return {content: [{type: 'text', text: raw}], details: {source: 'task-academic-bridge'}};
  };
  pi.registerTool({name: 'academic_search', label: 'Academic search',
    description: 'Search the task-authorized academic providers. Keep the real returned identifiers, request failures and counts.',
    parameters: Type.Object({query: Type.String({minLength: 1, maxLength: 2000}),
      providers: Type.Optional(Type.Array(Type.String(), {maxItems: 5})),
      limit: Type.Optional(Type.Integer({minimum: 1, maximum: 50}))}),
    execute: async (_id, params, signal) => call('academic_search', params, signal)});
  pi.registerTool({name: 'academic_verify', label: 'Verify academic identifier',
    description: 'Verify a DOI or PubMed identifier against a fixed public scholarly endpoint.',
    parameters: Type.Object({kind: Type.Union([Type.Literal('doi'), Type.Literal('pmid')]),
      identifier: Type.String({minLength: 1, maxLength: 300})}),
    execute: async (_id, params, signal) => call('academic_verify', params, signal)});
}
