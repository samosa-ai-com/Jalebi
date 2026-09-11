# Accessibility in Jalebi

Jalebi is built to provide an accessible, usable experience for all developers, including those who rely on screen readers, keyboard-only navigation, or reduced motion settings.

---

## 1. What Is Supported

- **WCAG 2.1 AA Contrast:** All text content complies with or exceeds the 4.5:1 normal text contrast ratio against dark background surfaces (`--color-ink-500: #97836f` against `ink-900: #1a1410` provides 5.03:1).
- **Keyboard Navigation & Focus Management:**
  - A bypass skip link (`#main-content`) allows jumping past header navigation directly into the page content.
  - All interactive elements are reachable via `Tab`/`Shift+Tab` with clear focus indicator rings.
  - Modal dialogs (`PublishDialog`, artifact preview, rerun modal) use `useFocusTrap` to trap keyboard focus within the dialog, autofocus initial interactive targets, and return focus to the opener element upon dismissal via `Escape` or cancel.
- **Screen Reader Announcements:**
  - Semantic HTML structure with proper landmark regions (`header`, `nav`, `main`).
  - Real-time task state transitions (`done`, `failed`, `timed out`, `interrupted`, and `needs_you`) are politely announced via dedicated `sr-only` live regions (`role="status" aria-live="polite"`) managed by `useStatusAnnouncer` on both the Tasks queue and Task Detail views.
  - Steady-state polling noise is omitted; only meaningful transitions trigger announcements (capped at 3 per update).
- **Global Reduced Motion:**
  - A universal `prefers-reduced-motion: reduce` media rule in `index.css` neutralizes all CSS animation and transition durations across the application (0.01ms duration, 1 iteration, `scroll-behavior: auto !important`).
- **Pointer Targets:** Interactive buttons, pills, toggles, and dismiss controls enforce minimum 24px target dimensions (`min-h-6 min-w-6` or equivalent).
- **Automated Linting:** `eslint-plugin-jsx-a11y` recommended rules are enforced across all web app code.

---

## 2. What Is Intentionally Limited

- **Dark-mode Palette Only:** Jalebi is styled strictly as a dark-theme application. System-level high-contrast and color-inversion modes are respected, but an in-app light mode toggle is not provided.
- **Streaming Terminal & Diff Logs:** Agent execution streams high-frequency terminal messages and tool calls. To prevent overwhelming assistive technology with continuous audio floods, raw stream bursts are not mirrored live into ARIA live regions; status transitions are announced instead, with searchable and copyable text panes provided.
- **Complex Graphical Visualizations:** Specialized visual representations (such as the Brew House SVG canvas) provide reduced motion handling and textual status equivalents rather than exhaustive ARIA tree representation for every animated SVG node.

---

## 3. How Contributors Should Keep It That Way

1. **No `text-ink-600` for Text:** Never use `text-ink-600` or `placeholder:text-ink-600` for readable text or placeholders; always use `text-ink-500` or brighter tokens (`text-ink-400` through `text-ink-100`). Border, background, and ring tokens (`border-ink-*`, `bg-ink-*`, `ring-ink-*`) are unaffected.
2. **Keep ESLint A11y Clean:** Run `npm run lint` and ensure `eslint-plugin-jsx-a11y` recommended rules pass with zero warnings or errors. Do not suppress rules without clear justification.
3. **Use `useFocusTrap` on New Modals:** Any new dialog or overlay must trap focus, handle `Escape`, and return focus to its triggering control upon closing.
4. **Lifecycle Announcements:** When presenting asynchronous task states, rely on `useStatusAnnouncer` to dispatch polite, transition-only updates rather than recurring polling alerts.
5. **Honor Reduced Motion:** Do not introduce inline animation or transition durations that bypass the global universal `prefers-reduced-motion` media rule.
6. **Manual QA:** Validate new UI features against the Accessibility Manual QA Checks in [`docs/12-ui-validation.md`](docs/12-ui-validation.md).
