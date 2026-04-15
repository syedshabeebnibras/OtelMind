"use client";

/**
 * Visual smoke test for <InteractionGraph />.
 *
 * Renders the component with eight mock datasets covering the edge cases
 * the production pages may hit (empty / one-agent / many-messages / long
 * names / every protocol's message_type). Not a unit test — the dashboard
 * has no test framework — but it's the minimum bar the spec requested:
 * proof each edge case renders without crashing.
 *
 * Visit /dev/interaction-graph in dev mode to eyeball every sample.
 */

import type { ComponentProps } from "react";

import { InteractionGraph } from "@/components/InteractionGraph";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";

type Sample = {
  title: string;
  description: string;
  props: ComponentProps<typeof InteractionGraph>;
};

function _msg(
  sender_id: string,
  role: string,
  content: string,
  round_number = 1,
  message_type = "broadcast",
  tokens = 200,
): NonNullable<ComponentProps<typeof InteractionGraph>["messages"]>[number] {
  return {
    sender_id,
    sender_role: role,
    recipient_id: null,
    content,
    round_number,
    message_type,
    token_usage: { total_tokens: tokens },
  };
}

const samples: Sample[] = [
  {
    title: "Empty",
    description: "No messages — the component should render a friendly placeholder.",
    props: { messages: [], perAgentStats: {} },
  },
  {
    title: "Single agent",
    description: "One lifeline only. No cross-agent arrows — broadcast resolves to nothing.",
    props: {
      messages: [_msg("solo-0", "coder", "Just me here.", 1)],
      perAgentStats: {
        "solo-0": {
          messages_sent: 1,
          tokens_used: 200,
          corrections_made: 0,
          corrections_received: 0,
          contribution_ratio: 1.0,
        },
      },
    },
  },
  {
    title: "Round-robin, 2 agents, 2 rounds",
    description: "The canonical happy path — should show a round separator.",
    props: {
      messages: [
        _msg("coder-0", "coder", "Proposed implementation", 1, "broadcast", 420),
        _msg("reviewer-1", "reviewer", "Found two edge cases", 1, "broadcast", 380),
        _msg("coder-0", "coder", "Revised implementation", 2, "broadcast", 510),
        _msg("reviewer-1", "reviewer", "LGTM now", 2, "broadcast", 190),
      ],
      perAgentStats: {
        "coder-0": {
          messages_sent: 2,
          tokens_used: 930,
          corrections_made: 0,
          corrections_received: 1,
          contribution_ratio: 0.62,
        },
        "reviewer-1": {
          messages_sent: 2,
          tokens_used: 570,
          corrections_made: 1,
          corrections_received: 0,
          contribution_ratio: 0.38,
        },
      },
    },
  },
  {
    title: "Debate with VERDICT",
    description: "Three agents: two debaters + a judge. Judge's correction colours its arrows red.",
    props: {
      messages: [
        _msg("pro-0", "pro", "Argument FOR", 1, "debate", 300),
        _msg("con-0", "con", "Argument AGAINST", 1, "debate", 280),
        _msg("judge-0", "judge", "VERDICT: pro wins on merit", 1, "debate", 150),
      ],
      perAgentStats: {
        "judge-0": {
          messages_sent: 1,
          tokens_used: 150,
          corrections_made: 1,
          corrections_received: 0,
          contribution_ratio: 0.21,
        },
      },
    },
  },
  {
    title: "Blackboard",
    description: "JSON updates, not conversation. message_type=blackboard_write.",
    props: {
      messages: [
        _msg("writer-0", "writer", '{"section_1": "intro draft"}', 1, "blackboard_write", 220),
        _msg("writer-1", "writer", '{"section_2": "body draft"}', 1, "blackboard_write", 240),
        _msg("writer-0", "writer", "{}", 2, "blackboard_write", 60),
        _msg("writer-1", "writer", "{}", 2, "blackboard_write", 60),
      ],
    },
  },
  {
    title: "Delegation",
    description: "Lead → specialists → lead summary. Mixed message_type tags.",
    props: {
      messages: [
        _msg("planner-0", "planner", 'Plan: [{"agent":"coder","task":"impl"}]', 1, "delegation_plan", 400),
        _msg("coder-1", "coder", "Implementation complete", 1, "delegation_report", 800),
        _msg("reviewer-2", "reviewer", "Review complete", 1, "delegation_report", 500),
        _msg("planner-0", "planner", "DONE: consolidated answer", 1, "delegation_summary", 350),
      ],
      perAgentStats: {
        "planner-0": {
          messages_sent: 2,
          tokens_used: 750,
          corrections_made: 0,
          corrections_received: 0,
          contribution_ratio: 0.37,
        },
        "coder-1": {
          messages_sent: 1,
          tokens_used: 800,
          corrections_made: 0,
          corrections_received: 0,
          contribution_ratio: 0.4,
        },
        "reviewer-2": {
          messages_sent: 1,
          tokens_used: 500,
          corrections_made: 0,
          corrections_received: 0,
          contribution_ratio: 0.23,
        },
      },
    },
  },
  {
    title: "Long agent names",
    description: "Verify name truncation in the header boxes doesn't blow the layout.",
    props: {
      messages: [
        _msg("extremely-verbose-agent-name-that-should-fit", "a-very-long-role-name-indeed", "…", 1),
        _msg("another-long-id-0123456789abcdef", "another-long-role", "…", 1),
      ],
    },
  },
  {
    title: "100 messages, 5 rounds",
    description: "Stress test — the SVG should scale without layout thrash.",
    props: {
      messages: Array.from({ length: 100 }, (_, i) =>
        _msg(
          `agent-${i % 5}`,
          `role-${i % 5}`,
          `message body ${i}`,
          Math.floor(i / 20) + 1,
          "broadcast",
          100,
        ),
      ),
    },
  },
];

export default function InteractionGraphSamplesPage() {
  return (
    <div className="p-6 space-y-6 max-w-6xl mx-auto">
      <div>
        <h1 className="text-xl font-semibold text-slate-100">
          InteractionGraph — edge-case samples
        </h1>
        <p className="mt-1 text-sm text-slate-400">
          Visual smoke test. Every card below must render without a client-side
          exception. If any card shows the Next.js error overlay, the component
          regressed on that case.
        </p>
      </div>
      {samples.map((s) => (
        <Card key={s.title}>
          <CardHeader>
            <CardTitle className="text-base">{s.title}</CardTitle>
            <CardDescription>{s.description}</CardDescription>
          </CardHeader>
          <CardContent>
            <InteractionGraph {...s.props} />
          </CardContent>
        </Card>
      ))}
    </div>
  );
}
