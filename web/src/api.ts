import { Candle } from "./engine/candles";

export interface Health {
  status: string;
  version: string;
  registered_assets: string[];
}

async function getJson<T>(url: string): Promise<T> {
  const resp = await fetch(url);
  if (!resp.ok) throw new Error(`${url}: ${resp.status}`);
  return resp.json() as Promise<T>;
}

export function getHealth(): Promise<Health> {
  return getJson("/health");
}

export interface ProfileSummary {
  asset: string;
  display_name: string;
  annual_vol: number;
  state_vol_ann: number[];
  expected_dwell_days: number[];
  volume_mode: string;
  last_close: number;
  last_date: string | null;
  periods_per_year: number;
  drift_ann: number;
  drift_ann_se: number;
  history_years: number | null;
}

/** What a run of `nCandles` bars actually means for this asset. Bar counts hide the
 *  calendar: 1500 bars is 4.1 years of 365-day crypto but 6.0 years of business days,
 *  and price dispersion grows with the square root of that span. */
export function horizon(p: ProfileSummary, nCandles: number) {
  const years = nCandles / p.periods_per_year;
  const sdLogPrice = p.annual_vol * Math.sqrt(years);
  return {
    years,
    sdLogPrice,
    // central 90% of terminal price, as a multiple of the starting level
    lowMultiple: Math.exp(-1.645 * sdLogPrice),
    highMultiple: Math.exp(1.645 * sdLogPrice),
  };
}

export function listProfiles(): Promise<Record<string, ProfileSummary>> {
  return getJson("/profiles");
}

/** The validation suite for one profile. 200 simulated histories take ~30s, so the
 *  server caches the report next to the profile and computes it in the background;
 *  a `computing` status means poll again. */
export function getValidation(
  asset: string,
  refresh = false,
): Promise<import("./engine/evidence").ValidationState> {
  return getJson(`/profiles/${asset}/validation${refresh ? "?refresh=true" : ""}`);
}

export interface ScenarioPreset {
  key: string;
  label: string;
  description: string;
}

export interface WorkflowOptions {
  resolutions: string[];
  formats: string[];
  presets: ScenarioPreset[];
  max_candles: number;
}

export function getWorkflow(): Promise<WorkflowOptions> {
  return getJson("/workflow");
}

/** The run definition the whole workflow revolves around: the same fields drive
 *  the chart and the export, so what you see is what you get on disk. */
export interface RunSpec {
  asset: string;
  nCandles: number;
  seed: number;
  resolution: string;
  preset: string;
  startPrice?: number;
  driftAnnual?: number | null; // null/undefined = the profile's calibrated drift
}

function runBody(run: RunSpec) {
  return {
    asset: run.asset,
    n_candles: run.nCandles,
    seed: run.seed,
    resolution: run.resolution,
    preset: run.preset === "standard" ? null : run.preset,
    // explicit anchor: the frontend dictates the start price so a stale
    // server-side pickle can't silently break the reference
    start_price: run.startPrice ?? null,
    drift_annual: run.driftAnnual ?? null,
  };
}

async function postRun(url: string, run: RunSpec, extra: object = {}): Promise<Response> {
  const resp = await fetch(url, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ ...runBody(run), ...extra }),
  });
  if (!resp.ok) {
    const detail = await resp.json().catch(() => null);
    throw new Error(detail?.detail ?? `${url}: ${resp.status}`);
  }
  return resp;
}

export interface SimulationResult {
  bars: Candle[];
  states: number[]; // HMM regime per returned bar, same grid as `bars`
  nStates: number;
  shockAt: number | null; // candle index of an injected shock (flash crash), if any
  preset: string | null;
}

export async function simulate(run: RunSpec): Promise<SimulationResult> {
  const resp = await postRun("/simulate", run);
  const body = await resp.json();
  return {
    bars: body.bars as Candle[],
    states: (body.states as number[]) ?? [],
    nStates: body.n_states ?? 2,
    shockAt: body.shock_at ?? null,
    preset: body.preset ?? null,
  };
}

/** Where a forecast should open: today's filtered regime probabilities and
 *  conditional volatility, against the unconditional level for comparison. */
export interface ConditionalState {
  asset: string;
  n_bars: number;
  last_date: string | null;
  state_probs: number[];
  stationary: number[];
  h: number;
  ann_vol: number;
  uncond_ann_vol: number;
  vol_ratio: number;
  periods_per_year: number;
}

export async function getConditional(asset: string): Promise<ConditionalState> {
  const resp = await fetch(`/profiles/${encodeURIComponent(asset)}/conditional`);
  if (!resp.ok) throw new Error(`/conditional: ${resp.status}`);
  return resp.json();
}

/** Many paths over N candles — the ensemble behind the distribution view. */
export async function runEnsemble(params: {
  asset: string;
  nCandles: number;
  nPaths: number;
  seed: number;
  preset: string;
  startPrice?: number;
  driftAnnual?: number | null;
  condition?: boolean;
}): Promise<import("./engine/ensemble").EnsembleResult> {
  const resp = await fetch("/ensemble", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      asset: params.asset,
      n_candles: params.nCandles,
      n_paths: params.nPaths,
      seed: params.seed,
      preset: params.preset === "standard" ? null : params.preset,
      start_price: params.startPrice ?? null,
      drift_annual: params.driftAnnual ?? null,
      condition: params.condition ?? false,
    }),
  });
  if (!resp.ok) throw new Error(`/ensemble: ${resp.status}`);
  return resp.json();
}

/** WebSocket URL for the animated ensemble build (one path at a time). */
export function ensembleWsUrl(params: {
  asset: string;
  nCandles: number;
  nPaths: number;
  seed: number;
  preset: string;
  startPrice?: number;
  driftAnnual?: number | null;
  condition?: boolean;
}): string {
  const proto = location.protocol === "https:" ? "wss" : "ws";
  const q = new URLSearchParams({
    asset: params.asset,
    n_candles: String(params.nCandles),
    n_paths: String(params.nPaths),
    seed: String(params.seed),
  });
  if (params.preset && params.preset !== "standard") q.set("preset", params.preset);
  if (params.startPrice) q.set("start_price", String(params.startPrice));
  if (params.driftAnnual != null) q.set("drift_annual", String(params.driftAnnual));
  if (params.condition) q.set("condition", "true");
  return `${proto}://${location.host}/ws/ensemble?${q}`;
}

/** Fetch an export and hand it to the browser as a download. Returns the filename
 *  the server chose, so the UI can report what actually landed on disk. */
export async function exportRun(run: RunSpec, fmt: string): Promise<string> {
  const resp = await postRun("/export", run, { fmt });
  const disposition = resp.headers.get("content-disposition") ?? "";
  const filename = /filename="([^"]+)"/.exec(disposition)?.[1] ?? `${run.asset}.${fmt}`;
  const url = URL.createObjectURL(await resp.blob());
  const a = document.createElement("a");
  a.href = url;
  a.download = filename;
  a.click();
  URL.revokeObjectURL(url);
  return filename;
}
