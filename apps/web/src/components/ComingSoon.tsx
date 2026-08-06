export function ComingSoon({ feature }: { feature: string }) {
  return (
    <div className="surface flex flex-col items-center justify-center gap-3 px-6 py-24 text-center animate-fade-up">
      <div className="jalebi-spin text-3xl" aria-hidden="true">
        🌀
      </div>
      <p className="eyebrow">{feature}</p>
      <p className="max-w-sm text-sm text-ink-400">
        Coming in a later phase of Jalebi. The engine behind this screen isn&apos;t built yet — this
        is just a placeholder so the navigation is complete.
      </p>
    </div>
  );
}
