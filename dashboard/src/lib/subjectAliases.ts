// Subject alias loader — opt-in JSON file mapping subject_ref → display name.
//
// PLAN D16 + IA §1.2.D. The file lives at dashboard/public/subject-aliases.json
// (gitignored — it's user data, possibly PII). When absent or malformed, every
// caller gracefully falls back to the raw `res_NNN` ref. Operators populate
// this once for nicer UX; verifiers (who load external evidence packs) typically
// don't have it, so they keep seeing res_NNN — that's intentional and OK.
//
// Used by RunsListPage (E2) and SummaryCard (E4).

export type SubjectAliases = Record<string, string>;

export async function loadSubjectAliases(): Promise<SubjectAliases> {
  try {
    const res = await fetch("/subject-aliases.json", {
      headers: { Accept: "application/json" },
    });
    if (!res.ok) return {};
    const data = (await res.json()) as unknown;
    if (typeof data !== "object" || data === null || Array.isArray(data)) {
      return {};
    }
    // Filter out any non-string values defensively — bad JSON shouldn't crash.
    const out: SubjectAliases = {};
    for (const [k, v] of Object.entries(data as Record<string, unknown>)) {
      if (typeof v === "string") out[k] = v;
    }
    return out;
  } catch {
    return {};
  }
}

// Resolve a subject_ref to its display label.
//   - alias present → "Bob Martinez (res_002)"
//   - alias missing → "res_002"
//   - subject_ref null/empty → null  (caller decides empty-state copy)
export function formatSubject(
  subjectRef: string | null,
  aliases: SubjectAliases,
): { primary: string; secondary: string | null } | null {
  if (!subjectRef) return null;
  const alias = aliases[subjectRef];
  if (alias) return { primary: alias, secondary: subjectRef };
  return { primary: subjectRef, secondary: null };
}
