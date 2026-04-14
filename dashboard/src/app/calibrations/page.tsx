"use client";

import Link from "next/link";
import { RefreshCw, Target } from "lucide-react";

import { useCalibrations } from "@/hooks/use-traces";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { formatDate } from "@/lib/utils";

export default function CalibrationsPage() {
  const { items, total, isLoading, isError, mutate } = useCalibrations(50);

  return (
    <div className="p-6 space-y-5">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-xl font-semibold text-slate-100">Judge calibrations</h1>
          <p className="mt-0.5 text-sm text-slate-400">
            Inter-rater agreement between the LLM judge and human-labeled gold sets
          </p>
        </div>
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
      </div>

      <Card>
        <CardHeader className="pb-3">
          <CardTitle className="flex items-center gap-2 text-base">
            <Target className="h-4 w-4 text-blue-400" />
            Recent calibrations ({total.toLocaleString()})
          </CardTitle>
          <CardDescription>
            Cohen&apos;s kappa &gt; 0.7 = strong agreement; bias near 0 = unbiased judge
          </CardDescription>
        </CardHeader>
        <CardContent>
          {isError ? (
            <p className="text-sm text-red-400">Failed to load calibrations.</p>
          ) : isLoading ? (
            <div className="space-y-2">
              {Array.from({ length: 5 }).map((_, i) => (
                <Skeleton key={i} className="h-10 w-full" />
              ))}
            </div>
          ) : items.length === 0 ? (
            <p className="text-sm text-slate-500 py-8 text-center">
              No calibrations run yet. POST /api/v1/calibrations to score human labels.
            </p>
          ) : (
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead>Judge model</TableHead>
                  <TableHead className="text-right">Cohen&apos;s κ</TableHead>
                  <TableHead className="text-right">Agreement</TableHead>
                  <TableHead className="text-right">Bias</TableHead>
                  <TableHead className="text-right">Cases</TableHead>
                  <TableHead>Created</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {items.map((c) => (
                  <TableRow key={c.id} className="hover:bg-slate-900/50">
                    <TableCell>
                      <Link
                        href={`/calibrations/${c.id}`}
                        className="text-blue-400 hover:underline font-mono text-xs"
                      >
                        {c.judge_model}
                      </Link>
                    </TableCell>
                    <TableCell className="text-right text-sm">
                      {c.cohens_kappa !== null ? c.cohens_kappa.toFixed(3) : "–"}
                    </TableCell>
                    <TableCell className="text-right text-sm">
                      {c.agreement_rate !== null
                        ? `${(c.agreement_rate * 100).toFixed(1)}%`
                        : "–"}
                    </TableCell>
                    <TableCell className="text-right text-sm">
                      {c.bias !== null ? c.bias.toFixed(3) : "–"}
                    </TableCell>
                    <TableCell className="text-right text-sm">{c.case_count}</TableCell>
                    <TableCell className="text-xs text-slate-500">
                      {formatDate(c.created_at)}
                    </TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          )}
        </CardContent>
      </Card>
    </div>
  );
}
