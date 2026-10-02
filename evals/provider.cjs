// Compatibility adapter for Promptfoo 0.123.1, whose catalog predates GPT-6 Sol/Luna.
// Promptfoo's native Responses provider still owns authentication, HTTP, retries,
// cancellation, raw responses, and token accounting. No separate request loop.
const { estimateCost, source, asOf, rates } = require('./pricing.cjs');
const { models } = require('./models.json');
const { schemaHash } = require('./output-schema.cjs');
const { finalAnswer } = require('./final-answer.cjs');

class ReasoningProvider {
  constructor(options) {
    this.config = options.config;
    this.label = options.label;
    const { model, reasoning } = this.config;
    if (!models[model]?.efforts.includes(reasoning?.effort)) throw new Error('Unsupported model/reasoning pair');
    this.model = model;
  }
  id() { return `openai:responses:${this.model}:${this.config.reasoning.effort}`; }
  async callApi(prompt, context, callApiOptions) {
    const format = context?.test?.metadata?.output_format;
    if (format?.type !== 'json_schema' || format.strict !== true || !format.schema) {
      throw new Error('Missing strict Structured Outputs schema for benchmark case');
    }
    const fingerprint = schemaHash(format);
    if (fingerprint !== context.test.metadata.output_schema_sha256) throw new Error('Output schema fingerprint mismatch');
    this.delegates ??= new Map();
    if (!this.delegates.has(fingerprint)) {
      const { model, ...config } = this.config;
      const promptfoo = require('promptfoo');
      // The CLI's ESM cache and this CJS library's cache are separate instances.
      // --no-cache alone does not disable the delegate's response cache.
      promptfoo.cache.disableCache();
      this.delegates.set(fingerprint, promptfoo.loadApiProvider(`openai:responses:${model}`, {
        options: { config: {
          ...config,
          passthrough: {
            ...config.passthrough,
            reasoning: config.reasoning,
            text: { format, ...(config.verbosity ? { verbosity: config.verbosity } : {}) },
          },
        } },
      }));
    }
    const response = await (await this.delegates.get(fingerprint)).callApi(prompt, context, callApiOptions);
    const extracted = finalAnswer(response);
    const cost = response.cached ? 0 : estimateCost(this.model, response.raw?.usage);
    // Missing usage stays unknown; it is never reported as a free API request.
    return {
      ...response,
      output: extracted.output,
      ...(extracted.error ? { error: extracted.error } : {}),
      cost,
      metadata: {
        ...response.metadata,
        outputMode: 'structured_outputs', outputSchemaSha256: fingerprint,
        outputExtraction: extracted.metadata,
        pricing: { source, asOf, currency: 'USD', serviceTier: 'default', perMillionTokens: rates[this.model] },
      },
    };
  }
}
module.exports = ReasoningProvider;
