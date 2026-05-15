import { canonicalJson, sha256Hex, computeEventHash, computePayloadHash } from "./hashing";

// ---------------------------------------------------------------------------
// canonicalJson — must produce byte-identical output to Python's
//   json.dumps(value, sort_keys=True, separators=(",", ":"))
// ---------------------------------------------------------------------------

describe("canonicalJson", () => {
  test("null", () => {
    expect(canonicalJson(null)).toBe("null");
  });

  test("undefined treated as null", () => {
    expect(canonicalJson(undefined)).toBe("null");
  });

  test("booleans", () => {
    expect(canonicalJson(true)).toBe("true");
    expect(canonicalJson(false)).toBe("false");
  });

  test("integers", () => {
    expect(canonicalJson(0)).toBe("0");
    expect(canonicalJson(42)).toBe("42");
    expect(canonicalJson(-7)).toBe("-7");
  });

  test("floats", () => {
    expect(canonicalJson(3.14)).toBe("3.14");
    expect(canonicalJson(1.0)).toBe("1");  // JSON.stringify(1.0) === "1"
  });

  test("NaN and Infinity throw", () => {
    expect(() => canonicalJson(NaN)).toThrow(TypeError);
    expect(() => canonicalJson(Infinity)).toThrow(TypeError);
    expect(() => canonicalJson(-Infinity)).toThrow(TypeError);
  });

  test("strings with escapes", () => {
    expect(canonicalJson("hello")).toBe('"hello"');
    expect(canonicalJson('say "hi"')).toBe('"say \\"hi\\""');
    expect(canonicalJson("line\nnew")).toBe('"line\\nnew"');
  });

  test("empty object", () => {
    expect(canonicalJson({})).toBe("{}");
  });

  test("object with sorted keys", () => {
    const result = canonicalJson({ z: 1, a: 2, m: 3 });
    expect(result).toBe('{"a":2,"m":3,"z":1}');
  });

  test("nested object key sorting", () => {
    const result = canonicalJson({ b: { d: 4, c: 3 }, a: { f: 6, e: 5 } });
    expect(result).toBe('{"a":{"e":5,"f":6},"b":{"c":3,"d":4}}');
  });

  test("array preserves order", () => {
    expect(canonicalJson([3, 1, 2])).toBe("[3,1,2]");
  });

  test("array of objects", () => {
    const result = canonicalJson([{ z: 1, a: 2 }, { y: 3, b: 4 }]);
    expect(result).toBe('[{"a":2,"z":1},{"b":4,"y":3}]');
  });

  test("empty array", () => {
    expect(canonicalJson([])).toBe("[]");
  });

  test("mixed types", () => {
    const obj = { num: 1, str: "x", bool: true, nil: null, arr: [1, 2] };
    const result = canonicalJson(obj);
    expect(result).toBe('{"arr":[1,2],"bool":true,"nil":null,"num":1,"str":"x"}');
  });

  // Python parity: json.dumps({"b":2,"a":1}, sort_keys=True, separators=(",",":")) == '{"a":1,"b":2}'
  test("Python parity — known vector", () => {
    const result = canonicalJson({ b: 2, a: 1 });
    expect(result).toBe('{"a":1,"b":2}');
  });

  // Python parity: json.dumps({"event_type":"model_call","model":"gpt-4o","tokens":1234},
  //                            sort_keys=True, separators=(",",":"))
  test("Python parity — event envelope shape", () => {
    const result = canonicalJson({
      event_type: "model_call",
      model: "gpt-4o",
      tokens: 1234,
    });
    expect(result).toBe('{"event_type":"model_call","model":"gpt-4o","tokens":1234}');
  });
});

// ---------------------------------------------------------------------------
// sha256Hex
// ---------------------------------------------------------------------------

describe("sha256Hex", () => {
  test("empty string — known SHA-256", () => {
    // echo -n '' | shasum -a 256
    expect(sha256Hex("")).toBe(
      "sha256:e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"
    );
  });

  test("prefixes with sha256:", () => {
    expect(sha256Hex("hello").startsWith("sha256:")).toBe(true);
  });

  test("deterministic", () => {
    expect(sha256Hex("foo")).toBe(sha256Hex("foo"));
  });

  test("different inputs produce different hashes", () => {
    expect(sha256Hex("a")).not.toBe(sha256Hex("b"));
  });
});

// ---------------------------------------------------------------------------
// computeEventHash — excludes event_hash field before hashing
// ---------------------------------------------------------------------------

describe("computeEventHash", () => {
  const baseEnvelope = {
    event_id: "01J00000000000000000000000",
    event_type: "model_call",
    run_id: "run_abc123",
    previous_event_hash: null,
    occurred_at: "2026-05-01T00:00:00Z",
  };

  test("produces sha256: prefixed string", () => {
    const hash = computeEventHash(baseEnvelope);
    expect(hash.startsWith("sha256:")).toBe(true);
  });

  test("deterministic for same input", () => {
    expect(computeEventHash(baseEnvelope)).toBe(computeEventHash({ ...baseEnvelope }));
  });

  test("event_hash field excluded — same result whether present or absent", () => {
    const withHash = { ...baseEnvelope, event_hash: "sha256:someprevioushash" };
    const withoutHash = { ...baseEnvelope };
    expect(computeEventHash(withHash)).toBe(computeEventHash(withoutHash));
  });

  test("different run_ids produce different hashes", () => {
    const a = computeEventHash({ ...baseEnvelope, run_id: "run_aaa" });
    const b = computeEventHash({ ...baseEnvelope, run_id: "run_bbb" });
    expect(a).not.toBe(b);
  });

  test("hash chain — previous_event_hash included in hash", () => {
    const first = computeEventHash(baseEnvelope);
    const second = computeEventHash({ ...baseEnvelope, previous_event_hash: first });
    expect(first).not.toBe(second);
  });

  test("key order does not affect hash (keys sorted by canonicalJson)", () => {
    const orderedA = computeEventHash({ event_type: "x", run_id: "r", previous_event_hash: null, occurred_at: "t", event_id: "e" });
    const orderedB = computeEventHash({ run_id: "r", event_type: "x", event_id: "e", occurred_at: "t", previous_event_hash: null });
    expect(orderedA).toBe(orderedB);
  });
});

// ---------------------------------------------------------------------------
// computePayloadHash
// ---------------------------------------------------------------------------

describe("computePayloadHash", () => {
  test("null payload hashes empty object", () => {
    expect(computePayloadHash(null)).toBe(computePayloadHash({}));
  });

  test("undefined payload hashes empty object", () => {
    expect(computePayloadHash(undefined)).toBe(computePayloadHash({}));
  });

  test("deterministic for same payload", () => {
    const p = { model: "gpt-4o", tokens: 500 };
    expect(computePayloadHash(p)).toBe(computePayloadHash({ ...p }));
  });

  test("key order independent", () => {
    expect(computePayloadHash({ b: 2, a: 1 })).toBe(computePayloadHash({ a: 1, b: 2 }));
  });
});
