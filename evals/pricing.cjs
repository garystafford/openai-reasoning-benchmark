// OpenAI Standard text rates, verified 2026-09-28. USD per million tokens.
const source = 'https://developers.openai.com/api/docs/pricing';
const asOf = '2026-09-28';
const rates = {
  'gpt-6-astra': { input: 10, cached: 1, write: 12.5, output: 50 },
  'gpt-6-sol': { input: 2, cached: 0.2, write: 2.5, output: 10 },
  'gpt-6-luna': { input: 0.1, cached: 0.01, write: 0.125, output: 0.5 },
  'gpt-5.6-sol': { input: 4, cached: 0.4, write: 5, output: 20 },
  'gpt-5.6-terra': { input: 2, cached: 0.2, write: 2.5, output: 12 },
  'gpt-5.6-luna': { input: 0.2, cached: 0.02, write: 0.25, output: 1.2 },
};
function estimateCost(model, usage) {
  const rate = rates[model];
  if (!rate || !usage) return undefined;
  const input = usage.input_tokens;
  const output = usage.output_tokens;
  const cached = usage.input_tokens_details?.cached_tokens ?? 0;
  const write = usage.input_tokens_details?.cache_write_tokens ?? 0;
  if (![input, output, cached, write].every((n) => Number.isSafeInteger(n) && n >= 0) || cached + write > input) return undefined;
  const long = input > 272000;
  return ((input - cached - write) * rate.input + cached * rate.cached + write * rate.write) * (long ? 2 : 1) / 1e6
    + output * rate.output * (long ? 1.5 : 1) / 1e6;
}
module.exports = { estimateCost, rates, source, asOf };
