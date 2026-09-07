# ruff: noqa: E501 -- seed bodies are prose markdown with long lines.
"""Preloaded skill library content (versioned seeds).

Jalebi-authored starter knowledge: generalized, reusable markdown that agents
link by reference. Inserted by seed_catalog.seed_catalog (insert-missing only,
never overwrites owner content).
"""

SEED_SKILLS: list[dict[str, object]] = [
    {
        'id': 'secure-coding',
        'name': 'Secure Coding',
        'description': 'Security audit and hardening checklist for any codebase.',
        'tags': ['security', 'review'],
        'content': """# Secure Coding

A repeatable security audit and hardening checklist you can run against any codebase, regardless of language or stack.

## When to use

Use this whenever you touch code that handles untrusted input, secrets, or destructive operations: writing new endpoints, reviewing a change, or preparing a change for merge. It also works as a standalone review pass over an unfamiliar codebase.

## Procedure

- Model the threat first. List every trust boundary: network to app, app to database, app to filesystem, app to external services. Name who is untrusted on each side and what they could gain.
- Inventory inputs. For every entry point (forms, APIs, file uploads, CLI args, env vars, webhook payloads) record its source, shape, and what consumes it.
- Validate input at the boundary. Apply allow-lists over deny-lists: check types, lengths, ranges, and allowed character sets before the value touches any logic.
- Audit injection sinks. Search for string concatenation feeding SQL, shell commands, ORMs, templates, or HTML. Replace with parameterized queries, prepared statements, or an escaping layer that matches the sink's context (HTML, attribute, URL, JS, shell).
- Review secrets hygiene. Confirm no credentials, tokens, or keys are committed, logged, or embedded in client code. Force secrets through a vault or environment, and confirm they never appear in error messages.
- Restrict dangerous boundaries. Audit subprocess calls, deserialization routines, and dynamic code evaluation; use fixed argument vectors instead of shell string interpolation, and confirm the full dependency risk picture in supply-chain-audit.
- Guard destructive paths. Verify any operation that deletes, overwrites, publishes, or pushes requires explicit confirmation, checks the target, and can be reversed or recovered.
- Verify after hardening. Re-run the happy path plus the attack cases: malformed input, oversized payloads, double-submission, and unauthenticated access attempts.
- Check authorization on every action. Confirm each endpoint or operation verifies the caller is allowed to perform it, not just that they are authenticated, and that the check happens at the resource level.
- Enforce least privilege. Scope any token, service account, or process to the minimum permissions it needs, and review whether that scope is ever broader than necessary.
- Harden the error path. Make sure failures return generic messages to callers while logging the specific detail server-side, so debugging is possible without leaking internals.

## Verification

- Re-run the attack cases after hardening and confirm they fail safely.
- Confirm no secret appears in logs, history, or client bundles.
- Verify authorization at the resource level on every endpoint, not just authentication.

## Pitfalls

- Auditing sinks before defining boundaries, so the threat model is vague and mitigations miss the real attack surface.
- Only testing happy paths, leaving malformed and adversarial input unexercised.
- Escaping for the wrong context (escaping for HTML then inserting into an attribute).
- Adding validation everywhere except at the actual trust boundary.
- Logging a secret "temporarily" to debug, then forgetting to remove it.
- Relying on client-side validation alone without enforcing identical checks at the server trust boundary.
""",
    },
    {
        'id': 'threat-modeling',
        'name': 'Threat Modeling',
        'description': 'STRIDE-style trust-boundary analysis for any codebase.',
        'tags': ['security', 'planning'],
        'content': """# Threat Modeling

A structured way to reason about security risk before you build: identify boundaries, assets, and threats, then choose mitigations and record residual risk.

## When to use

Use this at design time for any feature that crosses a trust boundary, handles sensitive data, or performs privileged actions. Do it early, when changing the design is cheap, and revisit it whenever the architecture changes.

## Procedure

- Draw the boundaries. Sketch the system as components connected by data flows, then mark every trust boundary where data moves from a less-trusted to a more-trusted zone (network to app, app to DB, plugin to host, user to admin).
- List assets. Name what an attacker would want: data (PII, tokens, source), integrity (config, state), or availability (compute, storage). Rank by sensitivity.
- Enumerate threats with STRIDE per component: Spoofing, Tampering, Repudiation, Information disclosure, Denial of service, Elevation of privilege.
- Trace abuse paths. For each threat, write the concrete sequence of steps an attacker takes to exploit it, and note which trust boundary it crosses.
- Choose mitigations. Map each abuse path to a control: authentication, authorization checks, validation, rate limiting, auditing, encryption at rest or in transit. Pick the smallest control that closes the path.
- Assess residual risk. For every mitigated threat, note what remains if the control fails or is bypassed, and decide whether that residual risk is acceptable or needs a control redesign.
- Record and review. Write the model down so a reviewer can check the reasoning, and re-run it when boundaries or components change.
- Prioritize by impact. Rank threats by likelihood and blast radius so you address the highest-risk paths first and communicate what you are deliberately deferring.
- Validate with the team. Walk the model past a second reviewer to catch boundaries or assets you missed, and treat disagreement as a signal to tighten the model.
- Reassess after changes. Re-run the model whenever you add an input path, a new data store, or a privilege, since each of these can open a boundary you have not reconsidered.

## Verification

- Every trust boundary has named threats with mitigations or accepted-risk owners.
- Residual risks are written down with an owner and a review trigger.
- Revisit the model whenever the architecture or data flows change.

## Pitfalls

- Modeling only external attackers and ignoring insider, plugin, or supply-chain trust.
- Listing assets but never tying them to concrete abuse paths.
- Treating STRIDE as a checkbox and skipping the boundary drawing that makes it meaningful.
- Assuming encryption or validation "handles it" without verifying the control actually intercepts the attack path.
- Ending at "mitigated" without recording residual risk or a follow-up owner.
- Failing to update the model when new components or data flows appear.
""",
    },
    {
        'id': 'code-review',
        'name': 'Code Review',
        'description': 'Structured diff review with severity-ranked verdicts.',
        'tags': ['review'],
        'content': """# Code Review

A structured method for reviewing a diff: scope it first, apply parallel lenses, rank findings by severity, and deliver a verdict the author can act on.

## When to use

Use this whenever you review a pull request, a patch, or a proposed change, whether you are the assigned reviewer, the author self-reviewing, or a maintainer doing final gate.

## Procedure

- Scope the diff. Read the description and linked issue to learn the intent, then list the changed files and how the change connects to that intent.
- Read for correctness first. Trace the control flow of the change end to end and check edge cases: empty inputs, nulls, boundary conditions, and failure paths.
- Apply parallel lenses, one pass each: security (untrusted input, secrets, injection), performance (hot paths, unbounded loops, N+1 queries, allocations), testability (are the behaviors covered, not just lines), and clarity (naming, duplication, dead code).
- Rank findings by severity. Use a fixed scale such as critical (blocks merge), major (should fix), minor (nice to fix), and nit (style). Tie each finding to a specific line or behavior, never a vague impression.
- Give a merge verdict. State clearly: approve, request changes with the blocking items listed, or approve with non-blocking nits. Separate blocking from optional so the author knows what to do.
- Respond to comments. Address every blocking finding with a concrete change or a reason why it is acceptable; acknowledge nits that you accept and push back once on ones you will not take. Do not silently ignore comments.
- Check for dead and duplicated code. Remove unused branches and copy-pasted logic the change leaves behind, and note where a comment restates the code instead of explaining the why.
- Confirm tests cover the change. Ensure new behavior has a test that fails without it and passes with it, and that the diff does not weaken existing assertions.
- Read with fresh eyes. If you wrote the code, step back and review it as an unfamiliar reader; if you are reviewing someone else's work, trace the logic yourself instead of trusting the summary.
- Track the findings. Keep a running list of severity-ranked issues and confirm each one is resolved or explicitly waived before you finalize the verdict.

## Verification

- The verdict is recorded with severity-ranked findings an author can act on without asking.
- Security findings get a second pair of eyes before merge.
- Every finding points at a line, a reason, and a suggested fix.

## Pitfalls

- Reviewing the whole file instead of the change, producing noise that buries the important findings.
- Skimming only for style and missing correctness and security problems.
- Reporting every issue at the same severity so nothing is prioritized.
- Saying "this won't work" without pointing at the line or giving an alternative.
- Approving without reading, or blocking over a nit.
- The author ignoring comments instead of replying with a change or a reasoned disagreement.
""",
    },
    {
        'id': 'systematic-debugging',
        'name': 'Systematic Debugging',
        'description': 'Hypothesis-driven root-cause method: reproduce, isolate, verify.',
        'tags': ['debugging'],
        'content': """# Systematic Debugging

A hypothesis-driven method for finding root causes: reproduce the bug first, isolate the failing component, then confirm the fix with evidence.

## When to use

Use this whenever behavior does not match expectations and the cause is not obvious. It applies to failing tests, crashes, wrong output, performance regressions, and race conditions, regardless of language.

## Procedure

- Reproduce it first. Get a minimal, reliable reproducer: the exact input, command, and environment that triggers the bug. If you cannot reproduce it, state that and keep gathering conditions before guessing.
- Read the trace. Start from the first unexpected value or error and work outward. Read stack traces, logs, and the failing assertion to locate where reality diverges from expectation.
- Form hypotheses. Write one or more candidate explanations of the root cause, each phrased as a falsifiable claim with a predicted observable effect.
- Isolate with bisection. Use binary search over commits, inputs, or call sites to shrink the suspect region. Reduce the reproducer until a single component or line stands out.
- Experiment, don't shotgun. For one hypothesis at a time, make the smallest change that tests it, observe the predicted effect, and record whether the evidence supports or refutes it. Do not stack several changes at once.
- Verify the root cause. Before fixing, confirm you can explain why the original input triggered the failure and why your change prevents it.
- Fix and regression-test. Apply the fix, re-run the reproducer, and add a test that would have caught the bug so it cannot silently return.
- Watch for environmental causes. Before assuming code is at fault, rule out configuration, stale state, time and timezone dependence, and differences between environments.
- Write down what you learn. Record the root cause and the fix in the issue or a note so the next person does not re-derive it from scratch.
- Know when to stop. If a hypothesis keeps failing to predict, discard it and return to the reproducer and trace rather than forcing a fit.

## Verification

- The reproducer fails before the fix and passes after it.
- Reverting the fix brings the failure back (causality, not coincidence).
- The diff contains only the fix — no drive-by changes.

## Pitfalls

- Changing code before you can reproduce the bug, then being unable to tell if anything helped.
- Making several changes at once and losing track of which one mattered.
- Fixing the symptom (the wrong output) instead of the cause (the bad input or state).
- Trusting that the fix worked because the reproducer stopped failing, without knowing why.
- Shotgun edits: trying random plausible changes until something sticks, with no hypothesis to confirm.
- Skipping the regression test, so the same bug reappears next refactor.
""",
    },
    {
        'id': 'verification-before-completion',
        'name': 'Verification Before Completion',
        'description': 'Evidence-over-claims closing gate for any change.',
        'tags': ['quality'],
        'content': """# Verification Before Completion

A closing gate for any change: never claim a task is done from edits alone. Run it, observe the fix, and check for regressions before reporting completion.

## When to use

Use this at the end of any task that changes behavior: a bug fix, a feature, a refactor, or a config change. It is the difference between "I changed the code" and "I confirmed the change works."

## Procedure

- Define the observable done-criteria up front. Before you finish, write down what the user would see or measure that proves the change worked, stated as behavior, not implementation.
- Run it. Execute the actual thing that changed: the app, the command, the test, or the script. Running the code is the only evidence that counts.
- Observe the fix. Confirm the specific behavior you intended is present, using the same reproduction path that failed before, and record what you actually saw.
- Check the surrounding system. Verify you did not regress adjacent behavior: existing tests, the build, lint, and the paths that touch the code you changed.
- Verify from a clean state. If feasible, restart the process or reset state so you are not fooled by stale caches or an already-warmed environment.
- Report with evidence. When you claim completion, point to the exact command run and the observed output, not to "I believe it works" or "the code compiles."
- If it does not verify, go back. Treat a failed check as a finding: revise, re-run, and only mark done when the evidence passes.
- Write the verification down. Keep the exact commands and expected outputs alongside the change so a reviewer or a future session can repeat the proof.
- Verify in the environment that matters. Confirm behavior in the context where it will actually run, not only in a mocked or idealized setup that cannot reproduce the failure.
- Treat unverified work as not done. Keep the task open and flag it as in-progress until the evidence exists, rather than moving on and assuming it worked.

## Verification

- You observed the fixed behavior directly (test output, UI, logs) — not inferred from edits.
- The regression suite is green with the change applied.
- The diff contains only the intended change.

## Pitfalls

- Marking a task complete because the code compiles or the edit looks correct.
- Trusting the previous failure mode without re-running it to prove it is gone.
- Verifying only the happy path and missing that the change broke a neighboring feature.
- Claiming done from memory of an earlier run rather than a fresh execution.
- Ignoring test or build failures because "the fix is obviously right."
- Reporting success without the specific command and output a reviewer could repeat.
""",
    },
    {
        'id': 'test-strategy',
        'name': 'Test Strategy',
        'description': 'Test-first discipline plus pragmatic testing method.',
        'tags': ['testing'],
        'content': """# Test Strategy

A test-first discipline combined with a pragmatic testing method you can apply to any project: write the failing test, make it pass, then refactor, asserting behavior rather than implementation.

## When to use

Use this when adding a feature or fixing a bug, or when the project has a gap in coverage. It gives you both the red/green/refactor rhythm and the judgment to test the right things at the right level.

## Procedure

- Discover the repo-native commands first. Find how this project runs tests (package manager, test runner, framework) and the conventions for where tests live, so your additions match.
- Write the test before the code (red). Express the desired behavior as an assertion that fails now, choosing a meaningful example: a real input and the output you expect.
- Make it pass (green). Write the smallest implementation that satisfies the test, then re-run to confirm it passes.
- Refactor safely. Improve the code while keeping the tests green, moving through small steps so any breakage is immediately visible.
- Assert behavior, not implementation. Test observable outcomes through public interfaces. Avoid asserting on internal calls, mocks of private methods, or incidental implementation details that make tests brittle.
- Match the test to the level. Use fast unit tests for logic, integration tests for boundaries and services, and a few end-to-end tests for critical user paths. Keep the pyramid in mind; do not put everything at one level.
- Investigate flakes deliberately. A flaky test is a signal of a real problem: shared mutable state, time or randomness dependence, or order dependence. Fix the cause, not the symptom of retrying.
- Add property-based tests where they earn their keep. For functions with complex or combinatorial inputs, use property-based testing to check invariants across generated inputs rather than hand-writing every case.
- Keep the suite fast and deterministic. Prefer isolated tests over shared mutable state, and favor the fastest test level that still covers the behavior, so the suite runs often enough to be useful.
- Test the boundaries and errors. Give error handling and edge inputs first-class coverage, since those are where regressions and defects most often hide.

## Verification

- New tests fail without the fix and pass with it.
- The suite passes three consecutive runs with no retries.
- No test depends on execution order, wall-clock timing, or external state.

## Pitfalls

- Writing tests that only cover the code you already wrote, so they never catch a wrong behavior.
- Asserting on implementation details, then breaking the test on every refactor.
- Mocking too much, so the test verifies your mock instead of the real behavior.
- Skipping the red phase and never confirming the test can fail.
- Ignoring a flaky test instead of finding its root cause.
- Testing only the happy path and leaving error and boundary cases uncovered.
""",
    },
    {
        'id': 'git-workflow',
        'name': 'Git Workflow',
        'description': 'Commit discipline, branches, PR sizing, versioning.',
        'tags': ['git'],
        'content': """# Git Workflow

Discipline for how you commit, branch, size pull requests, and version, so history stays readable, reviewable, and reversible.

## When to use

Use this whenever you create a commit, branch, pull request, or release. It applies to solo work and teams alike: good history is what makes future debugging and archaeology possible.

## Procedure

- Make atomic commits. Each commit should represent one logical change that stands alone: it builds, passes tests, and has a message explaining what and why. Separate refactors from behavior changes.
- Write clear messages. Use a short imperative summary line ("Add retry logic for the upload endpoint") followed by a body covering the why and any trade-offs. Match the style the repo already uses.
- Name branches by intent. Use a scheme such as `type/scope-short-desc` (for example `fix/auth-timeout` or `feat/export-csv`), and create one branch per piece of work.
- Use a worktree per task. When a change requires a clean checkout alongside other work, use a separate worktree rather than stashing or stacking unfinished changes on one working copy.
- Keep PRs small. Size a pull request so a reviewer can understand it in one sitting: one coherent change, a focused diff, and a description that states the intent and the verification you ran.
- Choose a rebase or merge policy and apply it consistently. Decide whether you keep history linear (rebase) or preserve merge topology, and document the choice so collaborators are not surprised.
- Version deliberately. Apply semantic versioning: bump the patch for backward-compatible fixes, minor for backward-compatible additions, major for breaking changes. Tag releases and keep a changelog that matches the versioned behavior.
- Know how to recover. Before you push, confirm how to undo the branch and how to restore a released version, so mistakes are cheap to reverse.
- Review your own diff before sharing. Stage the change, read it back as a diff, and fix the obvious issues before it reaches a reviewer.
- Keep the working tree clean between tasks. Resolve or stash unfinished work before switching branches so you never accidentally commit unrelated changes.

## Verification

- The log reads as reviewable atomic steps with conventional messages.
- CI is green on the branch from a clean checkout.
- The PR links its issue and shows its verification.

## Pitfalls

- Making one giant commit or a series of "wip" commits that cannot be reverted independently.
- Pushing a large, unfocused PR that reviewers cannot review meaningfully.
- Mixing a refactor and a behavior change in the same commit, hiding one behind the other.
- Using merge commits in a linear-history project, or rebasing in a topology-preserving one, inconsistently.
- Forgetting to update the changelog when a behavior changes.
- Never tagging releases, so version numbers carry no meaning about what changed.
""",
    },
    {
        'id': 'api-design',
        'name': 'API Design',
        'description': 'Contract design: resources, errors, versioning, idempotency.',
        'tags': ['design'],
        'content': """# API Design

A contract-first method for designing APIs that are predictable, easy to consume, and safe to evolve: naming, status codes, pagination, errors, idempotency, and versioning.

## When to use

Use this when you design or extend a network API, whether REST, an internal service interface, or a client SDK. Make the contract decisions up front, before consumers depend on them.

## Procedure

- Model resources as nouns. Design around the entities you expose and their relationships, with consistent naming (plural nouns for collections, singular for items) and stable identifiers.
- Use HTTP methods and status codes for their meaning. Map actions to verbs (GET for read, POST for create, PUT for replace, PATCH for partial, DELETE for removal) and choose status codes that communicate the outcome (2xx success, 4xx client error, 5xx server error), with the 2xx you return matched to what actually happened.
- Design errors as envelopes. Return structured errors with a stable machine-readable code, a human message, and optional details or a correlation id, not just a bare message string.
- Paginate every collection. Return a consistent page shape with a way to fetch the next page (cursor or offset), and cap page size. Never return unbounded lists.
- Make mutating operations idempotent where it matters. Accept an idempotency key from the client on create and other retry-prone operations so a duplicate request cannot create duplicate side effects.
- Define semantics precisely. Document required versus optional fields, null handling, ordering guarantees, time formats, and the units and precision of numeric fields.
- Evolve backward-compatibly. Add fields rather than removing or renaming them, treat unknown fields as ignorable, and only introduce breaking changes behind a new version. Version the API explicitly and keep the old version reachable while consumers migrate.
- Document the contract. Keep a machine-readable or written schema in sync with the implementation so consumers have a single source of truth for fields, types, and semantics.
- Handle time and identity consistently. Use a single time format with an explicit timezone, and make identifiers opaque and stable rather than exposing internal counters.
- Set sensible defaults and limits. Choose safe defaults for optional fields and enforce rate limits and payload caps so a misbehaving client cannot degrade the service.

## Verification

- The contract is exercised with real calls across happy paths and error paths.
- Breaking changes are listed explicitly with migration notes.
- Versioning, pagination, and error envelopes behave as documented.

## Pitfalls

- Returning the same status code for every failure, so clients cannot distinguish their mistakes from server problems.
- Returning unbounded lists with no pagination, which breaks under real data volumes.
- Using plain text error messages that clients cannot parse or match against.
- Renaming or removing a field in a minor release and silently breaking consumers.
- Failing to make create idempotent, so retries create duplicate resources.
- Leaving null, ordering, or timezone semantics unspecified and letting every client guess.
""",
    },
    {
        'id': 'planning',
        'name': 'Planning',
        'description': 'Plan-first breakdown into small verifiable increments.',
        'tags': ['planning'],
        'content': """# Planning

A plan-first method for turning a request into small, verifiable increments: get an approved spec, break the work into short tasks with done-criteria and exact paths, and brainstorm questions before writing code.

## When to use

Use this at the start of any feature, fix, or refactor, and again whenever scope expands mid-task. It keeps work reviewable, testable, and safe to stop at any point.

## Procedure

- Get an approved spec first. Restate the request as a concrete spec of what the result must do, and do not start writing code until the stakeholder approves that spec.
- Brainstorm questions before code. Surface ambiguities, edge cases, and decisions that need input, and resolve them in the plan rather than discovering them mid-implementation.
- Break work into small increments. Decompose the feature into tasks sized to be done in a few minutes each, each independently verifiable.
- Give every task done-criteria. For each increment write the observable behavior that proves it is complete, stated as evidence a reviewer could check, not as implementation steps.
- Name exact file paths. For each task list the precise files to create or modify, so the work has a concrete footprint and reviewers know what to expect.
- Include verification steps. For each task state the exact command or check you will run to verify it (test, build, manual reproduction) before marking it done.
- Sequence dependencies. Order increments so each builds on a stable, verified base, and keep each small enough that a failure is cheap to find and fix.
- Re-plan when scope changes. If the work grows beyond the approved plan, stop, revise the plan, and get approval again rather than quietly expanding.
- Keep the plan visible. Store the plan where the team and the next session can find it, and update it as the work moves so it stays the record of intent.
- Resolve risks early. Name the unknowns and risky assumptions in the plan and address them first, so a late surprise does not invalidate the increments built on top.

## Verification

- Every task names exact file paths, a done-criterion, and its verification step.
- The plan was reviewed before any code was written.
- Progress is checkable per task without asking for a status update.

## Pitfalls

- Writing code before the spec is approved, then discovering the wrong thing was built.
- Creating tasks so large they cannot be verified in one sitting or stopped safely.
- Giving tasks done-criteria like "implement the feature" with no observable evidence.
- Planning behavior without naming the files, so the work has no concrete footprint.
- Skipping verification steps and claiming completion from edits alone.
- Letting scope grow mid-task without revisiting the plan.
""",
    },
    {
        'id': 'technical-writing',
        'name': 'Technical Writing',
        'description': 'Docs by content type, ADRs, and actionable issue reports.',
        'tags': ['docs', 'writing'],
        'content': """# Technical Writing

A method for writing documentation that matches its purpose — tutorials, how-tos, references, READMEs, architecture decision records, and issue reports.

## When to use

Use this whenever you write any documentation: a README, a guide, an API reference, a design or decision record, or a bug report. Match the structure to the content type instead of forcing everything into one template.

## Procedure

- Choose the right structure per type. Use a tutorial to walk someone through learning by doing, a how-to to solve one concrete problem, a reference to describe every option exhaustively, and a README to give orientation: what, why, quick start, then pointers.
- Use active voice and direct address. Write "you run the command" and "the function returns" rather than passive or impersonal phrasing, and keep sentences short and imperative where you give instructions.
- Show real code samples. Give runnable, copy-pasteable examples with their expected output. Keep examples minimal, current, and consistent with the version you document.
- Write an ADR for decisions. A lightweight architecture decision record captures the context, the decision, and the alternatives considered, in a few short sections, so future readers understand why the code is the way it is.
- Structure reference material to be scanned. Use stable headings, tables, and consistent field descriptions so readers can find one fact without reading everything.
- Update docs with the code. Any behavioral or architectural change must ship with its doc update in the same change, so the documentation never silently drifts from reality.

## Issue and bug reports

Structure bug reports and feature issues so a maintainer can act without follow-up questions:

- Give a minimal, unambiguous reproduction sequence with exact commands and input.
- State the expected behavior versus the actual observed behavior, including raw error output.
- Record the environment: OS, runtime version, package versions, relevant configuration.
- State impact and scope: whether it blocks, who it affects, and whether a workaround exists.

## Verification

- A stranger can run the project from the README alone.
- Every code sample copy-pastes clean on the documented version.
- Recent decisions each have an ADR.

## Pitfalls

- Writing one long tutorial-shaped document for a reference need, forcing readers to read everything to find one fact.
- Using passive, impersonal voice that obscures who does what.
- Shipping code changes without updating the matching docs.
- Copy-pasting code that is out of date or will not run.
- Writing an ADR only after the decision is unrecoverable, or not writing one at all.
- Filing an issue so vague it needs a second round of questions before anyone can act.
""",
    },
    {
        'id': 'web-performance',
        'name': 'Web Performance',
        'description': 'Core Web Vitals diagnosis plus bundle and waterfall budgets.',
        'tags': ['web', 'performance'],
        'content': """# Web Performance

Diagnose and fix user-perceived performance: Core Web Vitals, bundle weight, and the critical request waterfall.

## When to use

- A page, component, or build is slow to load, render, or respond.
- You are setting or enforcing performance budgets for a frontend.
- A Lighthouse, RUM, or field-data report flags LCP, INP, or CLS.
- You are reviewing a change that touches the critical rendering path.

## Procedure

- Measure before you change anything. Record LCP, INP, CLS, TTFB, and bundle bytes on a representative device and network, so you can prove whether a fix helped.
- Triage by metric, not by guesswork. LCP is about resource timing and render-blocking; INP is about main-thread work and event handlers; CLS is about layout stability.
- Check the request waterfall for the LCP element. Its image or block should be discoverable, fetchable, and renderable as early as possible.
- Remove or defer render-blocking CSS and JavaScript on the critical path. Split CSS so the above-the-fold rules arrive first.
- Add preconnect and dns-prefetch to the few third-party origins you actually need; drop the rest.
- Stream and lazy-load below-the-fold content instead of shipping it eagerly.
- Set bundle budgets in CI (size, gzip/brotli, and per-route). Fail the build on regressions so weight cannot creep in silently.
- Audit re-renders and derived state in reactive UIs. Memoize only where profiling shows a real cost; identify layout thrash and synchronous work on the main thread.
- Reserve space for images, embeds, and async content to keep CLS near zero, and honor dimension attributes.

## Verification

- Vitals measured before and after on a throttled profile show the gain.
- Bundle budgets enforced in CI stay green.
- No new render-blocking requests on the critical path.

## Pitfalls

- Optimizing without a baseline, so you cannot tell if a change regressed or improved anything.
- Measuring only in a fast, wired, cached environment where the real-world bottleneck never appears.
- Chasing a single metric while breaking another, such as deferring an image and inflating LCP.
- Adding preconnect to many hosts, which the browser must then probe, doing more harm than good.
- Premature memoization that adds overhead and complexity without moving a real bottleneck.
- Shipping a large runtime library when a small, purpose-built alternative would do.
""",
    },
    {
        'id': 'web-accessibility',
        'name': 'Web Accessibility',
        'description': 'WCAG-minded audit: semantics, keyboard, forms, motion.',
        'tags': ['web', 'accessibility'],
        'content': """# Web Accessibility

Audit and repair a web interface for WCAG conformance: semantics, keyboard operation, forms, color, and motion.

## When to use

- You are building or reviewing a component, page, or whole app for accessibility.
- A user reports they cannot tab, read, or understand part of the UI.
- You are doing a WCAG 2.x AA pass before release.

## Procedure

- Prefer native semantic elements (button, nav, main, heading, table) over generic divs with ARIA. Native elements bring roles, focus, and keyboard behavior for free.
- Verify a complete, logical focus order. Every interactive element must be reachable with the Tab key, and focus must be visibly indicated.
- Ensure every form control has an associated label, and that validation errors are programmatically linked to the control with an error message that is announced.
- Check text and UI contrast against WCAG thresholds, including placeholder text, borders, and focus rings.
- Provide meaningful alt text for images, empty alt for decorative ones, and real text labels for icon-only buttons.
- Respect prefers-reduced-motion: replace or disable non-essential animation for users who request it, without removing information.
- Test with a keyboard alone, then with a screen reader, then with a zoomed viewport. Do not rely on an automated checker alone; it misses many failures.
- Give interactive elements accessible names, and keep link text descriptive out of context.

## Verification

- A keyboard-only walkthrough reaches and operates every new control.
- A screen-reader pass on new flows announces names, roles, and errors.
- Contrast ratios and reduced-motion behavior verified.

## Pitfalls

- Using ARIA to fake an interaction that a native element already provides, which breaks keyboard and screen-reader behavior.
- Setting aria-hidden on an element that still contains focusable children.
- Styling focus out of existence and never restoring a visible indicator.
- Relying on color alone to convey state, such as an error, success, or active state.
- Adding motion that ignores prefers-reduced-motion.
- Auto-advancing carousels or auto-submitting forms that defeat keyboard and reading users.
""",
    },
    {
        'id': 'webapp-testing',
        'name': 'Webapp Testing',
        'description': 'Browser-automation discipline: recon-then-action, console capture.',
        'tags': ['web', 'testing'],
        'content': """# Webapp Testing

Drive browser automation with discipline: recon the page before acting, capture the console, and isolate the environment.

## When to use

- You are writing or running end-to-end browser tests against a web app.
- A test is flaky, times out, or fails only in headless mode.
- You are automating an interaction through an unfamiliar or dynamic UI.

## Procedure

- Recon before you act. Inspect the DOM or accessibility tree and confirm the element you intend to touch actually exists, is enabled, and is visible, instead of guessing at a selector.
- Prefer robust, role- and label-based selectors over brittle CSS paths that break on minor markup changes.
- Wait for the right condition: an element to appear, a network request to settle, or text to render. Prefer explicit waits over fixed sleeps.
- Capture console messages and page errors during every run. A passing interaction can still be masking an unhandled exception or a swallowed 500.
- Interact one step at a time and assert state between steps, so a failure names the exact step that broke.
- Run against an isolated profile and environment with deterministic data, so tests are reproducible and do not depend on prior runs.
- Treat timeouts and auto-retries as a signal to debug the root cause, not as a routine to be papered over.
- Handle dialogs, navigation, and file uploads explicitly instead of assuming they happen silently.

## Verification

- The console is clean through the whole automated flow.
- Selectors use roles and labels, not brittle CSS or indices.
- Runs pass in an isolated profile with no cross-test state.

## Pitfalls

- Selecting elements before they exist, causing intermittent failures that a retry then hides.
- Using fixed sleeps that pass locally but break under load or on a slow network.
- Ignoring the browser console, missing the real error behind a "looks fine" result.
- Sharing state or data between tests so order and history affect outcomes.
- Believing a headless pass guarantees the same behavior in a real browser, or vice versa.
- Asserting nothing meaningful, so the test passes even when the feature is broken.
""",
    },
    {
        'id': 'observability',
        'name': 'Observability',
        'description': 'Instrumentation and ops-readiness: logs, metrics, alerts.',
        'tags': ['ops'],
        'content': """# Observability

Make a system diagnosable in production: structured logs, critical-path metrics, and alerts that tell you when it breaks.

## When to use

- You are adding or improving logging, metrics, or alerting for a service.
- An incident was hard to debug because logs were missing, unstructured, or lacked context.
- You are defining SLOs and the alerting that backs them.

## Procedure

- Log structured records (key-value fields, not prose) so they are queryable and filterable, and include a correlation ID that threads through a single request across services and workers.
- Log at the right level: debug for detail, info for lifecycle events, warn for recoverable anomalies, error for failures. Make error paths impossible to miss.
- Define the critical path: the handful of user-visible operations that matter. Measure latency, error rate, and throughput for each.
- Turn those into SLOs with error budgets, and set alerts on the symptoms users feel, not just on internal counters.
- For every feature, ask: how would we know this failed? If nothing would surface, add the log, metric, or alert that would.
- Never swallow exceptions silently. If a failure is handled, record it at the right level; if it is ignored, that is a bug worth surfacing.
- Add context at each hop: tenant, request id, duration, status, and the inputs needed to reproduce.
- Test your instrumentation, not just your code. Verify that a triggered alert actually fires and is actionable.

## Verification

- Each alert path fires correctly when triggered in staging.
- A correlation ID follows one request across every service and log.
- The dashboard answers "is it broken and who owns it" within thirty seconds.

## Pitfalls

- Logging prose that no one can query, or logging everything at one level so signal is buried in noise.
- Alerting on every minor spike, producing fatigue that makes real alerts get ignored.
- Measuring internal plumbing while the user-facing operation stays dark.
- Swallowing exceptions in a try/catch that logs nothing, hiding the very failure you need to see.
- Forgetting correlation IDs, so a single request becomes a dozen unrelated log lines.
- No SLO, so there is no definition of what "healthy" even means.
""",
    },
    {
        'id': 'migration-and-deprecation',
        'name': 'Migration and Deprecation',
        'description': 'Safe schema and dependency migration: expand/contract, deprecation policy.',
        'tags': ['database', 'planning'],
        'content': """# Migration and Deprecation

Migrate schemas and dependencies without downtime or breakage, and deprecate with a policy that users can rely on.

## When to use

- You are renaming or dropping a column, table, or API.
- You are upgrading or removing a dependency with public consumers.
- You need to change a schema while old and new code run side by side.

## Procedure

- Follow expand/migrate/contract for schema changes: first add the new column or table without removing the old, then backfill and migrate data and switch writers, and only after all readers are updated, drop the old artifact.
- Rename by adding the new name, copying data, updating all callers, and deleting the old name. Never rename in place on a live system.
- Order drops carefully. A column must be unused by every reader before it can be dropped; verify with the query surface, not by hope.
- Backfill in batches to avoid long locks and transaction bloat, and verify row counts before and after.
- Audit your dependency tree first. Triage each dependency by ownership, maintenance activity, license, and how deeply your code depends on it.
- Bump versions with breakage in mind: read the changelog and the diff, run the full test suite, and check for removed APIs before merging.
- Write a deprecation policy: a deprecation notice period, a migration guide, a removal schedule, and a way for consumers to see what is deprecated.
- Run a trial migration on a staging copy that matches production scale before touching the real system.

## Verification

- The migration runs forward and rolls back cleanly on a production copy.
- Old and new code coexist safely mid-rollout (expand before contract).
- Every deprecation notice carries a removal date and a migration path.

## Pitfalls

- Dropping a column or API while old code still reads or calls it, breaking the running system.
- Renaming in place, which breaks every in-flight request and reader.
- Backfilling in one giant transaction that locks the table or exhausts memory.
- Upgrading a major version without reading the changelog, then discovering removed behavior in production.
- Deprecating without a notice period, stranding consumers who had no time to migrate.
- Assuming the migration works because it passed on a tiny, unrepresentative dataset.
""",
    },
    {
        'id': 'sql-database-review',
        'name': 'SQL Database Review',
        'description': 'Query plans, indexing, N+1, parameterization review.',
        'tags': ['database'],
        'content': """# SQL Database Review

Review database access for correctness and performance: query plans, indexes, N+1 patterns, and parameterization.

## When to use

- You are reviewing a change that queries the database, adds an index, or changes a schema.
- A query is slow in production or is returning unexpected results.
- You are on the lookout for scalability problems like N+1.

## Procedure

- Run EXPLAIN (or the equivalent plan command) on every non-trivial query. Look for full table scans, cartesian joins, and wildly inaccurate row estimates, and confirm the plan matches what you expect.
- Verify each query is covered by an index that actually gets used. A new query usually wants a matching composite index; check the column order in the index against the predicates in the WHERE clause.
- Confirm every index you add is justified by the workload, and watch for redundant or overlapping indexes that bloat writes.
- Hunt for the N+1 pattern: a query that loops and issues one more query per row. Replace it with a single batched query or a join, and verify the number of statements issued drops.
- Check for connection and resource leaks. Ensure cursors, sessions, and transactions are closed on every path, including error paths.
- Demand parameterized queries. If SQL is built by string concatenation with user input, fix it before anything else; it is both an injection risk and a plan-cache killer.
- Sanity-check migrations: columns and tables added or dropped line up with the queries that reference them, and default values and constraints match reality.
- Bound results and add appropriate limits, timeouts, and deadlock retries where the query can grow unbounded.

## Verification

- EXPLAIN shows index use on every hot-path query.
- Traces show no N+1 patterns or unbounded result sets.
- No string-interpolated SQL remains (grep for interpolation at query sites).

## Pitfalls

- Writing a query and never looking at its plan, letting a scan or a nested loop ship unnoticed.
- Adding an index that the query never uses, because the predicate or column order does not line up.
- Leaving an N+1 loop in place because the test data is too small to expose it.
- Building SQL by string interpolation, which invites injection and poisons the plan cache.
- Leaking connections on an early-return or exception path, quietly exhausting the pool.
- Assuming the production data distribution matches the tiny dev sample you tuned against.
""",
    },
    {
        'id': 'release-checklist',
        'name': 'Release Checklist',
        'description': 'Pre-merge gate, rollout safety, and distribution readiness.',
        'tags': ['ops', 'quality'],
        'content': """# Release Checklist

Run a pre-merge gate and a safe rollout, including packaged and distributed builds.

## When to use

- You are about to merge a change or cut a release.
- You are doing a staged rollout of a feature or a version.
- You are shipping a packaged build (installer, container, device app, binary).

## Procedure

- Pass the quality gate: tests green, lint clean, and the production build succeeds from a clean checkout.
- Review the change for breaking changes to public APIs, data formats, or existing behavior, and call them out explicitly rather than burying them.
- Use feature flags or a phased rollout so you can disable a feature quickly without a full redeploy.
- Sequence dependent changes so no partial state ships: run migrations before the code that needs them, and never deploy code that expects a schema not yet applied.
- Write a rollback plan before you ship. Know the exact step to revert, what data is touched, and whether a forward-only fix is required.
- Verify the release in a production-like environment, not just in CI, and watch the metrics and error rate during the rollout window.

## Distribution and binary packaging profile

- Verify packaging integrity: release artifacts, containers, or distributed binaries are signed with valid certificates for the target channel.
- Verify release metadata: changelogs, user-facing notes, configuration schemas, and version bumps match what actually shipped.
- Audit distribution permissions: runtime permissions, consent disclosures, and environment prerequisites match the declared features.
- Roll out staged: a small percentage, beta channel, or canary instance first, then widen — never 100% at once.
- Monitor post-release signals: crash rates and key metrics immediately after release, with a pre-planned pause or rollback trigger.

## Verification

- The gate passes from a clean checkout, not just CI cache.
- The rollback step is rehearsed or documented command-for-command.
- Metrics and error rates are watched through the whole rollout window.

## Pitfalls

- Skipping the gate and merging a change that breaks the build or a test.
- Shipping a breaking change silently and surprising consumers.
- Deploying code before its migration, so the running system hits missing columns or tables.
- Releasing without a rollback path, then being stuck in a bad state.
- Shipping unsigned or mis-signed packages that fail to install or update.
- Releasing to everyone at once with no staged channel to catch a bad build.
""",
    },
    {
        'id': 'supply-chain-audit',
        'name': 'Supply Chain Audit',
        'description': 'Dependency risk review: advisories, abandonment, install scripts.',
        'tags': ['security', 'ops'],
        'content': """# Supply Chain Audit

Review a dependency tree for risk: known advisories, abandoned upstreams, publisher concentration, and install-time behavior.

## When to use

- You are adding a new dependency, or reviewing the existing tree for risk.
- You are responding to a disclosed vulnerability in a transitive dependency.
- You are deciding whether a dependency should exist at all.

## Procedure

- Match every vulnerable dependency to its fixed version. Do not stop at "there is a CVE"; confirm whether the fixed release is compatible, upgrade, and re-test.
- Check the upstream for abandonment: last commit date, open issues, and maintainer responsiveness. A dependency that is dormant is a growing risk.
- Assess publisher concentration. If a handful of accounts or a single package owns a large share of your tree, a compromise of that account is a compromise of everything.
- Scrutinize install scripts (postinstall, preinstall). Build-time execution runs arbitrary code, so only trust it from well-established, audited packages, and prefer zero-install-script dependencies where you can.
- Ask whether the dependency should exist: is it a small helper you could vendor in a few lines, or a deep, maintained library you genuinely need? Prefer the smaller surface.
- Pin exact versions in production and lock the full tree so builds are reproducible, then upgrade deliberately and with the test suite.
- Review licenses for compatibility with how you distribute the software, and keep a bill of materials so the tree is auditable.
- Re-audit periodically, not just when an incident makes the news, because risk changes as upstreams age.

## Verification

- Every advisory is matched against the locked versions actually installed.
- No unmaintained package sits on a critical path without a replacement plan.
- Install scripts and post-install hooks of new dependencies are reviewed.

## Pitfalls

- Treating a CVE as fixed by adding a patch number without verifying the fix is actually applied and compatible.
- Ignoring abandoned upstreams until they break, then discovering no one is patching them.
- Depending on a typosquat or near-miss package name that silently shipped with unexpected code.
- Allowing install scripts from unvetted packages to run arbitrary code at build time.
- Keeping a dependency you barely use that could be vendored or dropped.
- Depending on unpinned or floating versions, so the build changes out from under you.
""",
    },
    {
        'id': 'mobile-app-architecture',
        'name': 'Mobile App Architecture',
        'description': 'Maintainable Android/iOS structure: layers, DI, navigation, offline.',
        'tags': ['mobile', 'architecture'],
        'content': """# Mobile App Architecture

Design a maintainable mobile codebase by separating concerns, wiring dependencies deliberately, and planning for offline behavior from day one.

## When to use

Use this whenever you start a new mobile app, introduce a significant feature, or refactor a codebase where screens talk directly to networks and databases. Apply it before choosing the folder structure, dependency graph, or navigation model. It applies whether you build with Kotlin and Compose, Swift and SwiftUI, or any mix of the two.

## Procedure

- Separate responsibilities into clear layers: presentation (UI state and rendering), domain (business rules), and data (repositories, local persistence, remote sources). Keep each layer depending only on the one below it.
- Model screen state as an explicit, immutable state object (data, loading, error, empty) exposed via a single observable source, rather than scattering flags across views.
- Use dependency injection — constructor injection with a container (Koin, Hilt, Dagger) on Android, or a composition-root pattern on iOS (e.g., a factory or app environment object) — so tests can substitute fakes.
- Decide navigation deliberately: type-safe routes on Android or a coordinator/router on iOS that owns navigation and stays decoupled from the views it presents.
- Move all network, disk, and heavy compute off the UI thread. On Android use coroutines with the IO dispatcher; on iOS use async/await or Combine and never block the main actor.
- Design offline-first: repositories serve cached data immediately, reconcile remote updates, and expose sync state so the UI can reflect stale-versus-fresh data.
- Keep a small app to a pragmatic single-module split; only introduce feature modules when build time, team ownership, or true independence justify the added overhead.

## Pitfalls

- Letting the view layer reach straight into the network client or database, entangling UI with transport details and making unit tests impossible.
- Injecting singletons everywhere as a shortcut, which hides the dependency graph and makes scoping bugs hard to reason about.
- Putting business logic in the view, so the same rule diverges across screens and is unreachable by fast unit tests.
- Designing offline support as an afterthought, forcing a rewrite when a network is unreachable.
- Over-modularizing a small app into dozens of packages, paying coordination cost without any benefit.
- Ignoring configuration/session state (auth, user, feature flags) when wiring layers, so a logout or token refresh leaves stale state visible.

## Verification

- Unit-test the domain and data layers with fakes and no network, and confirm the view renders from a single state object.
- Build with a clean checkout to catch missing wiring, and run the app offline to confirm cached data still renders.
- Replace one repository with a fake in a UI test to confirm the presentation layer never depends on transport details.
- Keep a dependency graph of modules and confirm there are no cycles or cross-layer jumps as the app grows.
""",
    },
    {
        'id': 'mobile-lifecycle-state',
        'name': 'Mobile Lifecycle and State',
        'description': 'Kill lifecycle/state bugs: process death, restoration, cancellation.',
        'tags': ['mobile', 'reliability'],
        'content': """# Mobile Lifecycle and State

Build apps that survive configuration changes, backgrounding, and process death without losing user state or leaking work.

## When to use

Use this when writing UI that holds state, when starting background or long-running work tied to a screen, or when handling deep links and app restoration. Treat it as a checklist for any screen that collects user input or shows transient progress, whether it is Kotlin and Compose or Swift and SwiftUI.

## Procedure

- Distinguish ephemeral UI state (scrolled position, draft text) from durable state (saved accounts, completed steps), and persist each at the correct scope.
- On Android, handle process death and configuration changes by saving UI state via rememberSaveable or SavedStateHandle, and keep screen data in a ViewModel that survives rotation.
- Understand recomposition: write pure composables that react to state rather than mutating it, and scope effect/coroutine lifecycles to the composable or ViewModel that owns them.
- On iOS, understand SwiftUI identity: prefer stable identifiers or structural identity over index-based identity so views keep their state across updates, and model restoration through scene storage or the scene's restoration delegates.
- React to background and foreground transitions: suspend non-essential work on backgrounding and refresh stale data on foregrounding rather than assuming the process stayed alive.
- Handle deep links by routing through a single navigation entry point that restores the relevant screen and re-hydrates its state from a source of truth.
- Cancel async work when its owner is torn down. Cancel coroutines with the owning scope; cancel tasks or Combine subscriptions when the view disappears or the actor scope ends. Keep cancellation idempotent and cooperative.

## Pitfalls

- Holding state only in the view, so it vanishes on rotation (Android) or when a SwiftUI view is recreated.
- Leaking a coroutine, task, or subscription past the life of its screen, causing work that outlives the UI and drains resources.
- Assuming the process never dies; the system can kill a backgrounded app at any moment, and unsaved input is lost.
- Using index-based identity in SwiftUI lists so rows swap state when the data order changes.
- Ignoring cancellation inside long operations, so a cancelled task keeps running until it finishes and ignores the stop signal.
- Restoring state from a snapshot that is stale, showing the user an old screen after the underlying data changed.

## Verification

- Rotate the device and background-and-foreground the app to confirm state survives.
- Force-stop the app and relaunch through a deep link to confirm restoration still works.
- Kill a long-running task mid-way and confirm its owner and resources are released cleanly.
- Test with the OS's aggressive battery or background restrictions enabled to catch hidden kill paths.
""",
    },
    {
        'id': 'mobile-performance-battery',
        'name': 'Mobile Performance and Battery',
        'description': 'Responsiveness, resource use, and battery discipline on devices.',
        'tags': ['mobile', 'performance'],
        'content': """# Mobile Performance and Battery

Keep a mobile app responsive and gentle on device resources by profiling on real hardware and budgeting work that costs CPU, memory, and battery.

## When to use

Use this when a screen feels janky, startup is slow, memory grows over time, the device runs hot, or the OS flags heavy background usage. Apply it before shipping any feature that runs repeated work, senses the environment, or syncs data in the background.

## Procedure

- Measure cold and warm startup time on a mid-range device, not just a simulator or the latest flagship, and set a budget you can defend.
- Profile frame pacing with the platform tools (Android Studio profiler or Perfetto; Instruments on iOS) and fix long main-thread / main-actor work that causes dropped frames.
- Watch memory: look for leaks and unbounded caches, measure peak and steady-state usage, and validate that long lists and image grids recycle or render lazily.
- Audit wakeups and background work: coalesce jobs, respect battery-saving modes, and avoid polling when a push, an event, or a scheduled coalesced task will do.
- Treat sensors and network as power costs: reduce sensor sampling rates when possible, batch network calls, compress payloads, and avoid keeping connections open without need.
- Prefer push or coalesced background sync over frequent foreground refresh; let the OS schedule deferred work through WorkManager (Android) or BGTask (iOS).
- Cache aggressively at the right level but with size and eviction bounds, and reconcile cache updates so staleness is explicit rather than silent.
- Validate on device with representative conditions (poor signal, low battery, backgrounded), and measure the delta of any change against a baseline rather than trusting intuition.

## Pitfalls

- Optimizing without measuring first, spending effort on code that was never the bottleneck.
- Profiling only on a simulator or an emulator, where CPU and battery behavior do not match physical hardware.
- Running tight polling loops or frequent sensor reads that keep the device awake and drain the battery.
- Allowing caches and caches of images to grow without bounds, causing steady memory growth and eventual pressure.
- Doing heavy work on the main thread so the UI janks under load.
- Treating a single fast interaction as proof of health while ignoring repeated, steady-state, or backgrounded behavior.

## Verification

- Capture a before-and-after profile for any change and confirm the metric you targeted actually improved.
- Run a prolonged session and verify memory returns to baseline rather than climbing monotonically.
- Check battery and wakeup stats on a physical device after a background-sync feature ships.
""",
    },
    {
        'id': 'mobile-permissions-privacy',
        'name': 'Mobile Permissions and Privacy',
        'description': 'Least-privilege permissions, consent, and sensitive-data handling.',
        'tags': ['mobile', 'privacy'],
        'content': """# Mobile Permissions and Privacy

Request the minimum permissions you need, ask with clear consent, and protect sensitive data in storage, transport, logs, and analytics.

## When to use

Use this whenever your app accesses device capabilities (location, camera, microphone, contacts, photos, notifications) or handles personal or sensitive data. Apply it when designing permission flows, storing secrets, or deciding what to send to analytics or logs.

## Procedure

- Request permissions only at the point of need, in context, with an explanation of why and what the user gains, rather than demanding everything at first launch.
- Write rationale text that states the concrete benefit and the data being accessed, and route users to the system settings when a permission is denied or revoked.
- Know the platform differences: Android groups permissions and offers runtime prompts plus per-permission settings; iOS shows one-time, while-in-use, and always options with its own prompts and review flow.
- Implement least privilege: request the narrowest scope (approximate location, selected photos, while-in-use) and degrade gracefully when a permission is missing.
- Handle denial and revocation paths explicitly: detect the state, explain why the feature is unavailable, and provide a way to change it without a dead end.
- Keep secrets and personal data out of plaintext storage and logs. Use the platform keychain/key store for credentials, avoid logging tokens or PII, and redact before sending telemetry.
- Minimize data collection by default: collect only what the feature needs, anonymize or aggregate where possible, and give users a control to review or delete it.
- Apply privacy defaults: do not upload sensitive data unless required and consented to, and keep on-device processing as the default where feasible.

## Pitfalls

- Requesting every permission up front, triggering user distrust and higher denial rates.
- Offering no way forward after denial, leaving the user stuck with a broken feature and no explanation.
- Copying iOS behavior to Android and vice versa without accounting for the different permission models and prompts.
- Logging tokens, email addresses, or location coordinates in plaintext and shipping them to a log aggregator.
- Storing API keys or secrets in code or in insecure local storage readable by other processes.
- Sending full user content to analytics when only an aggregate or a hash would have been sufficient.

## Verification

- Walk every permission flow twice: once granting and once denying, and confirm both paths are handled.
- Grep logs and analytics payloads for token, email, and location fields to confirm nothing sensitive leaks.
- Revoke a permission in system settings and confirm the app recovers and offers a re-enable path.
""",
    },
    {
        'id': 'academic-paper-review',
        'name': 'Academic Paper Review',
        'description': 'Rigor review for scholarly papers: claims, methods, reproducibility.',
        'tags': ['academic', 'review'],
        'content': """# Academic Paper Review

Review a scholarly paper rigorously by checking whether the question, claim, method, and evidence line up, and by judging reproducibility rather than just novelty.

## When to use

Use this when reviewing a paper for a journal or conference, when reading a submission critically before citing it, or when assessing a colleague's draft. It applies to empirical, ML, systems, and theoretical work alike.

## Procedure

- Identify the central question and the paper's specific claims, then trace each claim to the evidence offered and confirm the method actually measures what the claim asserts.
- Check the baseline and control setup: are comparisons fair, are baselines reasonably tuned, and are the chosen settings representative rather than cherry-picked?
- Scrutinize statistics: are effect sizes reported with uncertainty, are significance claims justified, and are the tests appropriate for the data distribution and sample size?
- Evaluate limitations honestly: does the paper state what it cannot do, and are those limitations consistent with the scope of the claims it makes?
- Assess reproducibility: for ML, are code, data, seeds, and hyperparameters available and pinned; for systems, is the environment and configuration specified; for empirical work, is the protocol repeatable?
- Separate your verdicts by dimension: correctness (is the reasoning sound), novelty (is it new and non-obvious), clarity (can a reader follow it), and confidence (how sure are you overall).
- Look for missing experiments or ablations that would distinguish the proposed contribution from a simpler alternative, and say what concrete addition would settle your doubts.
- Consider threats to validity explicitly: internal, external, construct, and statistical, and note which ones materially weaken the conclusions.

## Pitfalls

- Confusing novelty with correctness, praising a new-sounding idea while its evidence does not support the claim.
- Accepting a headline number without checking the baseline, the tuning, or the uncertainty attached to it.
- Focusing only on the results section and skipping the method, missing a mismatch between what was done and what is claimed.
- Demanding endless ablations that are nice-to-have rather than the single experiment that would validate the mechanism.
- Overlooking whether the code and data are actually available and pinned, so the results cannot be reproduced.
- Writing a vague critique without pointing to a specific step, claim, or missing experiment that could be fixed.

## Verification

- Re-derive the paper's central claim from its method and evidence in a sentence, and check whether the claim would survive a reasonable reader's scrutiny.
- Attempt to reproduce one reported result from the stated setup and note where the paper makes that difficult.
- Draft the review as separate verdicts for correctness, novelty, clarity, and confidence before merging them.
""",
    },
    {
        'id': 'experiment-discipline',
        'name': 'Experiment Discipline',
        'description': 'Interpretable, repeatable experiments: hypotheses, controls, logs.',
        'tags': ['experimentation', 'ml'],
        'content': """# Experiment Discipline

Run experiments that are interpretable and repeatable by defining hypotheses and controls up front, pinning the configuration, and recording every decision.

## When to use

Use this whenever you change a model, tune parameters, run an A/B test, compare approaches, or investigate a result. Apply it to any comparison that might influence a product or research decision.

## Procedure

- State a falsifiable hypothesis and the primary metric before you start, so success is defined in advance rather than after seeing the results.
- Change one variable at a time when possible; when that is impractical, use a design that makes the contributing factors identifiable and say which one changed.
- Define stopping criteria up front (fixed budget, pre-registered threshold, power analysis) so you do not keep collecting until a result looks favorable.
- Version everything that affects the outcome: code, config, data, seeds, environment, and the model or pipeline version, so any run can be reproduced exactly.
- Fix the random seed and environment where nondeterminism matters, and run multiple seeds or repetitions to estimate variance rather than trusting a single run.
- Include controls: a no-op or baseline condition, and where feasible a replication of the experiment to confirm the result is stable.
- Run ablations that isolate the effect of each component, so you know what actually drives the gain and not just that the whole pipeline improved.
- Keep a decision log: record what you tried, what changed between runs, what the outcome was, and the reasoning behind each pivot, so future readers can reconstruct the journey.
- Distinguish the hypothesis test from the exploration that motivated it, and report which results are confirmatory and which are exploratory.

## Pitfalls

- Hunting for a configuration that passes until it does, then reporting it as if it were the plan, which inflates confidence.
- Changing multiple variables at once and being unable to attribute the effect to any single change.
- Failing to pin seeds or versions, so a colleague cannot reproduce the result and the numbers drift between runs.
- Deciding the metric after seeing the outcome, making the result look stronger than the data supports.
- Stopping early just because the direction is favorable, without respecting the pre-planned stopping rule.
- Running one lucky trial and treating it as truth instead of estimating variance across repetitions.

## Verification

- Re-run a completed experiment from its recorded config and confirm it reproduces the recorded numbers.
- Confirm the decision log lets a stranger reconstruct why each parameter was set the way it was.
""",
    },
    {
        'id': 'dataset-hygiene',
        'name': 'Dataset Hygiene',
        'description': 'Traceable, licensed, versioned datasets with honest splits.',
        'tags': ['data', 'ml'],
        'content': """# Dataset Hygiene

Build datasets that are traceable, legally usable, and versioned, so every model and analysis can be tied to exact, honest data.

## When to use

Use this when assembling, cleaning, sharing, or versioning a dataset, or when auditing an existing dataset for licensing or quality issues. Splitting strategy for evaluation lives in ml-evaluation-rigor — this skill covers storage, lineage, and quality.

## Procedure

- Record provenance for every source: where the data came from, who collected it, when, and under what conditions, so the lineage is auditable.
- Confirm licensing and consent before use: check the license terms, honor redistribution and attribution requirements, and verify that personal data was collected with appropriate consent.
- Inspect the schema and labels: validate types and ranges, find and document missing values, decide a handling policy for them, and remove or flag exact duplicates.
- Build a data dictionary describing each field, its meaning, allowed values, and any transformations, so downstream consumers interpret it consistently.
- Version the dataset with a unique identifier and changelog, and record the lineage of any derived dataset back to its source.
- Declare the split contract alongside the data: name the unit of independence (e.g. one person, one device) and whether the data is temporal, so the evaluation step can split honestly.
- Monitor for bias and drift: check that the distribution of sensitive attributes and the feature distribution over time are understood and documented, not just assumed.

## Pitfalls

- Reusing a dataset whose license forbids the intended use, creating a legal liability that surfaces at publication time.
- Dropping rows with missing values silently, biasing the distribution without documenting it.
- Failing to version data, so a model's results cannot be tied to the exact data that produced them.
- Duplicating or de-duplicating without checking, either inflating the effective dataset or removing legitimate near-identical samples.
- Recording no data dictionary, so each consumer re-guesses what fields mean.

## Verification

- Verify the dataset hash, version, license, and dictionary are recorded next to any model or analysis that consumes it.
- Confirm the declared unit of independence and temporal nature are written down before evaluation begins.
""",
    },
    {
        'id': 'ml-evaluation-rigor',
        'name': 'ML Evaluation Rigor',
        'description': 'No-leakage evaluation: separation, baselines, uncertainty.',
        'tags': ['ml', 'evaluation'],
        'content': """# ML Evaluation Rigor

Evaluate machine learning models without leakage, against strong baselines, and with uncertainty and calibration reported honestly.

## When to use

Use this whenever you report a model's performance, compare it with alternatives, or decide whether to deploy based on offline metrics. Apply it to classification, regression, ranking, and generative evaluations alike.

## Procedure

- Keep training, validation, and test sets strictly separate, and ensure no preprocessing or hyperparameter choice is informed by the test set.
- Prevent leakage by grouping dependent samples, splitting by time for temporal data, and avoiding feature leakage from the future or from the label itself.
- Check for contamination in text or web-scale data: verify that evaluation examples do not appear in the training corpus, especially for generative and retrieval models.
- Compare against strong baselines, including simple heuristics and prior state of the art, tuned fairly, not just against a weak default.
- Report metrics with uncertainty: give confidence intervals or variance across runs, and use the metric appropriate to the task (precision, recall, NDCG, calibration error) rather than accuracy alone.
- Report calibration: check that predicted probabilities align with observed frequencies, especially when decisions depend on confidence.
- Evaluate subgroup slices, not just the aggregate: performance on minority groups, rare classes, and edge cases often differs sharply from the headline number.
- Acknowledge the offline-versus-production gap: label distribution, latency, and user feedback differ from offline data, so treat offline metrics as a proxy and validate on real traffic.
- Check that preprocessing pipelines are identical between training and evaluation, so a mismatch in normalization or featurization does not quietly change results.

## Pitfalls

- Leaking training data into the test set through shared groups, future-dated rows, or contaminated text, inflating results.
- Tuning on the test set and then reporting it as held-out performance.
- Reporting a single aggregate metric that hides a much worse performance on an important subgroup.
- Comparing against an untuned or trivial baseline to make a small gain look decisive.
- Reporting accuracy with no uncertainty, so differences that could be noise are treated as real.
- Treating offline numbers as a guarantee of production behavior without checking for distribution shift.

## Verification

- Re-run the evaluation with a different seed or fold and confirm the reported interval overlaps.
- Spot-check that preprocessing matches train and eval, and that no test rows share a group with training.
""",
    },
    {
        'id': 'model-review',
        'name': 'Model Review',
        'description': 'Reliability/risk review for ML-enabled systems.',
        'tags': ['ml', 'review'],
        'content': """# Model Review

Review an ML-enabled system for reliability and risk by examining intended use, failure modes, data and prompt interactions, and the operational safeguards around it.

## When to use

Use this before deploying a model into a real system, after a model changes, or when auditing an existing ML feature. Apply it to prediction models, ranking, and LLM-based features alike.

## Procedure

- Define the intended use and user: who uses the model, for what decision, and what a reasonable person would and would not do with its output.
- Enumerate failure modes, misuse, and limits: where does the model degrade, what inputs break it, and what happens when users act on an incorrect output.
- Inspect the data, prompt, and tool interactions: how the model's inputs and tools are assembled, and how the surrounding code can amplify or contain model errors.
- Check fairness, robustness, and privacy: do protected groups fare worse, does small input perturbation change output drastically, and does the system expose or leak sensitive data.
- Define oversight: is there a human in the loop, are risky outputs flagged, and is there a mechanism for users to report errors.
- Establish monitoring, rollback, and retraining gates: what metrics are tracked in production, what thresholds trigger an alert, and how the model can be rolled back or retrained when quality degrades.
- Document the review outcome and the residual risks, and re-review whenever the data, prompt, or model changes materially.
- Consider the blast radius: what is the worst harm a wrong output can cause, and is that harm bounded by validation or human review.

## Pitfalls

- Reviewing only the model card and skipping how the model is embedded in code, prompts, and tools that can multiply its errors.
- Assuming the model is always right and ignoring the failure modes that matter most for the specific decision it informs.
- Deploying with no monitoring, so silent quality decay goes unnoticed until a user-impacting incident.
- Omitting a rollback path, leaving the team unable to revert a harmful model quickly.
- Testing fairness or robustness only on the aggregate, missing worse outcomes for specific subgroups or adversarial inputs.
- Treating the review as a one-time event and not revisiting it after prompt or data changes.

## Verification

- Trace one wrong-output scenario end to end and confirm the system's guardrails actually contain the harm.
- Confirm the monitoring dashboards and rollback path are wired and tested, not just documented.
- Re-run the review after any prompt, data, or model change and diff the risks against the prior review.
""",
    },
    {
        'id': 'sre-incident-response',
        'name': 'SRE Incident Response',
        'description': 'From detection to blameless follow-up: triage, mitigate, learn.',
        'tags': ['sre', 'operations'],
        'content': """# SRE Incident Response

Move a live incident from detection to mitigation to a blameless postmortem with durable, verified follow-up actions.

## When to use

Use this when an outage, degradation, or security event is detected, when a runbook has no clear owner, or when you need to run a postmortem that actually changes the system.

## Procedure

- Declare an incident early and assign a severity; ambiguous and potentially severe signals are better over-declared than under-declared.
- Establish roles explicitly: a single incident commander owns coordination, a scribe records the timeline, and others focus on triage rather than improvising.
- Escalate along a known path when impact is severe, when the commander is overloaded, or when the initial response is not converging.
- Mitigate first and diagnose later: aim to contain and restore service (rollback, feature flag off, scale up, isolate a shard) before deep root-cause work.
- Prefer reversible actions: prefer a rollback or a flag flip to an irreversible data mutation, and note the mitigation you applied so it can be undone.
- Collect evidence continuously: capture the timeline, key metrics, logs, and the exact state at each step, because memory degrades fast after the incident.
- Communicate status to stakeholders on a cadence, with severity, impact, current action, and next update, without inventing a root cause you do not yet have.
- Hold a blameless postmortem: reconstruct what happened factually, identify contributing factors in the system and process rather than blaming individuals.
- Turn findings into a small number of durable, actionable items with owners and due dates, and verify they are actually implemented rather than filed and forgotten.

## Pitfalls

- Jumping to diagnosis and root-cause analysis while the service is still down, wasting time that should go to mitigation.
- Acting without declaring roles, so people step on each other and no one owns communication.
- Taking an irreversible action (like deleting data) in the heat of the moment when a reversible rollback existed.
- Relying on memory instead of a recorded timeline, producing a postmortem full of gaps and guesses.
- Running a blame-oriented review that punishes individuals and suppresses honest reporting of the next incident.
- Writing a postmortem with a long action list that no one tracks, so the same incident recurs.

## Verification

- Re-read the postmortem action list after a few weeks and confirm each item is either done or explicitly deferred.
- Run a tabletop drill against a realistic scenario to confirm roles and escalation are known before a real incident.
""",
    },
    {
        'id': 'cli-ux-writing',
        'name': 'CLI UX Writing',
        'description': 'Discoverable, scriptable, safe command-line interfaces.',
        'tags': ['cli', 'writing'],
        'content': """# CLI UX Writing

Design command-line interfaces that are discoverable for humans and safe, predictable, and scriptable for machines.

## When to use

Use this when you are building or improving a command-line tool, writing help text or error messages, or deciding how commands behave in interactive versus piped contexts.

## Procedure

- Use consistent, conventional vocabulary across commands: the same verb for the same action, and standard flags (help, version, verbose, quiet) everywhere.
- Write a help message for every command that states what it does, its arguments, and the flags, and always make `--help` available at every level.
- Provide one or two concrete examples in help text showing typical real usage, since examples teach far better than a list of flags.
- Write error messages that name the failing input, say why it failed, and give the corrective action, rather than a bare stack trace or generic failure.
- Show exit codes: use zero for success, non-zero with distinct values for distinct failure classes, and document them so scripts can branch reliably.
- Add confirmations for destructive or irreversible actions, and support `--dry-run` that reports what would happen without doing it.
- Warn clearly before destructive or overwriting operations, and require an explicit flag (or interactive confirmation) to proceed.
- Make behavior scriptable: detect TTY versus pipe, suppress prompts and colors when output is not a terminal, and avoid interactive gating in non-TTY contexts.
- Offer machine-readable output (JSON or similar) for tools meant to be consumed by other programs, separate from human-friendly prose.

## Pitfalls

- Requiring interactive confirmation in a non-TTY context, hanging automated scripts that pipe the command.
- Throwing raw tracebacks at the user, which are useless for fixing the actual problem and noisy in logs.
- Overwriting or deleting files without any confirmation or dry-run, making one typo destructive.
- Emitting prose mixed with structured output, breaking parsers that expect clean JSON.
- Returning exit code zero even on failure, so scripts silently think the command succeeded.
- Using the same word for different meanings across commands, confusing users and breaking assumptions in scripts.

## Verification

- Run every command with `--help` and through a pipe, and confirm both paths behave as intended.
- Trigger each error path and confirm the message states the input, the cause, and the fix.
""",
    },
    {
        'id': 'data-wrangling-safety',
        'name': 'Data Wrangling Safety',
        'description': 'Transform data without silent corruption.',
        'tags': ['data', 'safety'],
        'content': """# Data Wrangling Safety

Transform data with confidence by handling encodings, nulls, dates, and joins carefully, and by verifying invariants so silent corruption never ships.

## When to use

Use this whenever you clean, join, aggregate, convert, or otherwise transform data for analysis or a pipeline, especially when the result feeds a decision, a model, or a downstream system.

## Procedure

- Handle encodings explicitly: read and write with a declared encoding (UTF-8 by default), and detect or reject unexpected encodings instead of silently mojibaking text.
- Decide and document the policy for nulls and missing values before transforming: distinguish genuinely missing from zero or empty, and record how each column is treated.
- Normalize dates and time zones explicitly: store timestamps in UTC with a clear instant, and convert to a local zone only at the display or reporting boundary.
- Watch numeric precision: avoid floating-point equality, understand integer overflow, and preserve the precision of identifiers and monetary amounts.
- Validate join invariants: check for many-to-many surprises and orphan keys, and assert the expected cardinality of each join rather than assuming it.
- Check de-duplication and aggregation semantics: know whether duplicates are legitimate, and verify that grouping and aggregation preserve the intended meaning.
- Verify invariants at each stage: compare row counts before and after, cross-tabulate key columns, and sample results by eye to catch structural breakage.
- Keep transformations reversible or reproducible: save both input and output (or the exact transformation and version) so any step can be re-run and audited.
- Escape and validate anything interpolated into regexes, shell commands, or delimited output, so data values cannot break the pipeline or inject behavior.

## Pitfalls

- Comparing or joining on float keys or unnormalized strings, silently failing to match values that should be equal.
- Assuming a join is one-to-one when it is many-to-many, multiplying rows without noticing.
- Treating timezone-naive timestamps as UTC or local, producing date errors that appear only in specific regions.
- Silently dropping rows with missing data and never checking how the distribution shifted.
- Passing data into a shell command or regex without escaping, letting a value with special characters corrupt output or inject commands.
- Skipping the row-count and sample checks, so a silent encoding or join bug propagates through the whole downstream pipeline.

## Verification

- Record row and column counts before and after each stage, and confirm no unexpected gain or loss.
- Sample a handful of rows at each step and eyeball them for structural breakage before trusting the output.
""",
    },
    {
        'id': 'academic-paper-writing',
        'name': 'Academic Paper Writing',
        'description': 'Structure, clarity, citations, and reproducible builds for scholarly papers.',
        'tags': ['academic', 'writing'],
        'content': """# Academic Paper Writing

Write scholarly papers whose claims, evidence, and presentation all hold up under review.

## When to use

Use this when drafting or revising a research paper, workshop submission, thesis chapter, or any manuscript where claims must be traceable to evidence.

## Procedure

- Structure around the argument: abstract, introduction with an explicit research question, methods detailed enough to repeat, results that show exactly what was found, and a discussion that separates what the evidence proves from what it suggests.
- Align every claim with evidence. Each sentence in the abstract and results must point at a figure, table, or analysis in the body; never assert a result the paper does not show.
- Practice citation hygiene: cite the origin of each claim precisely, never cite work you have not read, and keep every in-text citation paired with a full reference entry.
- Keep the build clean: compile with no undefined references or citations, use consistent labels and cross-references, number figures in order of appearance, and keep figures vector-based with legible fonts.
- End with a reproducibility statement naming the environment, versions, data, and scripts someone needs to reproduce the results.

## Verification

- Every abstract claim traces to a specific figure, table, or section.
- The manuscript compiles clean with zero warnings about references or citations.
- A reader can reconstruct the environment and rerun the analysis from the reproducibility statement.

## Pitfalls

- Overstating results in the abstract that the body never demonstrates.
- Citing a paper for a claim it does not make, or citing without reading.
- Leaving undefined cross-references, rasterized unreadable figures, or unnumbered floats.
- Burying limitations instead of stating them, so reviewers find them first.
""",
    },
]
