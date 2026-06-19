import { describe, expect, it } from "vitest";

import { previewFixture } from "./preview";
import {
  AddFavoriteResponseSchema,
  ByokKeyResponseSchema,
  DealsResponseSchema,
  FavoritesResponseSchema,
  InFlightRunSchema,
  IncentiveStackResponseSchema,
  MacroSnapshotSchema,
  MeResponseSchema,
  PrefsResponseSchema,
  RemoveFavoriteResponseSchema,
  RunsResponseSchema,
  TriggerRunResponseSchema,
} from "./schemas";

// Every preview fixture must satisfy the real Zod schema the UI parses it
// against — otherwise a screen would error in preview. This guards against
// fixture/schema drift.
describe("preview fixtures match their schemas", () => {
  it("GET /me", () => {
    expect(() => MeResponseSchema.parse(previewFixture("/me", "GET"))).not.toThrow();
  });
  it("GET /me/prefs", () => {
    expect(() => PrefsResponseSchema.parse(previewFixture("/me/prefs", "GET"))).not.toThrow();
  });
  it("GET /me/runs", () => {
    expect(() => RunsResponseSchema.parse(previewFixture("/me/runs", "GET"))).not.toThrow();
  });
  it("POST /me/runs", () => {
    expect(() =>
      TriggerRunResponseSchema.parse(previewFixture("/me/runs", "POST")),
    ).not.toThrow();
  });
  it("GET /me/runs/in_flight", () => {
    expect(() =>
      InFlightRunSchema.parse(previewFixture("/me/runs/in_flight", "GET")),
    ).not.toThrow();
  });
  it("GET /me/deals", () => {
    expect(() => DealsResponseSchema.parse(previewFixture("/me/deals", "GET"))).not.toThrow();
  });
  it("GET /me/favorites", () => {
    expect(() =>
      FavoritesResponseSchema.parse(previewFixture("/me/favorites", "GET")),
    ).not.toThrow();
  });
  it("POST /me/deals/{id}/favorite", () => {
    expect(() =>
      AddFavoriteResponseSchema.parse(previewFixture("/me/deals/preview-1/favorite", "POST")),
    ).not.toThrow();
  });
  it("DELETE /me/deals/{id}/favorite", () => {
    expect(() =>
      RemoveFavoriteResponseSchema.parse(
        previewFixture("/me/deals/preview-1/favorite", "DELETE"),
      ),
    ).not.toThrow();
  });
  it("PUT /me/byok-key", () => {
    expect(() =>
      ByokKeyResponseSchema.parse(previewFixture("/me/byok-key", "PUT")),
    ).not.toThrow();
  });
  it("GET /me/incentives", () => {
    expect(() =>
      IncentiveStackResponseSchema.parse(previewFixture("/me/incentives", "GET")),
    ).not.toThrow();
  });
  it("GET /me/macro", () => {
    expect(() =>
      MacroSnapshotSchema.parse(previewFixture("/me/macro", "GET")),
    ).not.toThrow();
  });
});
