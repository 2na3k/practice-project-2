type LangfuseSpan = {
  update: (attributes: Record<string, unknown>) => void;
};

type TraceFn = <T>(
  name: string,
  run: (span: LangfuseSpan) => Promise<T>,
  options?: { asType?: "agent" | "generation" | "tool" | "span" },
) => Promise<T>;

function isLangfuseEnabled(): boolean {
  return Boolean(process.env.LANGFUSE_PUBLIC_KEY && process.env.LANGFUSE_SECRET_KEY && process.env.LANGFUSE_BASE_URL);
}

const passthroughSpan: LangfuseSpan = { update: () => undefined };

export async function withLangfuseSpan<T>(
  name: string,
  run: (span: LangfuseSpan) => Promise<T>,
  options?: { asType?: "agent" | "generation" | "tool" | "span" },
): Promise<T> {
  if (!isLangfuseEnabled()) return run(passthroughSpan);
  try {
    const tracing = (await import("@langfuse/tracing")) as { startActiveObservation?: TraceFn };
    if (!tracing.startActiveObservation) return run(passthroughSpan);
    return tracing.startActiveObservation(name, run, options?.asType ? { asType: options.asType } : undefined);
  } catch {
    return run(passthroughSpan);
  }
}

export async function flushLangfuse(): Promise<void> {
  const processor = (globalThis as { __remymyLangfuseProcessor?: { forceFlush?: () => Promise<void> } }).__remymyLangfuseProcessor;
  await processor?.forceFlush?.();
}
