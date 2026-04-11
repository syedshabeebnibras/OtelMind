"use client";

import { useMemo, useState } from "react";
import {
  Bell,
  Plus,
  Trash2,
  RefreshCw,
  Slack,
  Mail,
  Webhook,
  CheckCircle2,
  XCircle,
} from "lucide-react";

import { useAlertChannels, useAlertRules } from "@/hooks/use-traces";
import { api, type AlertChannel, type AlertRule } from "@/lib/api";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { Switch } from "@/components/ui/switch";
import { Badge } from "@/components/ui/badge";
import { Skeleton } from "@/components/ui/skeleton";
import { formatDateShort } from "@/lib/utils";

const FAILURE_TYPES = [
  { value: "*", label: "Any failure type" },
  { value: "hallucination", label: "Hallucination" },
  { value: "tool_timeout", label: "Tool timeout" },
  { value: "infinite_loop", label: "Infinite loop" },
  { value: "context_overflow", label: "Context overflow" },
  { value: "semantic_drift", label: "Semantic drift" },
  { value: "prompt_injection", label: "Prompt injection" },
  { value: "cost_spike", label: "Cost spike" },
  { value: "eval_regression", label: "Eval regression" },
];

const CHANNEL_TYPES = [
  { value: "slack", label: "Slack", icon: Slack },
  { value: "pagerduty", label: "PagerDuty", icon: Bell },
  { value: "email", label: "Email", icon: Mail },
  { value: "webhook", label: "Webhook", icon: Webhook },
] as const;

type ChannelType = (typeof CHANNEL_TYPES)[number]["value"];

const CHANNEL_ICON: Record<string, React.ComponentType<{ className?: string }>> = {
  slack: Slack,
  pagerduty: Bell,
  email: Mail,
  webhook: Webhook,
};

/**
 * Config schema per channel type — one input field each. The backend stores
 * the dict verbatim; the alert router reads whatever keys each channel type
 * expects (slack → webhook_url, pagerduty → routing_key, email → to, webhook
 * → url).
 */
const CHANNEL_CONFIG_FIELD: Record<
  ChannelType,
  { key: string; label: string; placeholder: string; type?: "password" }
> = {
  slack: {
    key: "webhook_url",
    label: "Slack webhook URL",
    placeholder: "https://hooks.slack.com/services/T.../B.../...",
    type: "password",
  },
  pagerduty: {
    key: "routing_key",
    label: "PagerDuty routing key",
    placeholder: "R01ABCDEF2GH3IJKLM4NOP5QRSTU",
    type: "password",
  },
  email: {
    key: "to",
    label: "Recipient email",
    placeholder: "oncall@example.com",
  },
  webhook: {
    key: "url",
    label: "Webhook URL",
    placeholder: "https://ops.example.com/incoming",
  },
};

export default function AlertsPage() {
  const {
    rules,
    isLoading: rulesLoading,
    isError: rulesError,
    mutate: refreshRules,
  } = useAlertRules();
  const {
    channels,
    isLoading: channelsLoading,
    isError: channelsError,
    mutate: refreshChannels,
  } = useAlertChannels();

  // ── New-rule form state ────────────────────────────────────────────
  const [showCreateRule, setShowCreateRule] = useState(false);
  const [newFailureType, setNewFailureType] = useState("*");
  const [newRuleChannelId, setNewRuleChannelId] = useState<string>("");
  const [newThreshold, setNewThreshold] = useState(0.7);

  // ── New-channel form state ─────────────────────────────────────────
  const [showCreateChannel, setShowCreateChannel] = useState(false);
  const [newChannelType, setNewChannelType] = useState<ChannelType>("slack");
  const [newChannelName, setNewChannelName] = useState("");
  const [newChannelConfigValue, setNewChannelConfigValue] = useState("");

  const [busyId, setBusyId] = useState<string | null>(null);

  const refresh = () => {
    refreshRules();
    refreshChannels();
  };

  // ── Rule handlers ──────────────────────────────────────────────────
  const handleCreateRule = async () => {
    // Backend accepts channel *type* names for now. If a real channel is
    // selected, pass its type; otherwise default to slack.
    const selected = channels.find((c) => c.id === newRuleChannelId);
    const channelType = selected?.channel_type ?? "slack";
    await api.alerts.create({
      failure_type: newFailureType,
      threshold: newThreshold,
      channels: [channelType],
      enabled: true,
    });
    setShowCreateRule(false);
    setNewFailureType("*");
    setNewRuleChannelId("");
    setNewThreshold(0.7);
    refreshRules();
  };

  const handleToggleRule = async (rule: AlertRule) => {
    setBusyId(rule.id);
    try {
      await api.alerts.update(rule.id, { enabled: !rule.enabled });
      refreshRules();
    } finally {
      setBusyId(null);
    }
  };

  const handleDeleteRule = async (rule: AlertRule) => {
    if (!confirm(`Delete rule for ${rule.failure_type}?`)) return;
    setBusyId(rule.id);
    try {
      await api.alerts.delete(rule.id);
      refreshRules();
    } finally {
      setBusyId(null);
    }
  };

  // ── Channel handlers ───────────────────────────────────────────────
  const handleCreateChannel = async () => {
    const field = CHANNEL_CONFIG_FIELD[newChannelType];
    if (!newChannelName.trim() || !newChannelConfigValue.trim()) return;
    await api.alerts.channels.create({
      name: newChannelName.trim(),
      channel_type: newChannelType,
      config: { [field.key]: newChannelConfigValue.trim() },
    });
    setShowCreateChannel(false);
    setNewChannelName("");
    setNewChannelConfigValue("");
    setNewChannelType("slack");
    refreshChannels();
  };

  const handleToggleChannel = async (channel: AlertChannel) => {
    setBusyId(channel.id);
    try {
      await api.alerts.channels.update(channel.id, {
        is_active: !channel.is_active,
      });
      refreshChannels();
    } finally {
      setBusyId(null);
    }
  };

  const handleDeleteChannel = async (channel: AlertChannel) => {
    if (!confirm(`Delete channel "${channel.name}"?`)) return;
    setBusyId(channel.id);
    try {
      await api.alerts.channels.delete(channel.id);
      refreshChannels();
    } finally {
      setBusyId(null);
    }
  };

  // For the rule form's channel picker — only show active channels.
  const activeChannels = useMemo(
    () => channels.filter((c) => c.is_active),
    [channels],
  );

  return (
    <div className="p-6 space-y-5">
      {/* Header */}
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-xl font-semibold text-slate-100">Alerts</h1>
          <p className="mt-0.5 text-sm text-slate-400">
            Channels receive notifications; rules decide which failures fire which channel.
          </p>
        </div>
        <Button
          variant="outline"
          size="sm"
          onClick={refresh}
          disabled={rulesLoading || channelsLoading}
          className="gap-2"
        >
          <RefreshCw
            className={`h-3.5 w-3.5 ${
              rulesLoading || channelsLoading ? "animate-spin" : ""
            }`}
          />
          Refresh
        </Button>
      </div>

      {/* ── Channels section ─────────────────────────────────── */}
      <Card>
        <CardHeader className="flex flex-row items-center justify-between space-y-0">
          <div>
            <CardTitle>Channels</CardTitle>
            <CardDescription>
              Destinations that alerts route to. Configure one per Slack workspace,
              PagerDuty service, inbox, or webhook endpoint.
            </CardDescription>
          </div>
          <Button
            size="sm"
            onClick={() => setShowCreateChannel((v) => !v)}
            className="gap-2"
          >
            <Plus className="h-3.5 w-3.5" />
            New channel
          </Button>
        </CardHeader>
        <CardContent className="space-y-4">
          {showCreateChannel && (
            <div className="rounded-lg border border-slate-800 bg-slate-950 p-4 space-y-4">
              <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
                <Field label="Channel type">
                  <Select
                    value={newChannelType}
                    onValueChange={(v) => setNewChannelType(v as ChannelType)}
                  >
                    <SelectTrigger>
                      <SelectValue />
                    </SelectTrigger>
                    <SelectContent>
                      {CHANNEL_TYPES.map((c) => (
                        <SelectItem key={c.value} value={c.value}>
                          {c.label}
                        </SelectItem>
                      ))}
                    </SelectContent>
                  </Select>
                </Field>
                <Field label="Display name">
                  <Input
                    placeholder="oncall, alerts-prod, finance@..."
                    value={newChannelName}
                    onChange={(e) => setNewChannelName(e.target.value)}
                  />
                </Field>
                <Field label={CHANNEL_CONFIG_FIELD[newChannelType].label}>
                  <Input
                    placeholder={CHANNEL_CONFIG_FIELD[newChannelType].placeholder}
                    type={CHANNEL_CONFIG_FIELD[newChannelType].type ?? "text"}
                    value={newChannelConfigValue}
                    onChange={(e) => setNewChannelConfigValue(e.target.value)}
                  />
                </Field>
              </div>
              <div className="flex items-center justify-end gap-2">
                <Button
                  variant="ghost"
                  size="sm"
                  onClick={() => setShowCreateChannel(false)}
                >
                  Cancel
                </Button>
                <Button
                  size="sm"
                  onClick={handleCreateChannel}
                  disabled={!newChannelName.trim() || !newChannelConfigValue.trim()}
                >
                  Save channel
                </Button>
              </div>
            </div>
          )}

          {channelsError ? (
            <div className="py-6 text-center text-sm text-slate-500">
              Failed to load channels
            </div>
          ) : channelsLoading ? (
            <div className="space-y-2">
              {Array.from({ length: 2 }).map((_, i) => (
                <Skeleton key={i} className="h-12 w-full" />
              ))}
            </div>
          ) : channels.length === 0 ? (
            <div className="py-8 text-center">
              <Bell className="mx-auto h-7 w-7 text-slate-600" />
              <p className="mt-3 text-sm text-slate-400">
                No channels configured yet
              </p>
              <p className="text-xs text-slate-500">
                Add one above, then create a rule that routes to it.
              </p>
            </div>
          ) : (
            <div className="space-y-2">
              {channels.map((channel) => {
                const Icon = CHANNEL_ICON[channel.channel_type] ?? Bell;
                const field = CHANNEL_CONFIG_FIELD[channel.channel_type as ChannelType];
                const configValue = field
                  ? (channel.config?.[field.key] as string | undefined)
                  : undefined;
                const redacted =
                  field?.type === "password" && configValue
                    ? `${configValue.slice(0, 8)}…${configValue.slice(-4)}`
                    : configValue;
                return (
                  <div
                    key={channel.id}
                    className="flex items-center justify-between rounded-lg border border-slate-800 bg-slate-950 p-3"
                  >
                    <div className="flex items-center gap-3 min-w-0">
                      <div className="flex h-9 w-9 items-center justify-center rounded-md bg-slate-800/60 border border-slate-700 shrink-0">
                        <Icon className="h-4 w-4 text-slate-300" />
                      </div>
                      <div className="min-w-0">
                        <div className="flex items-center gap-2">
                          <p className="text-sm font-medium text-slate-100 truncate">
                            {channel.name}
                          </p>
                          <Badge variant="outline" className="text-[10px] uppercase">
                            {channel.channel_type}
                          </Badge>
                          {channel.is_active ? (
                            <Badge variant="success" className="gap-1 text-[10px]">
                              <CheckCircle2 className="h-3 w-3" />
                              active
                            </Badge>
                          ) : (
                            <Badge variant="secondary" className="gap-1 text-[10px]">
                              <XCircle className="h-3 w-3" />
                              paused
                            </Badge>
                          )}
                        </div>
                        <p className="mt-0.5 font-mono text-[11px] text-slate-500 truncate">
                          {redacted ?? "—"}
                        </p>
                      </div>
                    </div>
                    <div className="flex items-center gap-2 shrink-0">
                      <Switch
                        checked={channel.is_active}
                        onCheckedChange={() => handleToggleChannel(channel)}
                        disabled={busyId === channel.id}
                      />
                      <Button
                        variant="ghost"
                        size="sm"
                        onClick={() => handleDeleteChannel(channel)}
                        disabled={busyId === channel.id}
                        className="text-red-400 hover:text-red-300 hover:bg-red-500/10"
                      >
                        <Trash2 className="h-4 w-4" />
                      </Button>
                    </div>
                  </div>
                );
              })}
            </div>
          )}
        </CardContent>
      </Card>

      {/* ── Rules section ────────────────────────────────────── */}
      <Card>
        <CardHeader className="flex flex-row items-center justify-between space-y-0">
          <div>
            <CardTitle>Routing rules</CardTitle>
            <CardDescription>
              A failure at or above the confidence threshold fires the selected channel.
            </CardDescription>
          </div>
          <Button
            size="sm"
            onClick={() => setShowCreateRule((v) => !v)}
            className="gap-2"
          >
            <Plus className="h-3.5 w-3.5" />
            New rule
          </Button>
        </CardHeader>
        <CardContent className="space-y-4">
          {showCreateRule && (
            <div className="rounded-lg border border-slate-800 bg-slate-950 p-4 space-y-4">
              <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
                <Field label="Failure type">
                  <Select value={newFailureType} onValueChange={setNewFailureType}>
                    <SelectTrigger>
                      <SelectValue />
                    </SelectTrigger>
                    <SelectContent>
                      {FAILURE_TYPES.map((f) => (
                        <SelectItem key={f.value} value={f.value}>
                          {f.label}
                        </SelectItem>
                      ))}
                    </SelectContent>
                  </Select>
                </Field>

                <Field label="Channel">
                  <Select
                    value={newRuleChannelId}
                    onValueChange={setNewRuleChannelId}
                  >
                    <SelectTrigger>
                      <SelectValue
                        placeholder={
                          activeChannels.length === 0
                            ? "Add a channel first"
                            : "Pick a channel"
                        }
                      />
                    </SelectTrigger>
                    <SelectContent>
                      {activeChannels.map((c) => (
                        <SelectItem key={c.id} value={c.id}>
                          {c.name} ({c.channel_type})
                        </SelectItem>
                      ))}
                    </SelectContent>
                  </Select>
                </Field>

                <Field
                  label={`Confidence threshold · ${(newThreshold * 100).toFixed(
                    0,
                  )}%`}
                >
                  <Input
                    type="range"
                    min={0}
                    max={1}
                    step={0.05}
                    value={newThreshold}
                    onChange={(e) => setNewThreshold(parseFloat(e.target.value))}
                  />
                </Field>
              </div>
              <div className="flex items-center justify-end gap-2">
                <Button
                  variant="ghost"
                  size="sm"
                  onClick={() => setShowCreateRule(false)}
                >
                  Cancel
                </Button>
                <Button
                  size="sm"
                  onClick={handleCreateRule}
                  disabled={!newRuleChannelId && activeChannels.length > 0}
                >
                  Create rule
                </Button>
              </div>
            </div>
          )}

          {rulesError ? (
            <div className="py-6 text-center text-sm text-slate-500">
              Failed to load alert rules
            </div>
          ) : rulesLoading ? (
            <div className="space-y-2">
              {Array.from({ length: 2 }).map((_, i) => (
                <Skeleton key={i} className="h-12 w-full" />
              ))}
            </div>
          ) : rules.length === 0 ? (
            <div className="py-8 text-center">
              <Bell className="mx-auto h-7 w-7 text-slate-600" />
              <p className="mt-3 text-sm text-slate-400">
                No rules configured yet
              </p>
              <p className="text-xs text-slate-500">
                Create a rule above to get notified when failures exceed a threshold.
              </p>
            </div>
          ) : (
            <div className="space-y-2">
              {rules.map((rule) => (
                <div
                  key={rule.id}
                  className="flex items-center justify-between rounded-lg border border-slate-800 bg-slate-950 p-3"
                >
                  <div className="flex items-center gap-3">
                    <div className="flex h-9 w-9 items-center justify-center rounded-md bg-blue-500/10 border border-blue-500/20">
                      <Bell className="h-4 w-4 text-blue-400" />
                    </div>
                    <div>
                      <div className="flex items-center gap-2">
                        <p className="text-sm font-medium text-slate-100">
                          {rule.failure_type === "*" ? "Any failure" : rule.failure_type}
                        </p>
                        <Badge variant="outline" className="font-mono text-[10px]">
                          ≥ {(rule.threshold * 100).toFixed(0)}%
                        </Badge>
                        {!rule.enabled && (
                          <Badge variant="secondary" className="text-[10px]">
                            paused
                          </Badge>
                        )}
                      </div>
                      <p className="mt-0.5 text-xs text-slate-500">
                        Routes to {rule.channels.join(", ") || "—"} · Created{" "}
                        {formatDateShort(rule.created_at)}
                      </p>
                    </div>
                  </div>
                  <div className="flex items-center gap-2">
                    <Switch
                      checked={rule.enabled}
                      onCheckedChange={() => handleToggleRule(rule)}
                      disabled={busyId === rule.id}
                    />
                    <Button
                      variant="ghost"
                      size="sm"
                      onClick={() => handleDeleteRule(rule)}
                      disabled={busyId === rule.id}
                      className="text-red-400 hover:text-red-300 hover:bg-red-500/10"
                    >
                      <Trash2 className="h-4 w-4" />
                    </Button>
                  </div>
                </div>
              ))}
            </div>
          )}
        </CardContent>
      </Card>
    </div>
  );
}

function Field({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div className="space-y-1.5">
      <label className="text-xs uppercase tracking-wider text-slate-500 font-semibold">
        {label}
      </label>
      {children}
    </div>
  );
}
