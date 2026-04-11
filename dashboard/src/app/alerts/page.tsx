"use client";

import { useState } from "react";
import { Bell, Plus, Trash2, RefreshCw, Slack, Mail, Webhook } from "lucide-react";

import { useAlertRules } from "@/hooks/use-traces";
import { api, type AlertRule } from "@/lib/api";
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
];

const CHANNEL_TYPES = [
  { value: "slack", label: "Slack", icon: Slack },
  { value: "pagerduty", label: "PagerDuty", icon: Bell },
  { value: "email", label: "Email", icon: Mail },
  { value: "webhook", label: "Webhook", icon: Webhook },
];

export default function AlertsPage() {
  const { rules, isLoading, isError, mutate } = useAlertRules();

  const [showCreate, setShowCreate] = useState(false);
  const [newFailureType, setNewFailureType] = useState("*");
  const [newChannelType, setNewChannelType] = useState("slack");
  const [newThreshold, setNewThreshold] = useState(0.7);
  const [busyId, setBusyId] = useState<string | null>(null);

  const handleCreate = async () => {
    await api.alerts.create({
      failure_type: newFailureType,
      threshold: newThreshold,
      channels: [newChannelType],
      enabled: true,
    });
    setShowCreate(false);
    setNewFailureType("*");
    setNewChannelType("slack");
    setNewThreshold(0.7);
    mutate();
  };

  const handleToggle = async (rule: AlertRule) => {
    setBusyId(rule.id);
    try {
      await api.alerts.update(rule.id, { enabled: !rule.enabled });
      mutate();
    } finally {
      setBusyId(null);
    }
  };

  const handleDelete = async (rule: AlertRule) => {
    if (!confirm(`Delete rule for ${rule.failure_type}?`)) return;
    setBusyId(rule.id);
    try {
      await api.alerts.delete(rule.id);
      mutate();
    } finally {
      setBusyId(null);
    }
  };

  return (
    <div className="p-6 space-y-5">
      {/* Header */}
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-xl font-semibold text-slate-100">Alerts</h1>
          <p className="mt-0.5 text-sm text-slate-400">
            Routing rules from failure classifications to notification channels
          </p>
        </div>
        <div className="flex items-center gap-2">
          <Button
            variant="outline"
            size="sm"
            onClick={() => mutate()}
            disabled={isLoading}
            className="gap-2"
          >
            <RefreshCw className={`h-3.5 w-3.5 ${isLoading ? "animate-spin" : ""}`} />
            Refresh
          </Button>
          <Button size="sm" onClick={() => setShowCreate((v) => !v)} className="gap-2">
            <Plus className="h-3.5 w-3.5" />
            New rule
          </Button>
        </div>
      </div>

      {/* Create card */}
      {showCreate && (
        <Card>
          <CardHeader>
            <CardTitle>New alert rule</CardTitle>
            <CardDescription>
              A failure classification at or above the confidence threshold triggers a
              notification on the selected channel.
            </CardDescription>
          </CardHeader>
          <CardContent className="space-y-4">
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
                <Select value={newChannelType} onValueChange={setNewChannelType}>
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

              <Field label={`Confidence threshold · ${(newThreshold * 100).toFixed(0)}%`}>
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
              <Button variant="ghost" size="sm" onClick={() => setShowCreate(false)}>
                Cancel
              </Button>
              <Button size="sm" onClick={handleCreate}>
                Create rule
              </Button>
            </div>
          </CardContent>
        </Card>
      )}

      {/* Rules list */}
      <div className="space-y-3">
        {isLoading ? (
          Array.from({ length: 3 }).map((_, i) => (
            <Card key={i}>
              <CardContent className="p-5">
                <Skeleton className="h-5 w-48" />
                <Skeleton className="mt-3 h-4 w-72" />
              </CardContent>
            </Card>
          ))
        ) : isError ? (
          <Card>
            <CardContent className="p-8 text-center text-sm text-slate-500">
              Failed to load alert rules
            </CardContent>
          </Card>
        ) : rules.length === 0 ? (
          <Card>
            <CardContent className="p-8 text-center">
              <Bell className="mx-auto h-8 w-8 text-slate-600" />
              <p className="mt-3 text-sm text-slate-400">No alert rules configured yet</p>
              <p className="text-xs text-slate-500">
                Create one to get notified when failures exceed a confidence threshold
              </p>
            </CardContent>
          </Card>
        ) : (
          rules.map((rule) => (
            <Card key={rule.id}>
              <CardContent className="flex items-center justify-between p-5">
                <div className="flex items-center gap-4">
                  <div className="flex h-10 w-10 items-center justify-center rounded-lg bg-blue-500/10 border border-blue-500/20">
                    <Bell className="h-5 w-5 text-blue-400" />
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
                    <p className="mt-1 text-xs text-slate-500">
                      Routes to {rule.channels.length} channel
                      {rule.channels.length === 1 ? "" : "s"} · Created{" "}
                      {formatDateShort(rule.created_at)}
                    </p>
                  </div>
                </div>

                <div className="flex items-center gap-3">
                  <Switch
                    checked={rule.enabled}
                    onCheckedChange={() => handleToggle(rule)}
                    disabled={busyId === rule.id}
                  />
                  <Button
                    variant="ghost"
                    size="sm"
                    onClick={() => handleDelete(rule)}
                    disabled={busyId === rule.id}
                    className="text-red-400 hover:text-red-300 hover:bg-red-500/10"
                  >
                    <Trash2 className="h-4 w-4" />
                  </Button>
                </div>
              </CardContent>
            </Card>
          ))
        )}
      </div>

      {/* Channel types explainer */}
      <Card>
        <CardHeader>
          <CardTitle>Supported channels</CardTitle>
          <CardDescription>
            OtelMind routes failures to any combination of the integrations below
          </CardDescription>
        </CardHeader>
        <CardContent>
          <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
            {CHANNEL_TYPES.map((c) => {
              const Icon = c.icon;
              return (
                <div
                  key={c.value}
                  className="flex items-center gap-3 rounded-lg border border-slate-800 bg-slate-950 p-3"
                >
                  <Icon className="h-4 w-4 text-slate-400" />
                  <span className="text-sm text-slate-300">{c.label}</span>
                </div>
              );
            })}
          </div>
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
