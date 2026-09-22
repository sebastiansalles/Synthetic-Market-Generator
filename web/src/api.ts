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
}

export function listProfiles(): Promise<Record<string, ProfileSummary>> {
  return getJson("/profiles");
}

export async function simulate(
  asset: string,
  nCandles: number,
  seed: number,
  startPrice?: number,
): Promise<Candle[]> {
  const resp = await fetch("/simulate", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      asset,
      n_candles: nCandles,
      seed,
      // explicit anchor: the frontend dictates the start price so a stale
      // server-side pickle can't silently break the reference
      start_price: startPrice ?? null,
    }),
  });
  if (!resp.ok) throw new Error(`/simulate: ${resp.status}`);
  const body = await resp.json();
  return body.bars as Candle[];
}
