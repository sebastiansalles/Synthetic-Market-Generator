import { describe, expect, it } from "vitest";
import { grouped } from "../components/WorkflowPanel";

const profiles = {
  btcusd: { display_name: "Bitcoin" },
  btcusd_last3y: { display_name: "Bitcoin" },
  btcusd_last4y: { display_name: "Bitcoin" },
  eurusd: { display_name: "EUR/USD" },
  xauusd: { display_name: "XAU/USD (gold futures)" },
} as never;

describe("grouped profile picker", () => {
  it("one group per base asset, era variants as options under it", () => {
    const g = grouped(profiles);
    expect(g.map(([b]) => b)).toEqual(["btcusd", "eurusd", "xauusd"]); // one bitcoin
    const btc = g.find(([b]) => b === "btcusd")![1];
    expect(btc.map((v) => v.stem)).toEqual(["btcusd", "btcusd_last3y", "btcusd_last4y"]);
    expect(btc[0].label).toBe("Full history");
    expect(btc[1].label).toBe("Last 3 years");
  });

  it("variant without a base still renders under its derived base", () => {
    const g = grouped({ ethusd_last2y: {} } as never);
    expect(g[0][0]).toBe("ethusd");
    expect(g[0][1][0].label).toBe("Last 2 years");
  });
});
