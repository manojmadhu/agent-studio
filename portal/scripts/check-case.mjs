// Fails when the source tree would break on case-insensitive file systems (Windows, default macOS):
//  1. two files in one folder whose names differ only by letter case or extension (e.g. Menu.tsx + menu.ts), and
//  2. a relative import whose spelling does not match the file's real casing.
import { readdirSync, readFileSync, statSync } from 'node:fs';
import { join, dirname, resolve, relative, basename } from 'node:path';

const root = resolve('src');
const exts = ['.d.ts', '.ts', '.tsx', '.js', '.jsx', '.mjs', '.css', '.json'];
const problems = [];
const files = [];

function walk(dir) {
  const seen = new Map();
  for (const name of readdirSync(dir)) {
    const full = join(dir, name);
    if (statSync(full).isDirectory()) { walk(full); continue; }
    files.push(full);
    // module key: name without extension (and without .d for .d.ts), lower-cased
    const key = name.replace(/\.d\.ts$|\.[^.]+$/, '').toLowerCase();
    if (seen.has(key)) problems.push(`case/extension clash: ${relative('.', join(dir, seen.get(key)))} vs ${relative('.', full)}`);
    else seen.set(key, name);
  }
}
walk(root);

const importRe = /(?:import|export)[^'"]*?from\s*['"](\.{1,2}\/[^'"]+)['"]|import\s*\(\s*['"](\.{1,2}\/[^'"]+)['"]\s*\)|import\s+['"](\.{1,2}\/[^'"]+)['"]/g;
for (const file of files.filter((f) => /\.(tsx?|jsx?|mjs)$/.test(f))) {
  const src = readFileSync(file, 'utf8');
  for (const m of src.matchAll(importRe)) {
    const spec = m[1] || m[2] || m[3];
    const target = resolve(dirname(file), spec);
    const dir = dirname(target);
    let entries;
    try { entries = readdirSync(dir); } catch { problems.push(`${relative('.', file)}: cannot resolve folder for '${spec}'`); continue; }
    const want = basename(target);
    const candidates = [want, ...exts.map((e) => want + e)];
    const exact = candidates.some((c) => entries.includes(c)) ||
      (entries.includes(want) && statSync(target).isDirectory());
    if (!exact) {
      const lower = candidates.map((c) => c.toLowerCase());
      const near = entries.find((e) => lower.includes(e.toLowerCase()));
      problems.push(`${relative('.', file)}: import '${spec}' ${near ? `differs in case from '${near}'` : 'not found'}`);
    }
  }
}

if (problems.length) {
  console.error('Case-sensitivity check failed:\n  ' + problems.join('\n  '));
  process.exit(1);
}
console.log(`Case-sensitivity check passed (${files.length} files).`);
