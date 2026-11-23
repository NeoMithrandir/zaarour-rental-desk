// The area boxes and reject reasons match the Mac desk (desk/web.py area_chips, REJECT_REASONS).
import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { areaChips, REJECT_REASONS } from "../src/api.js";

const AREAS = JSON.parse(readFileSync(new URL("../../config/areas.json", import.meta.url), "utf8")).areas;

test("area boxes follow the phase and keep config order", () => {
  const p1 = areaChips(AREAS, "1", { Lachine: 4 });
  assert.ok(p1.length && p1.every((a) => a.phase === 1));
  assert.equal(p1[0].name, Object.keys(AREAS)[0]);
  assert.equal(p1.find((a) => a.name === "Lachine").n, 4);
  assert.equal(areaChips(AREAS, "", {}).length, Object.keys(AREAS).length);
  assert.deepEqual(areaChips(AREAS, "2", { "": 3 }).at(-1), { name: "", phase: null, n: 3 });
});

test("reject reasons are the Mac desk's", () => {
  const py = readFileSync(new URL("../../desk/web.py", import.meta.url), "utf8").match(/^REJECT_REASONS = (\[.*\])$/m)[1];
  assert.deepEqual(REJECT_REASONS, JSON.parse(py));
});
