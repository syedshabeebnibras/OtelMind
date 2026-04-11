import type { Tracer } from "../tracer.js";
import type { Span } from "../types.js";

// ---------------------------------------------------------------------------
// Minimal structural types for the openai v4 SDK surface we patch.
// Using structural typing keeps us decoupled from the peer dep at compile time.
// ---------------------------------------------------------------------------

type ChatCompletionMessageParam = {
  role: string;
  content: string | null;
};

type ChatCompletionChunk = {
  choices: Array<{
    delta: { content?: string | null };
    finish_reason?: string | null;
  }>;
  usage?: {
    prompt_tokens?: number;
    completion_tokens?: number;
  } | null;
};

type ChatCompletion = {
  id: string;
  model: string;
  choices: Array<{
    message: { role: string; content: string | null };
    finish_reason?: string | null;
  }>;
  usage?: {
    prompt_tokens?: number;
    completion_tokens?: number;
  } | null;
};

type ChatCompletionCreateParamsNonStreaming = {
  model: string;
  messages: ChatCompletionMessageParam[];
  stream?: false;
  [key: string]: unknown;
};

type ChatCompletionCreateParamsStreaming = {
  model: string;
  messages: ChatCompletionMessageParam[];
  stream: true;
  [key: string]: unknown;
};

type ChatCompletionCreateParams =
  | ChatCompletionCreateParamsNonStreaming
  | ChatCompletionCreateParamsStreaming;

interface AsyncIterableStream<T> extends AsyncIterable<T> {
  [Symbol.asyncIterator](): AsyncIterator<T>;
}

type OriginalCreate = {
  (params: ChatCompletionCreateParamsStreaming): Promise<AsyncIterableStream<ChatCompletionChunk>>;
  (params: ChatCompletionCreateParamsNonStreaming): Promise<ChatCompletion>;
  (params: ChatCompletionCreateParams): Promise<ChatCompletion | AsyncIterableStream<ChatCompletionChunk>>;
};

interface PatchableOpenAI {
  chat: {
    completions: {
      create: OriginalCreate;
    };
  };
}

// ---------------------------------------------------------------------------

export function instrumentOpenAI<T extends PatchableOpenAI>(client: T, tracer: Tracer): T {
  const original = client.chat.completions.create.bind(client.chat.completions) as OriginalCreate;

  // We need to handle both streaming and non-streaming overloads from one patched function.
  async function patched(params: ChatCompletionCreateParams): Promise<ChatCompletion | AsyncIterableStream<ChatCompletionChunk>> {
    const span: Span = tracer.startSpan(`openai.chat.completions.create`, {
      kind: "client",
      input: params.messages,
      attributes: {
        "llm.vendor": "openai",
        "llm.model": params.model,
        "llm.request.type": params.stream === true ? "streaming" : "unary",
      },
    });

    if (params.stream === true) {
      // Streaming path — collect chunks, wrap the async iterator.
      let streamResult: AsyncIterableStream<ChatCompletionChunk>;

      try {
        streamResult = await (original as (p: ChatCompletionCreateParamsStreaming) => Promise<AsyncIterableStream<ChatCompletionChunk>>)(params as ChatCompletionCreateParamsStreaming);
      } catch (err) {
        tracer.endSpan(span, { error: err instanceof Error ? err : String(err) });
        throw err;
      }

      return wrapStream(streamResult, span, tracer);
    }

    // Non-streaming path.
    try {
      const result = await (original as (p: ChatCompletionCreateParamsNonStreaming) => Promise<ChatCompletion>)(params as ChatCompletionCreateParamsNonStreaming);

      tracer.endSpan(span, {
        output: result.choices.map((c) => c.message),
        tokens: {
          prompt: result.usage?.prompt_tokens,
          completion: result.usage?.completion_tokens,
        },
        attributes: {
          "llm.model": result.model,
          "llm.response.finish_reason": result.choices[0]?.finish_reason ?? undefined,
        },
      });

      return result;
    } catch (err) {
      tracer.endSpan(span, { error: err instanceof Error ? err : String(err) });
      throw err;
    }
  }

  // Overwrite the method in place and preserve any extra properties.
  (client.chat.completions as { create: unknown }).create = patched as unknown as OriginalCreate;

  return client;
}

function wrapStream(
  stream: AsyncIterableStream<ChatCompletionChunk>,
  span: Span,
  tracer: Tracer
): AsyncIterableStream<ChatCompletionChunk> {
  const collectedChunks: ChatCompletionChunk[] = [];

  async function* generator(): AsyncGenerator<ChatCompletionChunk> {
    try {
      for await (const chunk of stream) {
        collectedChunks.push(chunk);
        yield chunk;
      }

      // Stream completed — finalise the span.
      const contentParts: string[] = [];
      let promptTokens: number | undefined;
      let completionTokens: number | undefined;
      let model: string | undefined;
      let finishReason: string | undefined;

      for (const chunk of collectedChunks) {
        for (const choice of chunk.choices) {
          if (choice.delta.content) contentParts.push(choice.delta.content);
          if (choice.finish_reason) finishReason = choice.finish_reason;
        }
        if (chunk.usage) {
          promptTokens = chunk.usage.prompt_tokens ?? promptTokens;
          completionTokens = chunk.usage.completion_tokens ?? completionTokens;
        }
      }

      // Model comes from the first non-empty chunk (openai includes it in every chunk).
      const firstChunk = collectedChunks[0] as (ChatCompletionChunk & { model?: string }) | undefined;
      model = firstChunk?.model;

      tracer.endSpan(span, {
        output: contentParts.join(""),
        tokens: { prompt: promptTokens, completion: completionTokens },
        attributes: {
          ...(model !== undefined ? { "llm.model": model } : {}),
          ...(finishReason !== undefined ? { "llm.response.finish_reason": finishReason } : {}),
          "llm.request.type": "streaming",
        },
      });
    } catch (err) {
      tracer.endSpan(span, { error: err instanceof Error ? err : String(err) });
      throw err;
    }
  }

  return {
    [Symbol.asyncIterator]: generator,
  };
}
