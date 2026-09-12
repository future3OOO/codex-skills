# TypeScript Production Rules

Load this reference only when the changed surface contains TypeScript or
JavaScript.

- Keep strict TypeScript enabled.
- Require `strict: true`, `noImplicitAny: true`,
  `noUncheckedIndexedAccess: true`, `exactOptionalPropertyTypes: true`, and
  `useUnknownInCatchVariables: true` unless an existing repository contract
  explicitly differs.
- Do not use `any` as an implementation shortcut.
- Do not use `// @ts-ignore`, `// @ts-expect-error`, or `eslint-disable` unless
  the repository's decision record explicitly authorizes the exact case.
- Do not use broad `unknown as X` casts or non-null assertions on external or
  persisted data.
- Prefer explicit narrowing and exhaustiveness over assertion chains.
- Use exhaustive `switch` statements for state machines and discriminated
  unions. Make unreachable defaults fail closed.
