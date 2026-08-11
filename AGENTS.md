# Project Agent Rules

## Development Servers

- Do not start Next.js, FastAPI, workers, Docker services, or other long-running project commands unless the user explicitly asks.
- When browser, Playwright, or database integration testing needs a running environment, give the user the exact Cursor command to start it. Once it is available, the development task owns completing the test.

## Testing Ownership

- The task that implements a feature also owns its tests, fixtures, mocks, and verification. Do not delegate writing or running tests to the user.
- Add or update tests for the normal path, meaningful failure paths, and risk-relevant boundary cases introduced by a change.
- For a defect, add a regression test that reproduces the failure before or alongside the fix.
- Before reporting completion, run the smallest relevant test suite, lint, and type checks for files changed by the task. Report the commands, results, and any remaining gaps.
- Mock or fake OpenAI, embedding, payment, shipping, and other external services in automated tests. Automated tests must not consume paid APIs or require real credentials.
- Do not use snapshot tests as the only behavioral assertion, and do not add meaningless tests solely to increase coverage.
- If a test cannot run, state the exact blocker, unverified behavior, and residual risk. Do not claim full verification.
- Do not enforce a project-wide coverage threshold during the learning versions. Add coverage gates later based on risk and an established baseline.

## Frontend Testing

- Use Vitest, React Testing Library, `user-event`, and jsdom for component and interaction tests in `apps/web` and `packages/ui`.
- Test rendered behavior, user interaction, loading and error states, accessibility semantics, and reduced-motion behavior where relevant.
- Use Playwright only for cross-page or full-stack critical paths. Component tests must not start or depend on a real backend.

## Backend Testing

- Use Pytest, pytest-asyncio, HTTPX ASGI Transport, Ruff, and Mypy strict mode for `apps/agent-core`.
- Inject fake services or model adapters into API tests. Real OpenAI calls are manual smoke tests only.
- Database integration tests must use an isolated test database or schema. LangGraph work must include checkpoint recovery and idempotency tests.

## Backend Teaching Mode

- Apply this mode only to backend development in `apps/agent-core`. Frontend work follows the normal implementation workflow and is not paused for teaching steps.
- The user writes backend production business code. Codex guides, reviews, explains, and verifies it instead of directly applying the business-code change.
- Teach from a beginner's evolutionary development path, not from a pre-designed final architecture. Start with the smallest readable implementation that can complete one observable behavior, even when it temporarily lives in one file.
- Do not plan or create the final module tree, placeholder directories, Protocols, repositories, services, factories, dependency modules, or custom exception hierarchies before the current code demonstrates a concrete need for them.
- Introduce a library, framework feature, abstraction, or new file only after explaining the immediate problem it solves, the simpler alternative, and why the simpler version has now become insufficient. "Enterprise best practice" alone is not a valid reason.
- Prefer this learning sequence for a new backend feature: make one thin vertical slice work, test its behavior, identify an observed pain, then refactor one boundary at a time while keeping tests green.
- Let the user first experience concrete pressures such as mixed HTTP and business rules, duplicated validation, hard-to-fake external calls, repeated error mapping, an overlong file, or unsafe shared state. Extract a schema, service, repository, dependency, or exception handler only in response to one of those visible pressures.
- Before each refactor, show the current request/data flow, point to the exact troublesome code, state the invariant that must not change, and compare the before/after tradeoff in concise Chinese.
- Do not refactor or collapse already-working code merely to recreate a beginner history. For existing abstractions, reconstruct the original simple version conceptually and explain what problem the current split solves when it becomes relevant.
- Teach one primary concept per coding step. Do not combine a new Python concept, a new database pattern, a new abstraction, and a new tool in the same step.
- Maintain the user's stated knowledge profile. Use concepts the user marks as already understood directly, without creating artificial intermediate implementations or repeating introductory explanations; explain only genuinely new behavior in the Python/backend context.
- Give exactly one small, concrete coding step at a time. Identify the target file or function, observable result, important constraints, and the minimum code outline needed for the user to proceed.
- Stop after the current coding step and wait for the user to report completion. Do not silently continue into later backend features.
- After the user finishes a step, read the actual change and run the relevant tests, Ruff, and changed-file Mypy checks. Explain errors from evidence and let the user correct backend business code.
- Codex still owns automated tests, fixtures, fake services, mocks, verification tooling, learning notes, and non-business test infrastructure. This preserves the project-wide testing ownership rule.
- Use short reflection questions when they help confirm understanding of security, state, async behavior, transactions, or Agent boundaries; do not add artificial quizzes to every step.
- Keep each version within its agreed learning scope. Do not introduce later-stage abstractions, dependencies, or product features early.
- If the user explicitly asks to leave teaching mode for a specific backend task, follow that instruction only for the stated task; otherwise teaching mode remains the default.
- At the end of a backend milestone, summarize what the user implemented, the Python and AI concepts learned, automated verification results, manual checks still needed, and residual risks.

## Backend Concurrency Teaching

- Apply this rule only when writing, reviewing, or planning backend code. Do not apply it to frontend implementation or pause frontend work for concurrency teaching.
- Proactively flag concurrency whenever backend work involves shared mutable state, application singletons, request-scoped resources, `async` tasks, database sessions or transactions, read-then-write logic, inventory or money movement, retries, webhooks, idempotency, event ordering, connection pools, or rate-limited external services.
- Before the relevant backend coding step, explain the risk in concise Chinese with a concrete two-request timeline. Distinguish application/process scope, request scope, client/session scope, and durable shared state so the user can see exactly which data may be shared.
- State the invariant that must remain true, the chosen protection mechanism, and why it is proportionate. Typical mechanisms include stateless services, request-local sessions, atomic SQL conditions, transactions, row or optimistic locks, unique constraints, idempotency keys, explicit ordering, and bounded concurrency. Do not introduce concurrency machinery when the current operation is naturally isolated or read-only; explain briefly why it is safe instead.
- Add or update risk-focused tests when concurrency affects correctness. Depending on the feature, cover overlapping requests, duplicate delivery, transaction rollback, ordering, idempotency, or resource cleanup, while keeping external services mocked and database tests isolated.

## Type Check Scope

- Type-check files changed by the current task unless the user explicitly requests a full-project check or shared types/configuration require broader validation.
- If a necessary full-project check fails because of unrelated existing errors, report them as pre-existing and still provide the changed-file result.

## Figma Reading Fallback

- When a Figma design is provided, try Figma MCP first. If it cannot read the target, use the Figma REST API with the local `FIGMA_OAUTH_TOKEN`.
- Never print or expose the token value; only report whether it is present.
