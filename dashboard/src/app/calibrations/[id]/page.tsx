"use client";

import { ArrowLeft } from "lucide-react";
import Link from "next/link";

import { useCalibration } from "@/hooks/use-traces";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";

function MetricCard({ label, value, hint }: { label: string; value: string; hint?: string }) {
  return (
    <Card>
      <CardHeader className="pb-1">
        <CardDescription className="text-xs uppercase tracking-wide">{label}</CardDescription>
      </CardHeader>
      <CardContent>
        <p className="text-2xl font-semibold text-slate-100">{value}</p>
        {hint && <p className="mt-1 text-xs text-slate-500">{hint}</p>}
      </CardContent>
    </Card>
  );
}

export default function CalibrationDetailPage({
  params,
}: {
  params: { id: string };
}) {
  const { id } = params;
  const { calibration, isLoading, isError } = useCalibration(id);

  if (isLoading) {
    return (
      <div className="p-6 space-y-4">
        <Skeleton className="h-8 w-72" />
        <Skeleton className="h-32 w-full" />
        <Skeleton className="h-64 w-full" />
      </div>
    );
  }
  if (isError || !calibration) {
    return (
      <div className="p-6">
        <p className="text-sm text-red-400">Calibration not found.</p>
        <Link href="/calibrations" className="text-blue-400 underline text-sm mt-2 inline-block">
          ← Back to calibrations
        </Link>
      </div>
    );
  }

  const perDim = calibration.per_dimension ?? {};
  const curve = calibration.calibration_curve ?? [];

  return (
    <div className="p-6 space-y-5">
      <div>
        <Link
          href="/calibrations"
          className="inline-flex items-center gap-1 text-xs text-slate-400 hover:text-slate-200 mb-1"
        >
          <ArrowLeft className="h-3 w-3" />
          All calibrations
        </Link>
        <h1 className="text-xl font-semibold text-slate-100">Calibration #{calibration.id}</h1>
        <p className="mt-0.5 text-sm text-slate-400 font-mono">{calibration.judge_model}</p>
      </div>

      <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
        <MetricCard
          label="Cohen's κ"
          value={calibration.cohens_kappa !== null ? calibration.cohens_kappa.toFixed(3) : "–"}
          hint="κ > 0.7 = strong"
        />
        <MetricCard
          label="Agreement"
          value={
            calibration.agreement_rate !== null
              ? `${(calibration.agreement_rate * 100).toFixed(1)}%`
              : "–"
          }
        />
        <MetricCard
          label="Bias"
          value={calibration.bias !== null ? calibration.bias.toFixed(3) : "–"}
          hint="positive = judge is generous"
        />
        <MetricCard label="Cases" value={calibration.case_count.toString()} />
      </div>

      <Card>
        <CardHeader className="pb-3">
          <CardTitle className="text-base">Per-dimension breakdown</CardTitle>
        </CardHeader>
        <CardContent>
          {Object.keys(perDim).length === 0 ? (
            <p className="text-sm text-slate-500">No per-dimension data.</p>
          ) : (
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead>Dimension</TableHead>
                  <TableHead className="text-right">κ</TableHead>
                  <TableHead className="text-right">Agreement</TableHead>
                  <TableHead className="text-right">MAE</TableHead>
                  <TableHead className="text-right">Bias</TableHead>
                  <TableHead className="text-right">N</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {Object.entries(perDim).map(([dim, d]) => (
                  <TableRow key={dim}>
                    <TableCell className="text-sm">{dim}</TableCell>
                    <TableCell className="text-right text-sm">{d.cohens_kappa.toFixed(3)}</TableCell>
                    <TableCell className="text-right text-sm">
                      {(d.agreement_rate * 100).toFixed(1)}%
                    </TableCell>
                    <TableCell className="text-right text-sm">
                      {d.mean_absolute_error.toFixed(3)}
                    </TableCell>
                    <TableCell className="text-right text-sm">{d.bias.toFixed(3)}</TableCell>
                    <TableCell className="text-right text-sm">{d.n}</TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          )}
        </CardContent>
      </Card>

      <Card>
        <CardHeader className="pb-3">
          <CardTitle className="text-base">Calibration curve</CardTitle>
          <CardDescription>
            Predicted bucket → mean human score for that bucket (perfect calibration: predicted = actual)
          </CardDescription>
        </CardHeader>
        <CardContent>
          {curve.length === 0 ? (
            <p className="text-sm text-slate-500">No curve data.</p>
          ) : (
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead className="text-right">Bin</TableHead>
                  <TableHead className="text-right">Predicted</TableHead>
                  <TableHead className="text-right">Actual (human)</TableHead>
                  <TableHead className="text-right">Δ</TableHead>
                  <TableHead className="text-right">N</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {curve.map((row) => (
                  <TableRow key={row.bin}>
                    <TableCell className="text-right text-sm">{row.bin}</TableCell>
                    <TableCell className="text-right text-sm">{row.predicted.toFixed(3)}</TableCell>
                    <TableCell className="text-right text-sm">{row.actual.toFixed(3)}</TableCell>
                    <TableCell className="text-right text-sm">
                      {(row.predicted - row.actual).toFixed(3)}
                    </TableCell>
                    <TableCell className="text-right text-sm">{row.n}</TableCell>
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
