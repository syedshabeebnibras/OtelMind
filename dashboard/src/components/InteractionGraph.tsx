"use client";

/**
 * Sequence-diagram-style visualization of multi-agent communication.
 *
 * - Vertical lifelines for each unique agent role
 * - Horizontal arrows for each message (broadcast = arrow into every other lifeline)
 * - Correction messages drawn in red/dashed when the sender's
 *   `corrections_made` is positive in the metrics
 * - Round separators as thin horizontal rules
 * - Token totals per agent at the bottom of each lifeline
 *
 * Pure inline SVG, viewBox-based so it scales to its container width.
 * No new dependencies.
 */

interface Message {
  sender_id?: string;
  sender_role?: string;
  recipient_id?: string | null;
  round_number?: number;
  message_type?: string;
  content?: string;
  token_usage?: { total_tokens?: number } | null;
}

interface AgentStats {
  messages_sent: number;
  tokens_used: number;
  corrections_made: number;
  corrections_received: number;
  contribution_ratio: number;
}

interface Props {
  messages: Message[];
  perAgentStats?: Record<string, AgentStats>;
}

export function InteractionGraph({ messages, perAgentStats = {} }: Props) {
  if (!messages || messages.length === 0) {
    return (
      <div className="flex h-32 items-center justify-center text-sm text-slate-500">
        No messages to visualise.
      </div>
    );
  }

  // Unique agent ids in first-seen order (keeps lifelines stable across rounds)
  const agentIds: string[] = [];
  const idToRole = new Map<string, string>();
  for (const m of messages) {
    const id = m.sender_id ?? "unknown";
    if (!agentIds.includes(id)) {
      agentIds.push(id);
      idToRole.set(id, m.sender_role ?? id);
    }
  }

  // Geometry — viewBox-based so the SVG scales to the container
  const COL_W = 200;
  const TOP = 60;
  const ROW_H = 60;
  const BOTTOM_PAD = 70;
  const GUTTER = 30;

  const width = Math.max(GUTTER * 2 + agentIds.length * COL_W, 600);
  const height = TOP + messages.length * ROW_H + BOTTOM_PAD;

  const colX = (id: string) => GUTTER + agentIds.indexOf(id) * COL_W + COL_W / 2;

  // Round separator rows — track the y between rounds
  const roundSeparators: Array<{ y: number; round: number }> = [];
  let prevRound: number | null = null;
  messages.forEach((m, i) => {
    const r = m.round_number ?? 1;
    if (prevRound !== null && r !== prevRound) {
      roundSeparators.push({ y: TOP + i * ROW_H - 8, round: r });
    }
    prevRound = r;
  });

  // Aggregate token totals per agent (uses message-level token_usage so the
  // component is self-contained and doesn't depend on perAgentStats arriving)
  const tokensByAgent: Record<string, number> = {};
  for (const m of messages) {
    const id = m.sender_id ?? "unknown";
    tokensByAgent[id] = (tokensByAgent[id] ?? 0) + (m.token_usage?.total_tokens ?? 0);
  }

  // Did this sender ever issue a correction at all? Drives line styling.
  const senderMakesCorrections = (id: string): boolean =>
    (perAgentStats[id]?.corrections_made ?? 0) > 0;

  // Tailwind color palette per role for legibility — fall back to slate
  const colorFor = (role: string): string => {
    const palette = ["#60a5fa", "#34d399", "#f472b6", "#fbbf24", "#a78bfa", "#fb7185"];
    let h = 0;
    for (const c of role) h = (h * 31 + c.charCodeAt(0)) >>> 0;
    return palette[h % palette.length];
  };

  return (
    <svg
      viewBox={`0 0 ${width} ${height}`}
      className="w-full"
      role="img"
      aria-label="Multi-agent interaction sequence diagram"
    >
      {/* Lifelines */}
      {agentIds.map((id) => (
        <g key={id}>
          <line
            x1={colX(id)}
            x2={colX(id)}
            y1={TOP - 10}
            y2={height - BOTTOM_PAD + 5}
            stroke="#334155"
            strokeWidth={1}
            strokeDasharray="4 4"
          />
          {/* Agent header */}
          <g>
            <rect
              x={colX(id) - 70}
              y={10}
              width={140}
              height={36}
              rx={6}
              fill="#0f172a"
              stroke={colorFor(idToRole.get(id) ?? id)}
              strokeWidth={1.5}
            />
            <text
              x={colX(id)}
              y={26}
              textAnchor="middle"
              fontSize={11}
              fontWeight="600"
              fill="#e2e8f0"
            >
              {idToRole.get(id)}
            </text>
            <text x={colX(id)} y={40} textAnchor="middle" fontSize={9} fill="#94a3b8" fontFamily="monospace">
              {id}
            </text>
          </g>
          {/* Token total */}
          <text
            x={colX(id)}
            y={height - BOTTOM_PAD + 30}
            textAnchor="middle"
            fontSize={10}
            fill="#94a3b8"
          >
            {tokensByAgent[id]?.toLocaleString() ?? 0} tokens
          </text>
        </g>
      ))}

      {/* Round separators */}
      {roundSeparators.map((sep, i) => (
        <g key={`sep-${i}`}>
          <line
            x1={GUTTER}
            x2={width - GUTTER}
            y1={sep.y}
            y2={sep.y}
            stroke="#1e293b"
            strokeWidth={1}
          />
          <text x={width - GUTTER - 6} y={sep.y - 4} textAnchor="end" fontSize={9} fill="#475569">
            round {sep.round} →
          </text>
        </g>
      ))}

      {/* Message arrows */}
      {messages.map((m, i) => {
        const y = TOP + i * ROW_H + 20;
        const senderId = m.sender_id ?? "unknown";
        const isCorrection = senderMakesCorrections(senderId);
        const stroke = isCorrection ? "#fb7185" : colorFor(idToRole.get(senderId) ?? senderId);

        // Recipient: explicit recipient_id, else broadcast = draw to every other lifeline
        const recipients =
          m.recipient_id && agentIds.includes(m.recipient_id)
            ? [m.recipient_id]
            : agentIds.filter((id) => id !== senderId);

        return (
          <g key={`msg-${i}`}>
            {recipients.map((rid) => {
              const x1 = colX(senderId);
              const x2 = colX(rid);
              const arrowSize = 5;
              const direction = x2 > x1 ? 1 : -1;
              const tipX = x2 - direction * arrowSize;
              return (
                <g key={`${i}-${rid}`}>
                  <line
                    x1={x1}
                    y1={y}
                    x2={x2}
                    y2={y}
                    stroke={stroke}
                    strokeWidth={1.5}
                    strokeDasharray={isCorrection ? "5 3" : undefined}
                  />
                  {/* Arrow head */}
                  <polygon
                    points={`${x2},${y} ${tipX},${y - arrowSize} ${tipX},${y + arrowSize}`}
                    fill={stroke}
                  />
                </g>
              );
            })}
            {/* Round + type tag near sender */}
            <text x={colX(senderId) + 8} y={y - 6} fontSize={9} fill="#64748b">
              r{m.round_number ?? "?"} · {m.message_type ?? "msg"}
            </text>
          </g>
        );
      })}

      {/* Legend */}
      <g transform={`translate(${GUTTER}, ${height - 22})`}>
        <line x1={0} y1={0} x2={20} y2={0} stroke="#94a3b8" strokeWidth={1.5} />
        <text x={26} y={3} fontSize={9} fill="#94a3b8">
          message
        </text>
        <line x1={90} y1={0} x2={110} y2={0} stroke="#fb7185" strokeWidth={1.5} strokeDasharray="5 3" />
        <text x={116} y={3} fontSize={9} fill="#fb7185">
          correction
        </text>
      </g>
    </svg>
  );
}
