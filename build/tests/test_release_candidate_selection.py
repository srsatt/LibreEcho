"""Execute the shipped resolver branches without any GitHub/network operations."""
import os
import hashlib
from pathlib import Path
import subprocess
import tempfile
import textwrap
import unittest

ROOT = Path(__file__).resolve().parents[2]
WORKFLOW = (ROOT / '.github/workflows/build-release.yml').read_text()


def shell_block(start, end):
    return textwrap.dedent(WORKFLOW[WORKFLOW.index(start):WORKFLOW.index(end)])


class CandidateSelectionTests(unittest.TestCase):
    def run_shell(self, block, **overrides):
        env = dict(os.environ, GITHUB_EVENT_NAME='pull_request',
                   GITHUB_BASE_REF='release/0.14.0',
                   GITHUB_HEAD_REF='fix/forward-port-01314',
                   GITHUB_REF='refs/pull/156/merge', GITHUB_REF_NAME='156/merge',
                   GITHUB_REPOSITORY='aslater3/LibreEcho',
                   PR_HEAD_REPOSITORY='aslater3/LibreEcho',
                   OTA_FORMAT_INPUT='v2', OTA_RELEASE_INPUT='')
        env.update(overrides)
        return subprocess.run(['bash', '-eu', '-o', 'pipefail', '-c', block],
                              env=env, text=True, capture_output=True, timeout=5)

    def selection(self, **overrides):
        block = shell_block('          component_ref=main\n', '          for ref_spec in')
        r = self.run_shell(block + '\nprintf "%s\\n" "$platform_ref" "$linux_ref" "$ui_ref"', **overrides)
        self.assertEqual(r.returncode, 0, r.stderr)
        return r.stdout.splitlines()

    def dispatch_ref(self, **overrides):
        block = shell_block('          if [[ "$GITHUB_EVENT_NAME" == pull_request ]]; then',
                            '          BUILD_PURPOSE_INPUT=${BUILD_PURPOSE_INPUT:-}')
        inputs = dict(GITHUB_EVENT_NAME='workflow_dispatch',
                      GITHUB_REF='refs/heads/feature/fork-trust-bootstrap',
                      GITHUB_REPOSITORY='srsatt/LibreEcho',
                      BUILD_PURPOSE_INPUT='sandbox', SANDBOX_SIGNING_INPUT='signed',
                      PLATFORM_SOURCE_INPUT='srsatt/LibreEcho-Platform@' + 'a' * 40,
                      UI_SOURCE_INPUT='srsatt/LibreEcho-UI@' + 'b' * 40,
                      LINUX_COMMIT_INPUT='2da646edaf294ead68123a2450eff49b8084c3ea')
        inputs.update(overrides)
        return self.run_shell(block, **inputs)

    def test_owner_feature_dispatch_reaches_exact_source_validation(self):
        result = self.dispatch_ref()
        self.assertEqual(result.returncode, 0, result.stderr)
        result = self.sources(GITHUB_REF='refs/heads/feature/fork-trust-bootstrap',
                              GITHUB_REF_NAME='feature/fork-trust-bootstrap',
                              FORK_VERSION_INPUT='0.15.0')
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_feature_dispatch_rejects_other_events_owners_lanes_and_missing_pins(self):
        cases = [{'GITHUB_EVENT_NAME': event} for event in ('push', 'schedule', 'workflow_call')]
        cases += [{'GITHUB_REPOSITORY': owner + '/LibreEcho'} for owner in ('aslater3', 'someone')]
        cases += [{'BUILD_PURPOSE_INPUT': purpose} for purpose in ('dev', 'prd', '')]
        cases += [{'SANDBOX_SIGNING_INPUT': mode} for mode in ('unsigned', '')]
        cases += [{name: ''} for name in ('PLATFORM_SOURCE_INPUT', 'UI_SOURCE_INPUT', 'LINUX_COMMIT_INPUT')]
        cases += [{'GITHUB_REF': 'refs/heads/fix/not-an-owner-build'}]
        for overrides in cases:
            with self.subTest(overrides=overrides):
                self.assertNotEqual(self.dispatch_ref(**overrides).returncode, 0)

    def test_regular_dispatch_and_pull_request_ref_policy_is_preserved(self):
        for ref in ('refs/heads/main', 'refs/heads/release/0.15.0'):
            result = self.dispatch_ref(GITHUB_REF=ref, GITHUB_REPOSITORY='aslater3/LibreEcho',
                                       BUILD_PURPOSE_INPUT='sandbox', SANDBOX_SIGNING_INPUT='unsigned',
                                       PLATFORM_SOURCE_INPUT='', UI_SOURCE_INPUT='', LINUX_COMMIT_INPUT='')
            self.assertEqual(result.returncode, 0, result.stderr)
        result = self.dispatch_ref(GITHUB_EVENT_NAME='pull_request', GITHUB_BASE_REF='main')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertNotEqual(self.dispatch_ref(GITHUB_EVENT_NAME='pull_request',
                                             GITHUB_BASE_REF='feature/fork-trust-bootstrap').returncode, 0)

    def test_same_owner_coordinated_014_candidates(self):
        self.assertEqual(self.selection(), ['fix/forward-port-01314'] * 3)

    def test_fork_and_normal_pr_use_release_components(self):
        self.assertEqual(self.selection(PR_HEAD_REPOSITORY='fork/LibreEcho'), ['release/0.14.0'] * 3)
        self.assertEqual(self.selection(GITHUB_HEAD_REF='fix/ordinary'), ['release/0.14.0'] * 3)
        self.assertEqual(self.selection(GITHUB_BASE_REF='release/0.13.15'), ['release/0.13.15'] * 3)

    def test_existing_coordinated_feature_lane_preserved(self):
        self.assertEqual(self.selection(GITHUB_HEAD_REF='feature/example-product'),
                         ['feature/example-platform', 'release/0.14.0', 'feature/example-ui'])
        self.assertEqual(self.selection(GITHUB_HEAD_REF='feature/example-product',
                                        PR_HEAD_REPOSITORY='fork/LibreEcho'), ['release/0.14.0'] * 3)

    def test_release_dispatch_and_push_never_use_fix_refs(self):
        for event in ('push', 'workflow_dispatch'):
            self.assertEqual(self.selection(GITHUB_EVENT_NAME=event,
                GITHUB_REF='refs/heads/release/0.14.0', GITHUB_REF_NAME='release/0.14.0'),
                ['release/0.14.0'] * 3)
        self.assertEqual(self.selection(GITHUB_EVENT_NAME='push', GITHUB_REF='refs/heads/main',
                                        GITHUB_REF_NAME='main'), ['main'] * 3)

    def sources(self, **overrides):
        block = shell_block('          component_ref=main\n',
                            '          python3 build/ci/resolve-source-set.py')
        # Pinned source selection must not consult a branch tip or the network.
        stub = 'git() { echo unexpected-git >&2; return 1; }\n'
        inputs = dict(GITHUB_EVENT_NAME='workflow_dispatch', GITHUB_REF='refs/heads/main',
                      GITHUB_REF_NAME='main', GITHUB_REPOSITORY='srsatt/LibreEcho',
                      PURPOSE='sandbox', SIGNING_MODE='local', GITHUB_OUTPUT='/dev/null',
                      FORK_VERSION_INPUT='1.0.0',
                      PLATFORM_SOURCE_INPUT='aslater3/LibreEcho-Platform@' + 'a' * 40,
                      LINUX_COMMIT_INPUT='2da646edaf294ead68123a2450eff49b8084c3ea',
                      UI_SOURCE_INPUT='srsatt/LibreEcho-UI@' + 'b' * 40)
        inputs.update(overrides)
        return self.run_shell(stub + block + '\nprintf "%s\\n" "$p" "$l" "$u" "$platform_repository" "$ui_repository"',
                              **inputs)

    def test_owner_sandbox_pins_exact_sources_without_branch_lookup(self):
        result = self.sources()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.splitlines(),
                         ['a' * 40, '2da646edaf294ead68123a2450eff49b8084c3ea', 'b' * 40,
                          'aslater3/LibreEcho-Platform', 'srsatt/LibreEcho-UI'])
        inputs = WORKFLOW.split('    inputs:\n', 1)[1].split('\npermissions:', 1)[0]
        self.assertEqual(inputs.count('        type: string'), 6)
        self.assertIn('repository: ${{ needs.resolve-and-preflight.outputs.platform_repository }}', WORKFLOW)
        self.assertIn('repository: ${{ needs.resolve-and-preflight.outputs.ui_repository }}', WORKFLOW)
        self.assertIn('repository: ${{ github.repository }}', WORKFLOW)

    def test_owner_sources_reject_invalid_or_unsafe_inputs(self):
        for overrides in ({'PLATFORM_SOURCE_INPUT': 'srsatt/LibreEcho-Platform@' + 'A' * 40},
                          {'UI_SOURCE_INPUT': 'someone/LibreEcho-UI@' + 'b' * 40},
                          {'LINUX_COMMIT_INPUT': 'main'},
                          {'LINUX_COMMIT_INPUT': '2' * 40},
                          {'GITHUB_EVENT_NAME': 'push'},
                          {'PURPOSE': 'dev'},
                          {'SIGNING_MODE': 'github'},
                          {'FORK_VERSION_INPUT': ''},
                          {'FORK_VERSION_INPUT': '0.14.4'},
                          {'GITHUB_REPOSITORY': 'aslater3/LibreEcho'}):
            with self.subTest(overrides=overrides):
                result = self.sources(**overrides)
                self.assertNotEqual(result.returncode, 0)
                self.assertNotIn('unexpected-git', result.stderr)

    def test_fork_builder_and_independent_verifier_receive_same_trust_policy(self):
        build = (ROOT / 'build/build.sh').read_text()
        self.assertIn('fork_image_args=(--release-repository srsatt)', build)
        self.assertIn('fork_verifier_args=(--expected-release-repository srsatt)', build)
        self.assertIn('[[ "$TARGET" != biscuit ]] || fork_image_args+=(--legacy-ota-public-key "$OTA_LEGACY_PUBLIC_KEY")', build)
        self.assertIn('[[ "$TARGET" != biscuit ]] || fork_verifier_args+=(--expected-legacy-ota-public-key-sha256 524c2b6343438b6ccffca8a8c7f0b9f36e6ec9a2ee37424364b6bfeef96d7f40)', build)
        self.assertIn('  "${fork_image_args[@]}" \\\n', build)
        self.assertIn('  "${fork_verifier_args[@]}" \\\n', build)

    def test_release_artifact_keeps_the_builder_selected_fork_public_key(self):
        step = WORKFLOW.split('      - name: Record release request and public OTA key', 1)[1]
        start = step.index('          for run in "${runs[@]}"; do')
        end = step.index('          python3 - "$run/release-request.json"', start)
        script = 'runs=("$1")\n' + textwrap.dedent(step[start:end])
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            run = root / 'run'
            stock = root / 'sources/platform/tools/mt8163-arm32/ota/ota-public-key.hex'
            run.mkdir()
            stock.parent.mkdir(parents=True)
            stock.write_bytes(b'stock-public-key\n')
            fork = run / 'ota-public-key.hex'
            fork.write_bytes(b'fork-public-key\n')
            expected = hashlib.sha256(fork.read_bytes()).hexdigest()
            env = dict(os.environ, EXPECTED_OTA_PUBLIC_KEY_SHA256=expected,
                       GITHUB_WORKSPACE=str(root))
            command = ['bash', '-eu', '-o', 'pipefail', '-c', script, 'test', str(run)]
            result = subprocess.run(command, env=env, text=True, capture_output=True, timeout=5)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(fork.read_bytes(), b'fork-public-key\n')
            env['EXPECTED_OTA_PUBLIC_KEY_SHA256'] = hashlib.sha256(stock.read_bytes()).hexdigest()
            result = subprocess.run(command, env=env, text=True, capture_output=True, timeout=5)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn('differs from protected fingerprint', result.stderr)

    def derivation(self, **overrides):
        import tempfile
        block = shell_block('          # Resolve the target version', '          case "$SSH_ENABLED_INPUT"')
        with tempfile.TemporaryDirectory() as directory:
            return self.run_shell(block + '\nprintf "%s\n" "$OTA_RELEASE_INPUT"', GITHUB_OUTPUT=str(Path(directory) / 'outputs'), OTA_FORMAT_INPUT='v3', **overrides)

    def test_v3_derives_branch_version_and_preserves_explicit_input(self):
        r = self.derivation(GITHUB_BASE_REF='release/0.15.0')
        self.assertEqual((r.returncode, r.stdout.strip()), (0, '0.15.0'))
        r = self.derivation(OTA_RELEASE_INPUT='0.14.0')
        self.assertEqual((r.returncode, r.stdout.strip()), (0, '0.14.0'))

    def test_v3_main_default_and_invalid_version_refusal(self):
        r = self.derivation(GITHUB_BASE_REF='', GITHUB_REF_NAME='main')
        self.assertEqual((r.returncode, r.stdout.strip()), (0, '0.14.0'))
        r = self.derivation(OTA_RELEASE_INPUT='invalid')
        self.assertNotEqual(r.returncode, 0)
        self.assertIn('invalid target version', r.stderr)

    def test_dispatch_default_and_resolved_contract_identity(self):
        inputs = WORKFLOW.split('      ota_format:', 1)[1].split('      ota_release:', 1)[0]
        self.assertIn('default: v3', inputs)
        self.assertIn('options: [v3]', inputs)
        self.assertIn("OTA_FORMAT_INPUT: ${{ inputs.ota_format || 'v3' }}", WORKFLOW)
        contracts = WORKFLOW.split('  contract-checks:', 1)[1].split('  prepare-public-inputs:', 1)[0]
        self.assertIn('needs: resolve-and-preflight', contracts)
        self.assertIn('ref: ${{ needs.resolve-and-preflight.outputs.platform_sha }}', contracts)


if __name__ == '__main__':
    unittest.main()
