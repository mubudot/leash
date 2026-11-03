#!/usr/bin/env node
// Buffer the standard-JSON stream; solcjs's CLI can truncate piped output.
const fs = require('node:fs');
const solc = require('solc');
if (process.argv.includes('--version')) {
  fs.writeFileSync(1, solc.version() + '\n');
} else if (process.argv.includes('--standard-json')) {
  fs.writeFileSync(1, solc.compile(fs.readFileSync(0, 'utf8')));
} else {
  throw new Error('Expected --version or --standard-json');
}
