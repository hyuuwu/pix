import unittest

from pix.mapping import build_aliases, identity, source_name


def package(distro, name, origin='example', homepage='https://example.org/project'):
    return dict(distro=distro, name=name, origin=origin, homepage=homepage)


class Mapping(unittest.TestCase):
    def test_development_suffixes(self):
        aliases, rejected = build_aliases([
            package('debian', 'example-dev'), package('fedora', 'example-devel', 'example-1.2-4.fc44.src.rpm')])
        self.assertEqual(len(aliases), 2)
        self.assertEqual(aliases[0][2], aliases[1][2])
        self.assertEqual(rejected, 0)

    def test_python_module_prefix(self):
        aliases, _ = build_aliases([
            package('debian', 'python3-example', 'python-example'),
            package('arch', 'python-example', 'python-example')])
        self.assertEqual(len(aliases), 2)

    def test_runtime_and_headers_stay_separate(self):
        aliases, _ = build_aliases([
            package('arch', 'example'), package('fedora', 'example-devel')])
        self.assertEqual(aliases, [])

    def test_shared_homepage_is_not_enough(self):
        aliases, _ = build_aliases([
            package('arch', 'example-client'), package('fedora', 'example-server')])
        self.assertEqual(aliases, [])

    def test_abi_and_python_major_versions_are_preserved(self):
        self.assertNotEqual(identity('libexample1'), identity('libexample2'))
        self.assertNotEqual(identity('python2-example'), identity('python3-example'))

    def test_competing_names_in_one_distro_are_rejected(self):
        aliases, rejected = build_aliases([
            package('arch', 'example-dev'), package('fedora', 'example-devel'),
            package('fedora', 'example-dev')])
        self.assertEqual(aliases, [])
        self.assertEqual(rejected, 1)

    def test_curated_entries_are_preserved(self):
        aliases, _ = build_aliases([
            package('arch', 'example-dev'), package('fedora', 'example-devel')],
            {('arch', 'example-dev')})
        self.assertEqual(aliases, [])

    def test_missing_identity_cannot_join_packages(self):
        aliases, _ = build_aliases([
            package('arch', 'example-dev', '', ''), package('fedora', 'example-devel', '', '')])
        self.assertEqual(aliases, [])

    def test_source_version_parsing(self):
        self.assertEqual(source_name('example-2.1-4.fc44.src.rpm'), 'example')
        self.assertEqual(source_name('example-2.1_1'), 'example')
        self.assertEqual(source_name('example'), 'example')

    def test_group_ids_are_deterministic(self):
        rows = [package('arch', 'example-dev'), package('fedora', 'example-devel')]
        self.assertEqual(build_aliases(rows), build_aliases(reversed(rows)))
