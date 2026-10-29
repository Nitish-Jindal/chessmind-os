import { describe, expect, it } from "vitest";
import { resolveApiBase } from "./api";

describe("resolveApiBase", () => {
  it("defaults to the local backend", () => {
    expect(resolveApiBase(undefined)).toBe("http://localhost:8000");
    expect(resolveApiBase("  ")).toBe("http://localhost:8000");
  });
  it("passes full URLs through without a trailing slash", () => {
    expect(resolveApiBase("https://api.example.com/")).toBe("https://api.example.com");
    expect(resolveApiBase("http://localhost:8000")).toBe("http://localhost:8000");
  });
  it("adds https to a public host", () => {
    expect(resolveApiBase("chessmind-api.onrender.com")).toBe("https://chessmind-api.onrender.com");
  });
  it("expands a bare Render service host", () => {
    expect(resolveApiBase("chessmind-api-x7k2")).toBe("https://chessmind-api-x7k2.onrender.com");
  });
  it("keeps localhost on http", () => {
    expect(resolveApiBase("localhost:8000")).toBe("http://localhost:8000");
  });
});
