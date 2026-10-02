// Never infer a final answer by finding JSON inside arbitrary prose.
const POLICY = 'assistant-final-v1';

function finalAnswer(response) {
  const messages = (response.raw?.output ?? []).filter(i => i.type === 'message' && i.role === 'assistant');
  const finals = messages.filter(i => i.phase === 'final_answer');
  const metadata = { policy: POLICY, assistantMessages: messages.length,
    finalMessages: finals.length, commentaryMessages: messages.filter(i => i.phase === 'commentary').length };
  // Preserve API failures, refusals and incomplete status for the native grader.
  if (response.error || messages.some(i => (i.content ?? []).some(p => p.type === 'refusal'))) {
    return { output: response.output, metadata: { ...metadata, selection: response.error ? 'provider_error' : 'refusal' } };
  }
  let selected;
  if (finals.length === 1) {
    selected = finals[0]; metadata.selection = 'explicit_final';
  } else if (finals.length === 0 && messages.length === 1 && !messages[0].phase) {
    selected = messages[0]; metadata.selection = 'single_unphased_message';
  } else {
    return { output: '', error: finals.length > 1 ? 'Multiple assistant final answers; selection is ambiguous'
      : 'No unambiguous assistant final answer', metadata: { ...metadata, selection: 'invalid' } };
  }
  const parts = (selected.content ?? []).filter(p => p.type === 'output_text' && typeof p.text === 'string');
  if (!parts.length) return { output: '', error: 'Assistant final answer has no output text',
    metadata: { ...metadata, selection: 'invalid' } };
  return { output: parts.map(p => p.text).join(''), metadata };
}

module.exports = { finalAnswer, POLICY };
