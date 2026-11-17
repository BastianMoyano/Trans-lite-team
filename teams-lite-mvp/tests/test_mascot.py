import json
import tempfile
import unittest
from pathlib import Path

from teams_lite.mascot import (
    DEFAULT_TITLE,
    MascotSettings,
    load_mascot_settings,
    resolve_placement,
    save_mascot_settings,
)
from teams_lite.storage import Storage


class MascotSettingsTests(unittest.TestCase):
    def test_missing_file_gives_the_defaults(self):
        with tempfile.TemporaryDirectory() as d:
            settings = load_mascot_settings(d)
            self.assertEqual(settings, MascotSettings())
            self.assertTrue(settings.enabled)
            self.assertEqual(settings.title, DEFAULT_TITLE)
            self.assertIsNone(settings.organization_id)
            self.assertIsNone(settings.position)

    def test_settings_survive_a_save_and_load(self):
        with tempfile.TemporaryDirectory() as d:
            saved = MascotSettings(enabled=False, title='Daily ventas', organization_id='org',
                                   project_id='proj', position=(120, 640))
            save_mascot_settings(d, saved)

            self.assertEqual(load_mascot_settings(d), saved)
            self.assertEqual([p.name for p in Path(d).iterdir()], ['mascot.json'])  # no temporary file left

    def test_damaged_or_odd_values_fall_back_field_by_field(self):
        with tempfile.TemporaryDirectory() as d:
            (Path(d) / 'mascot.json').write_text('not json', encoding='utf-8')
            self.assertEqual(load_mascot_settings(d), MascotSettings())

            (Path(d) / 'mascot.json').write_text(json.dumps({
                'enabled': 'yes', 'title': '   ', 'organization_id': 7, 'project_id': 'proj',
                'position': ['a', 2],
            }), encoding='utf-8')
            settings = load_mascot_settings(d)
            self.assertTrue(settings.enabled)
            self.assertEqual(settings.title, DEFAULT_TITLE)
            self.assertIsNone(settings.organization_id)
            self.assertIsNone(settings.project_id)  # a project only makes sense inside its organization
            self.assertIsNone(settings.position)


class ResolvePlacementTests(unittest.TestCase):
    def test_existing_defaults_are_used_and_missing_ones_degrade_with_a_reason(self):
        with tempfile.TemporaryDirectory() as d:
            store = Storage(d)
            zenith = store.create_organization('Zenith')
            crm = store.create_project(zenith, 'CRM')

            self.assertEqual(resolve_placement(store, MascotSettings()), (None, None, None))
            self.assertEqual(resolve_placement(store, MascotSettings(organization_id=zenith, project_id=crm)),
                             (zenith, crm, None))
            self.assertEqual(resolve_placement(store, MascotSettings(organization_id=zenith)), (zenith, None, None))

            org, project, problem = resolve_placement(store, MascotSettings(organization_id=zenith, project_id='gone'))
            self.assertEqual((org, project), (zenith, None))
            self.assertIn('proyecto por defecto ya no existe', problem)

            org, project, problem = resolve_placement(store, MascotSettings(organization_id='gone', project_id=crm))
            self.assertEqual((org, project), (None, None))
            self.assertIn('Cofre de reuniones', problem)


if __name__ == '__main__':
    unittest.main()
