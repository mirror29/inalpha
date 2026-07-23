import { readFile } from "node:fs/promises";
import assert from "node:assert/strict";
import test from "node:test";

import {
  LLMS_REQUIRED_LINKS,
  ROBOTS_AGENTS,
  SITEMAP_URLS,
} from "./export-seo-contracts.mjs";

const output = new URL("../out/", import.meta.url);
const homepageSchema = /"@type":"(?:Organization|WebSite|SoftwareSourceCode|FAQPage)"/;

async function readOutput(path) {
  return readFile(new URL(path, output), "utf8");
}

function count(value, pattern) {
  return [...value.matchAll(pattern)].length;
}

test("exports complete homepage metadata and schema", async () => {
  const [english, chinese] = await Promise.all([
    readOutput("en/index.html"),
    readOutput("zh/index.html"),
  ]);

  for (const [html, canonical] of [
    [english, "https://inalpha.dev/"],
    [chinese, "https://inalpha.dev/zh/"],
  ]) {
    assert.match(html, new RegExp(`<link rel="canonical" href="${canonical}"/>`));
    assert.match(html, /hrefLang="en" href="https:\/\/inalpha\.dev\/"/);
    assert.match(html, /hrefLang="zh" href="https:\/\/inalpha\.dev\/zh\/"/);
    assert.match(html, /hrefLang="x-default" href="https:\/\/inalpha\.dev\/"/);
    assert.match(html, /<meta property="og:url" content="https:\/\/inalpha\.dev/);
    assert.match(html, homepageSchema);
    assert.doesNotMatch(html, /SearchAction/);
  }
});

test("keeps homepage schema off root, kit, and legal pages", async () => {
  const pages = await Promise.all([
    readOutput("index.html"),
    readOutput("en/kit/index.html"),
    readOutput("zh/kit/index.html"),
    readOutput("privacy/index.html"),
    readOutput("terms/index.html"),
  ]);

  for (const page of pages) {
    assert.doesNotMatch(page, homepageSchema);
  }
  assert.match(pages[0], /<meta name="robots" content="noindex, nofollow"\/>/);
  for (const kit of pages.slice(1, 3)) {
    assert.match(kit, /<meta name="robots" content="noindex, nofollow"\/>/);
    assert.doesNotMatch(kit, /<link rel="canonical"/);
  }
});

test("exports complete legal metadata and discovery artifacts", async () => {
  const [privacy, terms, sitemap, robots, llms] = await Promise.all([
    readOutput("privacy/index.html"),
    readOutput("terms/index.html"),
    readOutput("sitemap.xml"),
    readOutput("robots.txt"),
    readOutput("llms.txt"),
  ]);

  assert.match(privacy, /<title>Privacy Policy \| Inalpha<\/title>/);
  assert.match(privacy, /<meta name="description" content="How the Inalpha website handles privacy/);
  assert.match(privacy, /<link rel="canonical" href="https:\/\/inalpha\.dev\/privacy\/"\/>/);
  assert.match(terms, /<title>Terms of Service \| Inalpha<\/title>/);
  assert.match(terms, /<meta name="description" content="Terms, financial risk disclosure/);
  assert.match(terms, /<link rel="canonical" href="https:\/\/inalpha\.dev\/terms\/"\/>/);

  assert.equal(count(sitemap, /<loc>/g), 4);
  for (const url of SITEMAP_URLS) {
    assert.match(sitemap, new RegExp(url.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")));
  }
  assert.equal(count(sitemap, /hreflang="en"/g), 2);
  assert.equal(count(sitemap, /hreflang="zh"/g), 2);
  assert.equal(count(sitemap, /hreflang="x-default"/g), 2);
  assert.doesNotMatch(sitemap, /<lastmod>|\/kit\/|https:\/\/inalpha\.dev\/en\//);

  for (const agent of ROBOTS_AGENTS) {
    assert.match(robots, new RegExp(`User-agent: ${agent.replace("*", "\\*")}`));
  }
  assert.match(robots, /Sitemap: https:\/\/inalpha\.dev\/sitemap\.xml/);

  for (const link of LLMS_REQUIRED_LINKS) {
    assert.match(llms, new RegExp(link.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")));
  }
  assert.match(llms, /does not provide live brokerage execution or financial advice/);
  assert.doesNotMatch(llms, /llms-full|\d+ stars/i);
});

test("keeps the current Cloudflare root rewrite for PR1", async () => {
  const redirects = await readOutput("_redirects");

  assert.match(redirects, /^\/\s+\/en\/\s+200$/m);
});
