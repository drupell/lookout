"use client";

import { useEffect, useRef, useState } from "react";
import {
  Controller,
  useForm,
  useWatch,
  type Control,
  type Path,
  type UseFormRegister,
} from "react-hook-form";

import { Button, FormField, Input, Select, cn, useToast, type SelectOption } from "@/components/ui";
import { ApiError, api } from "@/lib/api";
import { formatDaysOfWeek, formatNextRunDate, relativeFuture } from "@/lib/dealFormat";
import type { Preferences, PrefsResponse } from "@/lib/schemas";

import { parseList, pickDirty } from "./dirty";

const RESULT_COUNT_OPTIONS_DEFAULT: readonly SelectOption[] = [
  { value: "10", label: "10" },
  { value: "25", label: "25" },
  { value: "50", label: "50 (max for default tier)" },
];

const RESULT_COUNT_OPTIONS_BYOK: readonly SelectOption[] = [
  { value: "10", label: "10" },
  { value: "25", label: "25" },
  { value: "50", label: "50" },
  { value: "100", label: "100" },
  { value: "200", label: "200" },
  { value: "500", label: "500 (max)" },
];

interface Props {
  prefs: PrefsResponse;
  /** The user's next scheduled run (ISO-8601 UTC) or null — shown in the
   *  Schedule section so the cadence is concrete, not abstract. */
  nextRunAt?: string | null | undefined;
  onSaved: (next: PrefsResponse) => void;
  /**
   * Fired when a save is rejected with 403 — a default-tier user touched a
   * BYOK-only field. The parent resyncs `/me` + prefs so the now-disabled
   * fields re-lock; we surface targeted guidance here.
   */
  onTierViolation: () => void;
}

type SaveState =
  | { kind: "idle" }
  | { kind: "saving" }
  | { kind: "ok" }
  | { kind: "tier-locked" }
  | { kind: "error"; message: string };

const TIER_GUIDANCE =
  "That setting needs the BYOK tier — add your MarketCheck key below to unlock it.";

export function PrefsForm({ prefs, nextRunAt, onSaved, onTierViolation }: Props) {
  const toast = useToast();
  const writable = new Set(prefs.writable_paths);
  const canWrite = (path: string) => writable.has(path);

  // Scoring + schedule are the BYOK-gated groups; we use the notify threshold
  // as the bellwether for "this user can edit advanced controls".
  const advancedUnlocked = canWrite("scoring.threshold_notify");

  const {
    register,
    control,
    handleSubmit,
    reset,
    formState: { dirtyFields, isDirty, errors },
  } = useForm<Preferences>({
    defaultValues: prefs.effective,
  });

  const [save, setSave] = useState<SaveState>({ kind: "idle" });

  // Re-sync defaults whenever the parent hands us a fresh prefs blob.
  useEffect(() => {
    reset(prefs.effective);
  }, [prefs, reset]);

  const onSubmit = handleSubmit(async (values) => {
    const patch = pickDirty(values, dirtyFields);
    if (!patch) {
      setSave({ kind: "ok" });
      return;
    }
    setSave({ kind: "saving" });
    try {
      const updated = await api.putPrefs(patch);
      onSaved(updated);
      // Reset to the saved values so the form is no longer dirty — without
      // this, `isDirty` stays true and the Save button never re-disables.
      reset(updated.effective);
      setSave({ kind: "ok" });
      toast.success("Preferences saved");
    } catch (err: unknown) {
      // 403 == tier violation: the user IS authenticated but tried to write a
      // BYOK-only field. Don't show a raw error — resync caps so the offending
      // fields re-lock, and point them at the upgrade path.
      if (err instanceof ApiError && err.status === 403) {
        setSave({ kind: "tier-locked" });
        toast.error("Upgrade required", { description: TIER_GUIDANCE });
        onTierViolation();
        return;
      }
      const message =
        err instanceof ApiError ? err.toUserMessage() : "Failed to save preferences";
      setSave({ kind: "error", message });
      toast.error(message);
    }
  });

  return (
    <form onSubmit={onSubmit} className="space-y-8">
      <Section title="Search">
        <NumberField
          label="ZIP code"
          path="search.location_zip"
          register={register}
          name="search.location_zip"
          canWrite={canWrite}
          asString
        />
        <NumberField
          label="Radius (miles)"
          path="search.radius_miles"
          register={register}
          name="search.radius_miles"
          canWrite={canWrite}
          integer
        />
        <NumberField
          label="Max vehicle age (years)"
          path="search.max_vehicle_age_years"
          register={register}
          name="search.max_vehicle_age_years"
          canWrite={canWrite}
          integer
        />
        <NumberField
          label="Min price (USD)"
          path="search.min_price_usd"
          register={register}
          name="search.min_price_usd"
          canWrite={canWrite}
          integer
        />
        <NumberField
          label="Max price (USD)"
          path="search.max_price_usd"
          register={register}
          name="search.max_price_usd"
          canWrite={canWrite}
          integer
        />
        <NumberField
          label="Max mileage (miles)"
          path="search.max_mileage_miles"
          register={register}
          name="search.max_mileage_miles"
          canWrite={canWrite}
          integer
          help="Skip vehicles above this odometer reading. 0 = no cap."
        />
        <ListField
          label="Fuel types"
          path="search.fuel_types"
          name="search.fuel_types"
          control={control}
          canWrite={canWrite}
          help="Separated by commas — spaces around commas are fine. e.g. Electric, Plug-in Hybrid"
        />
        <ListField
          label="Body styles"
          path="search.body_styles"
          name="search.body_styles"
          control={control}
          canWrite={canWrite}
          help="Separated by commas — spaces fine. e.g. sedan, suv, crossover"
        />
        <SelectField
          label="Listings per run"
          path="search.target_listings"
          name="search.target_listings"
          register={register}
          control={control}
          canWrite={canWrite}
          options={
            // Default tier is server-side clamped at 50; BYOK can scale up.
            // Capped at 500 by the Pydantic model — anything higher just
            // wastes LLM tokens on long-tail deals.
            advancedUnlocked ? RESULT_COUNT_OPTIONS_BYOK : RESULT_COUNT_OPTIONS_DEFAULT
          }
          help="The agent stops early once it has this many qualifying listings."
        />
        <ListField
          label="Included brands"
          path="included_brands"
          name="included_brands"
          control={control}
          canWrite={canWrite}
          help="Separated by commas — spaces fine. Leave empty to include all brands. e.g. Tesla, Ford, Hyundai"
        />
        <ListField
          label="Excluded brands"
          path="excluded_brands"
          name="excluded_brands"
          control={control}
          canWrite={canWrite}
          help="Separated by commas — spaces fine. Wins over Included brands. e.g. Stellantis, Mitsubishi"
        />
        <ListField
          label="Excluded models"
          path="excluded_models"
          name="excluded_models"
          control={control}
          canWrite={canWrite}
          help="Separated by commas — spaces fine. Models to skip. e.g. Bolt EUV, Leaf"
        />
      </Section>

      <Section title="Vehicle & trade-in" hint="Optional — used to value your trade-in.">
        <NumberField
          label="VIN"
          path="vehicle.vin"
          register={register}
          name="vehicle.vin"
          canWrite={canWrite}
          asString
        />
        <NumberField
          label="Mileage"
          path="vehicle.mileage"
          register={register}
          name="vehicle.mileage"
          canWrite={canWrite}
          integer
        />
        <NumberField
          label="Condition"
          path="vehicle.condition"
          register={register}
          name="vehicle.condition"
          canWrite={canWrite}
          asString
        />
        <NumberField
          label="Trade-in floor (USD)"
          path="vehicle.trade_in_floor_usd"
          register={register}
          name="vehicle.trade_in_floor_usd"
          canWrite={canWrite}
          integer
        />
      </Section>

      <Section title="Deal criteria">
        <NumberField
          label="Max effective monthly (USD)"
          path="deal_criteria.max_effective_monthly_usd"
          register={register}
          name="deal_criteria.max_effective_monthly_usd"
          canWrite={canWrite}
          integer
        />
        <NumberField
          label="Min discount off MSRP (%)"
          path="deal_criteria.min_discount_off_msrp_pct"
          register={register}
          name="deal_criteria.min_discount_off_msrp_pct"
          canWrite={canWrite}
        />
        <NumberField
          label="Max acceptable APR (%)"
          path="deal_criteria.acceptable_apr_max"
          register={register}
          name="deal_criteria.acceptable_apr_max"
          canWrite={canWrite}
        />
        <CheckboxField
          label="Lease-to-own preferred"
          path="deal_criteria.lease_to_own_preferred"
          register={register}
          name="deal_criteria.lease_to_own_preferred"
          canWrite={canWrite}
        />
        <CheckboxField
          label="Prefer 0% financing"
          path="deal_criteria.zero_percent_financing_preferred"
          register={register}
          name="deal_criteria.zero_percent_financing_preferred"
          canWrite={canWrite}
        />
      </Section>

      <Section
        title="Scoring"
        badge={advancedUnlocked ? null : "BYOK only"}
        hint={advancedUnlocked ? undefined : TIER_GUIDANCE}
      >
        <NumberField
          label="Notify threshold (0–1)"
          path="scoring.threshold_notify"
          register={register}
          name="scoring.threshold_notify"
          canWrite={canWrite}
        />
        <NumberField
          label="Draft email threshold (0–1)"
          path="scoring.threshold_draft_email"
          register={register}
          name="scoring.threshold_draft_email"
          canWrite={canWrite}
        />
      </Section>

      <Section
        title="Schedule"
        badge={canWrite("schedule.days_of_week") ? null : "BYOK only"}
        hint={canWrite("schedule.days_of_week") ? undefined : TIER_GUIDANCE}
      >
        {/* The summary leads both tiers; it reads the live form value so it
            updates as BYOK users toggle days, not just on save. */}
        <ScheduleSummaryLive
          control={control}
          editable={canWrite("schedule.days_of_week")}
          nextRunAt={nextRunAt}
        />
        {canWrite("schedule.days_of_week") ? (
          <>
            <WeekdayPicker
              control={control}
              {...(errors.schedule?.days_of_week?.message !== undefined
                ? { error: errors.schedule.days_of_week.message }
                : {})}
            />
            <TimeField
              label="Time of day"
              path="schedule.time_of_day_utc"
              register={register}
              name="schedule.time_of_day_utc"
              canWrite={canWrite}
            />
          </>
        ) : null}
      </Section>

      <div className="editorial-rule" />
      <div className="flex flex-wrap items-center gap-3">
        <Button
          type="submit"
          disabled={!isDirty || errors.schedule?.days_of_week !== undefined}
          loading={save.kind === "saving"}
        >
          {save.kind === "saving" ? "Saving…" : "Save changes"}
        </Button>
        {save.kind === "ok" && !isDirty ? (
          <span className="text-xs text-emerald-600 dark:text-emerald-400">Saved</span>
        ) : null}
        {save.kind === "tier-locked" ? (
          <span className="text-xs text-fg-muted dark:text-stone-400">{TIER_GUIDANCE}</span>
        ) : null}
        {save.kind === "error" ? (
          <span className="text-xs text-red-600 dark:text-red-400">{save.message}</span>
        ) : null}
      </div>
    </form>
  );
}

// ---- Field primitives ----

function Section({
  title,
  badge,
  hint,
  children,
}: {
  title: string;
  badge?: string | null;
  hint?: string | undefined;
  children: React.ReactNode;
}) {
  return (
    <section className="space-y-4">
      <div className="space-y-1 border-b border-border pb-2 dark:border-stone-800">
        <div className="flex items-baseline gap-2">
          {/* Spectral serif section title — the editorial voice carried down
              into the form, matching SectionHeader's hierarchy. */}
          <h3 className="font-serif text-lg font-medium tracking-tight text-stone-900 dark:text-stone-100">
            {title}
          </h3>
          {badge ? (
            <span className="rounded bg-stone-100 px-1.5 py-0.5 text-2xs font-medium uppercase tracking-wide text-stone-700 dark:bg-stone-800 dark:text-stone-300">
              {badge}
            </span>
          ) : null}
        </div>
        {hint ? <p className="text-xs text-fg-muted dark:text-stone-500">{hint}</p> : null}
      </div>
      <div className="grid grid-cols-1 gap-4 md:grid-cols-2">{children}</div>
    </section>
  );
}

/** "13:00" → "1:00 PM"; passes anything unparseable straight through. */
function timeLabel(hhmm: string | undefined): string {
  if (!hhmm || !/^([01]\d|2[0-3]):[0-5]\d$/.test(hhmm)) return hhmm ?? "";
  const [h, m] = hhmm.split(":").map(Number);
  const hour = h ?? 0;
  const period = hour < 12 ? "AM" : "PM";
  const hour12 = hour % 12 === 0 ? 12 : hour % 12;
  return `${String(hour12)}:${String(m ?? 0).padStart(2, "0")} ${period}`;
}

const WEEKDAY_CHIPS = [
  { value: 0, short: "Mon", full: "Monday" },
  { value: 1, short: "Tue", full: "Tuesday" },
  { value: 2, short: "Wed", full: "Wednesday" },
  { value: 3, short: "Thu", full: "Thursday" },
  { value: 4, short: "Fri", full: "Friday" },
  { value: 5, short: "Sat", full: "Saturday" },
  { value: 6, short: "Sun", full: "Sunday" },
] as const;

const PRESETS: { label: string; days: number[] }[] = [
  { label: "Every day", days: [0, 1, 2, 3, 4, 5, 6] },
  { label: "Weekdays", days: [0, 1, 2, 3, 4] },
  { label: "Weekends", days: [5, 6] },
];

function sortedUnique(days: number[]): number[] {
  return Array.from(new Set(days)).sort((a, b) => a - b);
}

function sameDays(a: number[], b: number[]): boolean {
  const x = sortedUnique(a);
  const y = sortedUnique(b);
  return x.length === y.length && x.every((v, i) => v === y[i]);
}

/**
 * A plain-language summary of the user's schedule sitting above the controls.
 * Reads the LIVE form value (via useWatch) so it updates instantly as BYOK
 * users toggle days; default tier sees the same line, display-only. The
 * concrete next run (in UTC) anchors it. Spans both grid columns so it reads
 * as a lead-in, not a field. Example:
 *   "Mondays & Thursdays · 1:00 PM UTC · next run Thu, May 28 (in 3 days)"
 */
function ScheduleSummaryLive({
  control,
  editable,
  nextRunAt,
}: {
  control: Control<Preferences>;
  editable: boolean;
  nextRunAt?: string | null | undefined;
}) {
  const schedule = useWatch({ control, name: "schedule" });
  const days = Array.isArray(schedule.days_of_week) ? schedule.days_of_week : [];
  const daysPhrase = formatDaysOfWeek(days);
  const time = timeLabel(schedule.time_of_day_utc);

  const nextDate = formatNextRunDate(nextRunAt);
  const relative = relativeFuture(nextRunAt);

  return (
    <div className="md:col-span-2 rounded-md border border-border bg-surface-elevated px-3.5 py-3 text-sm dark:border-stone-800 dark:bg-stone-900/40">
      <p className="text-stone-800 dark:text-stone-200">
        <span className="font-medium">{daysPhrase}</span>
        {time ? (
          <span className="text-fg-muted dark:text-stone-400">{` · ${time} UTC`}</span>
        ) : null}
        {nextDate ? (
          <>
            <span className="text-fg-muted dark:text-stone-400">{` · next run ${nextDate}`}</span>
            {relative ? (
              <span className="text-fg-muted dark:text-stone-500">{` (${relative})`}</span>
            ) : null}
          </>
        ) : (
          <span className="text-fg-muted dark:text-stone-400"> · next run not scheduled</span>
        )}
      </p>
      <p className="mt-1 text-xs text-fg-muted dark:text-stone-500">
        {editable
          ? "Pick the days and time your agent runs — all times are UTC."
          : "On the default tier this is fixed. Add your MarketCheck key below to choose your own days and time."}
      </p>
    </div>
  );
}

/**
 * BYOK weekday multi-select: seven accessible toggle chips (0=Mon … 6=Sun)
 * plus quick presets. Bound to `schedule.days_of_week` via Controller and
 * validated to require at least one day. Chips are real buttons with
 * aria-pressed and a focus ring; motion respects prefers-reduced-motion.
 */
function WeekdayPicker({
  control,
  error,
}: {
  control: Control<Preferences>;
  error?: string;
}) {
  return (
    <div className="md:col-span-2">
      <Controller
        control={control}
        name="schedule.days_of_week"
        rules={{
          validate: (value) =>
            (Array.isArray(value) && value.length > 0) || "Select at least one day.",
        }}
        render={({ field }) => {
          const selected = Array.isArray(field.value) ? field.value : [];
          const toggle = (day: number) => {
            const next = selected.includes(day)
              ? selected.filter((d) => d !== day)
              : [...selected, day];
            field.onChange(sortedUnique(next));
          };
          return (
            <FormField
              label="Days of week"
              {...(error !== undefined ? { error } : {})}
            >
              <div className="space-y-2.5">
                <div role="group" aria-label="Days of week" className="flex flex-wrap gap-1.5">
                  {WEEKDAY_CHIPS.map((chip) => {
                    const isOn = selected.includes(chip.value);
                    return (
                      <button
                        key={chip.value}
                        type="button"
                        aria-pressed={isOn}
                        aria-label={chip.full}
                        onClick={() => {
                          toggle(chip.value);
                        }}
                        className={cn(
                          "focus-ring inline-flex h-9 min-w-[3rem] items-center justify-center rounded-md border px-3 text-sm font-medium",
                          "transition-[background-color,border-color,color] duration-fast ease-editorial motion-reduce:transition-none",
                          isOn
                            ? "border-ink bg-ink text-ink-fg dark:border-stone-100 dark:bg-stone-100 dark:text-stone-900"
                            : "border-stone-200 bg-white text-stone-700 hover:bg-stone-50 dark:border-stone-800 dark:bg-stone-900 dark:text-stone-300 dark:hover:bg-stone-800",
                        )}
                      >
                        {chip.short}
                      </button>
                    );
                  })}
                </div>
                <div className="flex flex-wrap gap-1.5">
                  {PRESETS.map((preset) => {
                    const isActive = sameDays(selected, preset.days);
                    return (
                      <button
                        key={preset.label}
                        type="button"
                        aria-pressed={isActive}
                        onClick={() => {
                          field.onChange(sortedUnique(preset.days));
                        }}
                        className={cn(
                          "focus-ring inline-flex items-center rounded-full border px-2.5 py-1 text-2xs font-medium uppercase tracking-wide",
                          "transition-colors duration-fast ease-editorial motion-reduce:transition-none",
                          isActive
                            ? "border-brand/40 bg-brand/10 text-brand dark:border-brand/40 dark:bg-brand/15"
                            : "border-stone-200 text-stone-600 hover:bg-stone-50 dark:border-stone-800 dark:text-stone-400 dark:hover:bg-stone-800",
                        )}
                      >
                        {preset.label}
                      </button>
                    );
                  })}
                </div>
              </div>
            </FormField>
          );
        }}
      />
    </div>
  );
}

/**
 * UTC time field for `time_of_day_utc`. Uses a native `<input type="time">`
 * (24h HH:MM in/out, which matches the backend contract) with a UTC label so
 * there's no timezone ambiguity. Registered with RHF so dirty-tracking and
 * reset-after-save work like every other field.
 */
function TimeField<T extends Preferences>({
  label,
  path,
  name,
  register,
  canWrite,
}: {
  label: string;
  path: string;
  name: Path<T>;
  register: UseFormRegister<T>;
  canWrite: (path: string) => boolean;
}) {
  const enabled = canWrite(path);
  return (
    <FormField label={label} hint="24-hour, UTC — when the run fires on each selected day.">
      <Input type="time" disabled={!enabled} {...register(name)} />
    </FormField>
  );
}

function NumberField<T extends Preferences>({
  label,
  path,
  name,
  register,
  canWrite,
  asString,
  integer,
  help,
}: {
  label: string;
  path: string;
  name: Path<T>;
  register: UseFormRegister<T>;
  canWrite: (path: string) => boolean;
  /** Bypass numeric coercion entirely (e.g. VIN, condition labels). */
  asString?: boolean;
  /** Whole-number field (counts, mileage, dollars). Hides the decimal key on
   *  mobile and lets us coerce to int on submit. */
  integer?: boolean;
  help?: string;
}) {
  const enabled = canWrite(path);
  // We use type="text" + inputMode instead of type="number" on purpose:
  // type="number" treats mousewheel and arrow keys as silent +/- operations,
  // which produced "I typed 34000 but it saved 33994" bugs. We only ever
  // want users editing by typing. setValueAs converts on submit; the backend
  // Pydantic models do range validation.
  const opts = asString
    ? {}
    : {
        setValueAs: (v: string) => {
          if (v === "") return undefined;
          const n = Number(v);
          if (Number.isNaN(n)) return undefined;
          return integer ? Math.trunc(n) : n;
        },
      };
  const inputMode = asString ? undefined : integer ? "numeric" : "decimal";
  return (
    <FormField label={label} {...(help !== undefined ? { hint: help } : {})}>
      <Input
        type="text"
        inputMode={inputMode}
        disabled={!enabled}
        {...register(name, opts)}
      />
    </FormField>
  );
}

function SelectField({
  label,
  path,
  name,
  register,
  control,
  canWrite,
  options,
  help,
}: {
  label: string;
  path: string;
  name: Path<Preferences>;
  register: UseFormRegister<Preferences>;
  control: Control<Preferences>;
  canWrite: (path: string) => boolean;
  options: readonly SelectOption[];
  help?: string;
}) {
  const enabled = canWrite(path);
  // If the user's current value sits outside the canonical options (e.g. an
  // existing BYOK user being rendered with default-tier options, or a value
  // that pre-dates today's option set), the native <select> falls back to
  // showing blank. Prepend a transient option so the value renders correctly
  // until the user picks a canonical one.
  const current = useWatch({ control, name });
  const currentStr =
    typeof current === "number" || typeof current === "string" ? String(current) : "";
  const hasCurrent = options.some((o) => o.value === currentStr);
  const renderedOptions: readonly SelectOption[] =
    currentStr && !hasCurrent
      ? [{ value: currentStr, label: `${currentStr} (current)` }, ...options]
      : options;
  return (
    <FormField label={label} {...(help !== undefined ? { hint: help } : {})}>
      <Select
        disabled={!enabled}
        options={renderedOptions}
        {...register(name, {
          // Stored as numbers in form state — backend expects ints.
          setValueAs: (v: string) => (v === "" ? undefined : Number(v)),
        })}
      />
    </FormField>
  );
}

function CheckboxField<T extends Preferences>({
  label,
  path,
  name,
  register,
  canWrite,
}: {
  label: string;
  path: string;
  name: Path<T>;
  register: UseFormRegister<T>;
  canWrite: (path: string) => boolean;
}) {
  const enabled = canWrite(path);
  return (
    <label className="flex items-center gap-2 text-sm">
      <input
        type="checkbox"
        disabled={!enabled}
        className="focus-ring h-4 w-4 rounded border-stone-300 text-brand dark:border-stone-700 dark:bg-stone-900"
        {...register(name)}
      />
      <span
        className={
          enabled
            ? "text-stone-800 dark:text-stone-200"
            : "text-stone-400 dark:text-stone-600"
        }
      >
        {label}
      </span>
    </label>
  );
}

function ListField({
  label,
  path,
  name,
  control,
  canWrite,
  help,
}: {
  label: string;
  path: string;
  name: Path<Preferences>;
  control: Control<Preferences>;
  canWrite: (path: string) => boolean;
  help?: string;
}) {
  const enabled = canWrite(path);
  return (
    <FormField label={label} {...(help !== undefined ? { hint: help } : {})}>
      <Controller
        control={control}
        name={name}
        render={({ field }) => (
          <ListInput
            value={Array.isArray(field.value) ? (field.value as string[]) : []}
            disabled={!enabled}
            onChange={field.onChange}
            onBlur={field.onBlur}
            inputRef={field.ref}
          />
        )}
      />
    </FormField>
  );
}

/**
 * Comma-separated list editor. Stores raw text locally — only normalizes to
 * the array shape that lands in form state.
 *
 * Why not parse on every keystroke: doing so round-trips the display through
 * `Array#join`, which strips trailing commas/spaces and prevents the user
 * from typing past a separator (you'd type "BMW," and it'd revert to "BMW").
 * Local text + array on form-side fixes that without losing dirty tracking.
 */
function ListInput({
  value,
  disabled,
  onChange,
  onBlur,
  inputRef,
}: {
  value: string[];
  disabled: boolean;
  onChange: (next: string[]) => void;
  onBlur: () => void;
  inputRef: (instance: HTMLInputElement | null) => void;
}) {
  const externalText = value.join(", ");
  const [text, setText] = useState(externalText);
  const lastSyncedExternal = useRef(externalText);

  // If the form re-syncs (e.g., after `reset()` or a successful save), pull
  // the new canonical value into local text. Local edits in flight are
  // preserved — we only overwrite when the EXTERNAL value actually changes.
  useEffect(() => {
    if (externalText !== lastSyncedExternal.current) {
      setText(externalText);
      lastSyncedExternal.current = externalText;
    }
  }, [externalText]);

  return (
    <Input
      type="text"
      disabled={disabled}
      value={text}
      onChange={(e) => {
        const next = e.target.value;
        setText(next);
        onChange(parseList(next));
      }}
      onBlur={onBlur}
      ref={inputRef}
    />
  );
}
