/** Snack served per task type: freeform → jalebi, issue_fix → samosa, pr_review → chakli. */

export type SnackKind = "jalebi" | "samosa" | "chakli";

export function snackForType(type: string): SnackKind {
  if (type === "issue_fix") return "samosa";
  if (type === "pr_review") return "chakli";
  return "jalebi";
}
