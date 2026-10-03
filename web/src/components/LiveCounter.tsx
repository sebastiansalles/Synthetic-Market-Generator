import { useStore } from "../store";

export const LIVE_TOTAL = 400;

/** Small isolated component: subscribes to the live session so only IT re-renders
 *  per tick — App deliberately avoids a wholesale `live` subscription. */
export function LiveCounter() {
  const live = useStore((s) => s.live);
  if (!live) return null;
  const x = live.bars.length + (live.forming ? 1 : 0);
  return (
    <span className="live-count" title="live session progress">
      ▶ {x}/{LIVE_TOTAL}
      {live.done ? " · complete" : ""}
    </span>
  );
}
