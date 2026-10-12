// Point ProfileService at a guaranteed-absent config before any test runs so
// unit tests never pick up the host's real ~/.config/pyops/config.toml. Tests
// that exercise profiles override PYOPSCTL_CONFIG via setProfileConfig().
import * as os from 'os';
import * as path from 'path';

process.env.PYOPSCTL_CONFIG = path.join(os.tmpdir(), 'pyops-vsix-tests-no-such-config.toml');
delete process.env.PYOPSCTL_PROFILE;
