// The Claude API calls the desk makes on Cloudflare: custom messages, template suggestions (drafts.js, profile.js)
// and the lease photo scan (leaseScan.js). The key is the Worker secret ANTHROPIC_API_KEY.
import Anthropic from "@anthropic-ai/sdk";

export class ClaudeError extends Error {}

const BETAS = ["server-side-fallback-2026-07-01"];

export function client(settings) {
  const key = settings.anthropic_api_key;
  if (!key) throw new ClaudeError("No Claude API key on the desk yet. Nesi adds it once with: npx wrangler secret put ANTHROPIC_API_KEY");
  return new Anthropic({ apiKey: key });
}

export function explain(e, what = "The Claude API call failed") {
  if (e instanceof ClaudeError) return e;
  if (e instanceof Anthropic.AuthenticationError) return new ClaudeError("The API key was refused. Nesi sets a new one with: npx wrangler secret put ANTHROPIC_API_KEY");
  if (e instanceof Anthropic.RateLimitError) return new ClaudeError("Too many requests right now. Wait a minute and try again.");
  if (e instanceof Anthropic.APIError) return new ClaudeError(`${what}: ${e.message}`);
  return new ClaudeError(`${what}: ${e?.message || e}`);
}

/** One message: {text, model, stop_reason}. The system prompt is cached (it is the same for every ad). */
export async function write(settings, { model, effort, system, user }, api = null) {
  const c = api || client(settings);
  let msg;
  try {
    msg = await c.beta.messages.create({
      model: model || "claude-opus-5-5",
      max_tokens: 16000,
      output_config: { effort: effort || "low" },
      // If the model declines (a safety classifier false positive), the API retries on its default fallback.
      betas: BETAS,
      fallbacks: "default",
      system: [{ type: "text", text: system, cache_control: { type: "ephemeral" } }],
      messages: [{ role: "user", content: user }],
    });
  } catch (e) {
    throw explain(e, "The Claude API call failed");
  }
  const text = msg.content.filter((b) => b.type === "text").map((b) => b.text).join("");
  return { text, model: msg.model || model, stop_reason: msg.stop_reason };
}

/** Stream a reply: calls onText(chunk) as text arrives, returns the final message's stop_reason. */
export async function stream(settings, { model, effort, system, content }, onText, api = null) {
  const c = api || client(settings);
  try {
    const s = c.beta.messages.stream({
      model: model || "claude-opus-5-5",
      max_tokens: 16000,
      output_config: { effort: effort || "low" },
      betas: BETAS,
      fallbacks: "default",
      system,
      messages: [{ role: "user", content }],
    });
    for await (const event of s) {
      if (event.type === "content_block_delta" && event.delta.type === "text_delta") await onText(event.delta.text);
    }
    const final = await s.finalMessage();
    return final.stop_reason;
  } catch (e) {
    throw explain(e, "The API call failed");
  }
}
