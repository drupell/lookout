import { describe, expect, it } from "vitest";

import { parseList, pickDirty } from "./dirty";

describe("pickDirty", () => {
  it("returns undefined when nothing is dirty", () => {
    expect(pickDirty({ a: 1, b: 2 }, undefined)).toBeUndefined();
    expect(pickDirty({ a: 1 }, {})).toBeUndefined();
  });

  it("picks a single dirty leaf", () => {
    expect(pickDirty({ a: 1, b: 2 }, { b: true })).toEqual({ b: 2 });
  });

  it("recurses into nested objects", () => {
    const values = { search: { zip: "10001", radius: 50 }, tier: "byok" };
    const dirty = { search: { zip: true } };
    expect(pickDirty(values, dirty)).toEqual({ search: { zip: "10001" } });
  });

  it("treats any dirty array element as 'send the full array'", () => {
    const values = { fuels: ["EV", "PHEV"] };
    const dirty = { fuels: [true, false] };
    expect(pickDirty(values, dirty)).toEqual({ fuels: ["EV", "PHEV"] });
  });

  it("prunes empty branches", () => {
    const values = { search: { zip: "10001" }, vehicle: { vin: "abc" } };
    const dirty = { search: {}, vehicle: { vin: true } };
    expect(pickDirty(values, dirty)).toEqual({ vehicle: { vin: "abc" } });
  });
});

describe("parseList", () => {
  it("trims and drops blanks", () => {
    expect(parseList("  ford , chevy ,, tesla,")).toEqual(["ford", "chevy", "tesla"]);
  });

  it("returns empty for empty input", () => {
    expect(parseList("")).toEqual([]);
    expect(parseList("   ")).toEqual([]);
  });
});
