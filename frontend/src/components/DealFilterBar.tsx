"use client";

import { useMemo } from "react";

import type { Deal } from "@/lib/schemas";

export interface DealFilters {
  make: string;
  status: string;
  onlyAboveThreshold: boolean;
}

export const EMPTY_FILTERS: DealFilters = {
  make: "",
  status: "",
  onlyAboveThreshold: false,
};

interface Props {
  deals: readonly Deal[];
  filters: DealFilters;
  filteredCount: number;
  onChange: (next: DealFilters) => void;
}

/**
 * Filter bar above the deal list. Options are derived from the current deals
 * so a user can never select a filter that yields zero results just from the
 * filter alone — e.g., the Make dropdown only lists makes that actually appear
 * in the loaded set.
 */
export function DealFilterBar({ deals, filters, filteredCount, onChange }: Props) {
  const makes = useMemo(() => distinctSorted(deals.map((d) => d.make)), [deals]);
  const statuses = useMemo(() => distinctSorted(deals.map((d) => d.status)), [deals]);
  const hasAboveThreshold = useMemo(() => deals.some((d) => d.above_threshold), [deals]);

  const total = deals.length;
  const isFiltered = filters.make !== "" || filters.status !== "" || filters.onlyAboveThreshold;

  return (
    <div className="flex flex-wrap items-end gap-3 border-b border-stone-200/80 bg-stone-50/70 px-5 py-3 backdrop-blur-sm dark:border-stone-800 dark:bg-stone-900/40">
      <FilterSelect
        label="Make"
        value={filters.make}
        options={makes}
        allLabel="All makes"
        onChange={(v) => {
          onChange({ ...filters, make: v });
        }}
      />
      <FilterSelect
        label="Status"
        value={filters.status}
        options={statuses}
        allLabel="Any status"
        onChange={(v) => {
          onChange({ ...filters, status: v });
        }}
      />

      <label
        className={
          "flex items-center gap-2 pb-1 text-xs " +
          (hasAboveThreshold
            ? "text-stone-700 dark:text-stone-300"
            : "cursor-not-allowed text-stone-400 dark:text-stone-600")
        }
      >
        <input
          type="checkbox"
          disabled={!hasAboveThreshold}
          checked={filters.onlyAboveThreshold}
          onChange={(e) => {
            onChange({ ...filters, onlyAboveThreshold: e.target.checked });
          }}
          className="focus-ring h-4 w-4 rounded border-stone-300 text-amber-600 dark:border-stone-700 dark:bg-stone-900"
        />
        <span className="font-medium">Above threshold only</span>
      </label>

      <div className="ml-auto flex items-center gap-2 pb-1">
        <span className="text-xs tabular-nums text-stone-600 dark:text-stone-400">
          {isFiltered ? (
            <>
              <span className="font-semibold text-stone-900 dark:text-stone-100">
                {filteredCount}
              </span>{" "}
              of {total}
            </>
          ) : (
            <>
              <span className="font-semibold text-stone-900 dark:text-stone-100">{total}</span>{" "}
              {total === 1 ? "deal" : "deals"}
            </>
          )}
        </span>
        {isFiltered ? (
          <button
            type="button"
            onClick={() => {
              onChange(EMPTY_FILTERS);
            }}
            className="focus-ring rounded border border-stone-200 px-2 py-0.5 text-2xs font-medium uppercase tracking-wide text-stone-600 transition-colors duration-fast hover:bg-stone-100 dark:border-stone-700 dark:text-stone-300 dark:hover:bg-stone-800"
          >
            Clear
          </button>
        ) : null}
      </div>
    </div>
  );
}

function FilterSelect({
  label,
  value,
  options,
  allLabel,
  onChange,
}: {
  label: string;
  value: string;
  options: readonly string[];
  allLabel: string;
  onChange: (v: string) => void;
}) {
  const disabled = options.length === 0;
  return (
    <label className="block space-y-1">
      <span className="block text-2xs font-medium uppercase tracking-wide text-stone-500 dark:text-stone-400">
        {label}
      </span>
      <select
        disabled={disabled}
        value={value}
        onChange={(e) => {
          onChange(e.target.value);
        }}
        className="focus-ring block min-w-[10rem] rounded-md border border-stone-200 bg-white px-2.5 py-1.5 text-sm text-stone-900 disabled:bg-stone-100 disabled:text-stone-400 dark:border-stone-800 dark:bg-stone-900 dark:text-stone-100 dark:disabled:bg-stone-950"
      >
        <option value="">{allLabel}</option>
        {options.map((opt) => (
          <option key={opt} value={opt}>
            {opt}
          </option>
        ))}
      </select>
    </label>
  );
}

function distinctSorted(values: readonly (string | undefined)[]): string[] {
  const set = new Set<string>();
  for (const v of values) {
    if (v && v.trim() !== "") set.add(v);
  }
  return [...set].sort((a, b) => a.localeCompare(b));
}

export function applyDealFilters(deals: readonly Deal[], filters: DealFilters): Deal[] {
  return deals.filter((d) => {
    if (filters.make && d.make !== filters.make) return false;
    if (filters.status && d.status !== filters.status) return false;
    if (filters.onlyAboveThreshold && !d.above_threshold) return false;
    return true;
  });
}
