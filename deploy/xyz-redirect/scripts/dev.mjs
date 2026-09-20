// Runs `wrangler dev` from this project's folder, wherever it's launched from
// (the repo's .claude/launch.json starts it from the repo root).

import { spawn } from 'node:child_process';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const wrangler = path.join(root, 'node_modules', 'wrangler', 'bin', 'wrangler.js');
const args = [wrangler, 'dev', '--port', '8787', '--ip', '127.0.0.1', ...process.argv.slice(2)];

const child = spawn(process.execPath, args, { cwd: root, stdio: 'inherit' });
child.on('exit', (code) => process.exit(code ?? 0));
