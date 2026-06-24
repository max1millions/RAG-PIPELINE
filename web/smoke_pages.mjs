/**
 * HTTP smoke tests against local web stack (BASE_URL).
 * Paths are read from SMOKE_PATHS env var (JSON array of {path, name} objects)
 * or fall back to a single health check on "/".
 *
 * Set by web/local.py from web_local.yaml health_paths.
 */
import { chromium } from 'playwright';

const base = (process.env.BASE_URL || 'http://127.0.0.1:8080').replace(/\/$/, '');

let paths = [{ path: '/', name: 'root' }];
if (process.env.SMOKE_PATHS) {
    try {
        const raw = JSON.parse(process.env.SMOKE_PATHS);
        if (Array.isArray(raw) && raw.length > 0) {
            paths = raw.map((p, i) =>
                typeof p === 'string'
                    ? { path: p, name: p.replace(/[^a-z0-9]/gi, '_') || `path_${i}` }
                    : p
            );
        }
    } catch (_) {
        // fall back to default
    }
}

const browser = await chromium.launch({ headless: true });
const page = await browser.newPage();

let failures = 0;

console.log(`Smoke pages (BASE_URL=${base})\n`);

for (const { path, name } of paths) {
    const url = `${base}${path}`;
    try {
        const response = await page.goto(url, { waitUntil: 'domcontentloaded', timeout: 30000 });
        const status = response?.status() ?? 0;
        if (status >= 200 && status < 400) {
            console.log(`OK   ${name} ${status} ${url}`);
        } else {
            console.log(`FAIL ${name} HTTP ${status} ${url}`);
            failures += 1;
        }
    } catch (err) {
        console.log(`FAIL ${name} ${url}: ${err.message}`);
        failures += 1;
    }
}

await browser.close();

if (failures > 0) {
    console.error(`\n${failures} smoke check(s) failed`);
    process.exit(1);
}

console.log('\nAll smoke checks passed');
