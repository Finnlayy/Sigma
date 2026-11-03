const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const ts = require('typescript');

test('React hooks are not called from render helpers or async event/load functions', () => {
  const violations = [];
  function scan(directory) {
    for (const entry of fs.readdirSync(directory, { withFileTypes: true })) {
      const file = path.join(directory, entry.name);
      if (entry.isDirectory()) { scan(file); continue; }
      if (!file.endsWith('.tsx')) continue;
      const source = ts.createSourceFile(file, fs.readFileSync(file, 'utf8'), ts.ScriptTarget.Latest, true, ts.ScriptKind.TSX);
      function visit(node) {
        if (ts.isCallExpression(node) && /^use[A-Z]/.test(node.expression.getText(source).split('.').at(-1))) {
          let owner = node.parent;
          while (owner && !ts.isArrowFunction(owner) && !ts.isFunctionDeclaration(owner) && !ts.isFunctionExpression(owner)) owner = owner.parent;
          if (owner) {
            const name = owner.name?.getText(source) || (ts.isVariableDeclaration(owner.parent) ? owner.parent.name.getText(source) : null);
            if (name && /^[a-z]/.test(name) && !/^use[A-Z]/.test(name)) {
              violations.push(`${file}:${source.getLineAndCharacterOfPosition(node.pos).line + 1} ${name}`);
            }
          }
        }
        ts.forEachChild(node, visit);
      }
      visit(source);
    }
  }
  scan('src');
  assert.deepEqual(violations, []);
});

test('calendar does not invent ledger history or a live daemon on API failure', () => {
  const source = fs.readFileSync('src/components/CalendarHeatmap.tsx', 'utf8');
  assert.ok(!source.includes('const mockData'));
  assert.ok(!source.includes('Daemon Live'));
  assert.ok(source.includes('Historical P&L unavailable'));
});
