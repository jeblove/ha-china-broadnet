const { chromium } = require('playwright');
const { resolve } = require('node:path');
const { createInterface } = require('node:readline/promises');
const { setTimeout: wait } = require('node:timers/promises');
const { ORIGIN, observe } = require('./readings.cjs');

const REFRESH_MS = 30 * 60 * 1000;

async function main() {
  const endpoint = new URL(process.env.BROADNET_WEBHOOK_URL);
  if (!['http:', 'https:'].includes(endpoint.protocol)
      || !/^\/api\/webhook\/[a-zA-Z0-9_-]+$/.test(endpoint.pathname)
      || endpoint.username || endpoint.password || endpoint.search || endpoint.hash) {
    throw new Error('Invalid webhook URL');
  }
  const profile = resolve(process.env.BROADNET_BROWSER_PROFILE || '.broadnet-browser');
  const browser = await chromium.launchPersistentContext(profile, {
    headless: false,
    args: ['--restore-last-session'],
  });
  try {
    const page = browser.pages().find(tab => new URL(tab.url()).origin === ORIGIN)
      ?? await browser.newPage();
    if (new URL(page.url()).origin !== ORIGIN) {
      await page.goto(ORIGIN, { waitUntil: 'domcontentloaded', timeout: 20000 });
    }
    const terminal = createInterface({ input: process.stdin, output: process.stdout });
    try {
      await terminal.question('Complete any necessary sign-in in the official browser, then press Enter. ');
    } finally {
      terminal.close();
    }
    for (;;) {
      const reading = await observe(page, process.env.BROADNET_GENERAL_PACKAGE);
      const result = await fetch(endpoint, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(reading),
        redirect: 'error',
        signal: AbortSignal.timeout(10000),
      });
      if (result.status !== 204) throw new Error('Home Assistant did not accept the snapshot');
      console.log(Object.keys(reading.traffic).length
        ? 'Published balance and general data.' : 'Published balance. No matching monthly general package.');
      await wait(REFRESH_MS);
    }
  } finally {
    await browser.close();
  }
}

main().catch(() => {
  console.error('Browser companion stopped. Check the official session and the Home Assistant receiver.');
  process.exitCode = 1;
});
