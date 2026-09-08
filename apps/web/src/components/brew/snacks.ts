/** Snack served per task type: freeform → jalebi, issue_fix → samosa, pr_review → pakora. */

export type SnackKind = "jalebi" | "samosa" | "pakora";

export function snackForType(type: string): SnackKind {
  if (type === "issue_fix") return "samosa";
  if (type === "pr_review") return "pakora";
  return "jalebi";
}
export const SEV_COLOR: Record<string, string> = {
  critical: "text-red-300",
  high: "text-syrup-300",
  medium: "text-chai-300",
  low: "text-ink-400",
};
