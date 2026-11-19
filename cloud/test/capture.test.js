// The same ad gets the same id on the Mac (desk/capture.py) and online. Expected values come from the Python side.
import { test } from "node:test";
import assert from "node:assert/strict";
import { bookmarklet, canonicalId, sourceOf } from "../src/capture.js";

const CASES = [
  ["https://www.kijiji.ca/v-apartments-condos/laval/x/1746375583", "kijiji", "kijiji:1746375583"],
  ["https://www.facebook.com/marketplace/item/123456/", "facebook", "facebook:123456"],
  ["https://rentals.ca/montreal/some-building", "rentalsca", "rentalsca:0ee9ae579309"],
  ["https://www.facebook.com/groups/abc/posts/1", "group", "group:6dbfd1b17829"],
  ["https://example.com/a?b=1#c", "", "other:c4ed1c218d14"],
];

test("source and id match the Mac's", async () => {
  for (const [url, source, id] of CASES) {
    assert.equal(sourceOf(url), source, url);
    assert.equal(await canonicalId(url), id, url);
  }
});

test("bookmarklets point at the desk they came from", () => {
  const b = bookmarklet("// note\nopen('__DESK__#' + x);\n", "https://rentals.zaarour.ca");
  assert.equal(b, "javascript:open('https://rentals.zaarour.ca/capture%23'+%20x);".replace("'+%20x", "'%20+%20x"));
});
