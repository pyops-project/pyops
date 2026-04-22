import * as fs from 'fs';
import * as path from 'path';

describe('package version', () => {
  it('matches the project root VERSION file', () => {
    const versionPath = path.resolve(__dirname, '..', '..', 'VERSION');
    const versionFile = fs.readFileSync(versionPath, 'utf8').trim();
    // eslint-disable-next-line @typescript-eslint/no-var-requires
    const pkg = require('../package.json');
    expect(pkg.version).toBe(versionFile);
  });
});
