type LangfuseSpan = {
  update: (attributes: Record<string, unknown>) => void;
  setTraceIO?: (attributes: { input?: unknown; output?: unknown }) => void;
};

type TraceFn = <T>(
  name: string,
  run: (span: LangfuseSpan) => Promise<T>,
  options?: { asType?: "agent" | "generation" | "tool" | "span"; traceName?: string },
) => Promise<T>;

function isLangfuseEnabled(): boolean {
  return Boolean(process.env.LANGFUSE_PUBLIC_KEY && process.env.LANGFUSE_SECRET_KEY && process.env.LANGFUSE_BASE_URL);
}

const passthroughSpan: LangfuseSpan = { update: () => undefined };

export async function withLangfuseSpan<T>(
  name: string,
  run: (span: LangfuseSpan) => Promise<T>,
  options?: { asType?: "agent" | "generation" | "tool" | "span"; traceName?: string },
): Promise<T> {
  if (!isLangfuseEnabled()) return run(passthroughSpan);
  let tracing: {
    startActiveObservation?: TraceFn;
    propagateAttributes?: <R>(attributes: { traceName?: string }, run: () => R) => R;
  };
  try {
    tracing = (await import("@langfuse/tracing")) as {
      startActiveObservation?: TraceFn;
      propagateAttributes?: <R>(attributes: { traceName?: string }, run: () => R) => R;
    };
  } catch {
    return run(passthroughSpan);
  }
  if (!tracing.startActiveObservation) return run(passthroughSpan);
  const start = () => tracing.startActiveObservation!(name, run, options?.asType ? { asType: options.asType } : undefined);
  return options?.traceName && tracing.propagateAttributes
    ? tracing.propagateAttributes({ traceName: options.traceName }, start)
    : start();
}

export async function flushLangfuse(): Promise<void> {
  const processor = (globalThis as { __remymyLangfuseProcessor?: { forceFlush?: () => Promise<void> } }).__remymyLangfuseProcessor;
  await processor?.forceFlush?.();
}
