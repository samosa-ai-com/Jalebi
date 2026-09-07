/** Agent avatar registry: built-in SVG set + keyword auto-assignment.
 *
 * Every agent always resolves to a picture: an explicit `avatar` id wins when
 * it names a known avatar, otherwise the name+description are matched against
 * keyword patterns (first match wins), otherwise a deterministic hash of the
 * agent id picks one so the picture is stable across renders.
 */

export const AVATARS: { id: string; label: string }[] = [
  { id: "shield", label: "Shield" },
  { id: "magnifier", label: "Magnifier" },
  { id: "flask", label: "Flask" },
  { id: "wrench", label: "Wrench" },
  { id: "doc", label: "Document" },
  { id: "rocket", label: "Rocket" },
  { id: "compass", label: "Compass" },
  { id: "bug", label: "Bug" },
  { id: "bolt", label: "Bolt" },
  { id: "eye", label: "Eye" },
  { id: "link", label: "Link" },
  { id: "spark", label: "Spark" },
];

const AVATAR_IDS = new Set(AVATARS.map((a) => a.id));

const KEYWORDS: [RegExp, string][] = [
  [/secur|vuln|threat|audit|owasp|exploit|hardening/i, "shield"],
  [/review|inspect|approve/i, "magnifier"],
  [/test|qa\b|coverage|spec\b/i, "flask"],
  [/refactor|fix|debt|clean|migrat/i, "wrench"],
  [/doc|readme|writ|blog|changelog/i, "doc"],
  [/release|ship|deploy|launch|publish/i, "rocket"],
  [/plan|roadmap|architect/i, "compass"],
  [/debug|bug|triage|trace|repro/i, "bug"],
  [/perf|speed|optim|latency|bundle/i, "bolt"],
  [/access|a11y|ux\b|ui\b|design|css|style/i, "eye"],
  [/api\b|contract|integrat|endpoint|sdk/i, "link"],
];

function hashPick(seed: string): string {
  let h = 0;
  for (let i = 0; i < seed.length; i++) h = (h * 31 + seed.charCodeAt(i)) >>> 0;
  return AVATARS[h % AVATARS.length].id;
}

/** Keyword suggestion from name+description (no id, no explicit avatar). */
export function suggestAvatar(name: string, description: string): string {
  const hay = `${name} ${description}`;
  for (const [re, id] of KEYWORDS) {
    if (re.test(hay)) return id;
  }
  return hashPick(name || "agent");
}

/** Effective avatar id for an agent: explicit pick, else suggestion. */
export function avatarFor(agent: {
  id: string;
  name: string;
  description?: string | null;
  avatar?: string | null;
}): string {
  if (agent.avatar && AVATAR_IDS.has(agent.avatar)) return agent.avatar;
  return suggestAvatar(agent.name, agent.description ?? "");
}

export function avatarUrl(id: string): string {
  return `/avatars/${AVATAR_IDS.has(id) ? id : "spark"}.svg`;
}
