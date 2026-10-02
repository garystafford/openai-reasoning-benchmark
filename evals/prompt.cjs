// JSON.stringify protects newlines, quotes, and braces in the original prompts.
module.exports = ({ vars }) => JSON.stringify([
  { role: 'system', content: vars.system },
  { role: 'user', content: vars.question },
]);
