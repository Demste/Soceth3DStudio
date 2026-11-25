const fs = require('node:fs');
const lock = JSON.parse(fs.readFileSync('package-lock.json', 'utf8'));
for (const [path, expected] of Object.entries(lock.packages)) {
  if (!path || expected.optional) continue;
  try {
    if (JSON.parse(fs.readFileSync(path + '/package.json', 'utf8')).version !== expected.version) process.exit(1);
  } catch {
    process.exit(1);
  }
}
