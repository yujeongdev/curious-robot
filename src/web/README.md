# Curious Robot Agent Lab frontend

React 19/Vite frontend for the event-sourced physics-evidence workspace. It is a presentation client: Newton remains the sole physics authority.

```bash
corepack pnpm install
corepack pnpm dev
corepack pnpm lint
corepack pnpm typecheck
corepack pnpm test
corepack pnpm build
```

Open `/runs/new`, then use **Run discovery demo** with the Python control plane or **Preview recorded fixture** for the deterministic browser-only UI fixture.

The frontend intentionally contains no browser physics engine, Monaco editor, or table framework. Live and replay both reduce the same validated `AgentEvent` stream.
