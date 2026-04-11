# @otelmind/sdk

TypeScript SDK for [OtelMind](https://otelmind.io) — AI agent observability.

Auto-instrument OpenAI and Anthropic SDK calls, batch spans, and stream them to the OtelMind collector with zero changes to your existing code.

## Requirements

- Node.js 18+ (uses native `fetch` and `AsyncLocalStorage`)
- TypeScript 5.x (optional but recommended)

## Installation

```bash
npm install @otelmind/sdk
# peer deps — install only what you use
npm install openai            # optional
npm install @anthropic-ai/sdk # optional
```

## Quick Start

### OpenAI

```typescript
import { OtelMindClient } from '@otelmind/sdk';
import OpenAI from 'openai';

const otelmind = new OtelMindClient({
  apiKey: 'om_...',
  serviceName: 'my-agent',
});

// Patch the client — all subsequent calls are traced automatically.
const openai = otelmind.instrumentOpenAI(new OpenAI());

const response = await openai.chat.completions.create({
  model: 'gpt-4o',
  messages: [{ role: 'user', content: 'Hello!' }],
});

// Flush remaining spans before process exits.
await otelmind.shutdown();
```

### Anthropic

```typescript
import { OtelMindClient } from '@otelmind/sdk';
import Anthropic from '@anthropic-ai/sdk';

const otelmind = new OtelMindClient({
  apiKey: 'om_...',
  serviceName: 'my-agent',
});

const anthropic = otelmind.instrumentAnthropic(new Anthropic());

const message = await anthropic.messages.create({
  model: 'claude-opus-4-5',
  max_tokens: 1024,
  messages: [{ role: 'user', content: 'Hello!' }],
});

await otelmind.shutdown();
```

### Streaming

Streaming works transparently — chunks are collected in memory and the span is finalised once the stream is exhausted:

```typescript
const stream = await openai.chat.completions.create({
  model: 'gpt-4o',
  messages: [{ role: 'user', content: 'Count to 10.' }],
  stream: true,
});

for await (const chunk of stream) {
  process.stdout.write(chunk.choices[0]?.delta?.content ?? '');
}
// span is automatically ended here
```

### Manual Spans

Use `startSpan` / `ActiveSpan` to trace custom work:

```typescript
const span = otelmind.startSpan('vector-search', {
  'db.system': 'pinecone',
  'db.collection': 'embeddings',
});

try {
  const results = await vectorDB.query(embedding);
  span.end(results);
} catch (err) {
  span.setError(err as Error);
  throw err;
}
```

### Trace Wrapper

`client.trace()` automatically manages the span lifecycle and propagates async context to child spans:

```typescript
const answer = await otelmind.trace('rag-pipeline', async (span) => {
  span.setAttribute('query', userQuestion);

  const docs = await retrieveDocs(userQuestion);   // child spans propagate traceId
  const reply = await generateAnswer(docs);

  span.addTokens(promptTokens, completionTokens);
  return reply;
});
```

## Configuration

| Option | Type | Default | Description |
|--------|------|---------|-------------|
| `apiKey` | `string` | — | **Required.** Your OtelMind API key. |
| `endpoint` | `string` | `https://ingest.otelmind.io` | Collector base URL. |
| `serviceName` | `string` | — | Logical service / agent name added to every span. |
| `batchSize` | `number` | `50` | Flush after this many spans accumulate. |
| `flushInterval` | `number` | `3000` | Flush at least every N milliseconds. |
| `debug` | `boolean` | `false` | Emit verbose debug logs to stderr. |

## Graceful Shutdown

Always call `shutdown()` before your process exits to ensure buffered spans are delivered:

```typescript
process.on('SIGTERM', async () => {
  await otelmind.shutdown();
  process.exit(0);
});
```

## Advanced: Lower-Level API

```typescript
import { SpanQueue, Tracer } from '@otelmind/sdk';

const queue = new SpanQueue({
  endpoint: 'https://ingest.otelmind.io',
  apiKey: 'om_...',
  batchSize: 20,
  flushInterval: 1000,
  debug: true,
});

const tracer = new Tracer(queue, 'my-service');

const span = tracer.startSpan('custom-op', { kind: 'internal' });
tracer.endSpan(span, { output: 'done' });

await queue.flush();
await queue.shutdown();
```

## License

MIT
