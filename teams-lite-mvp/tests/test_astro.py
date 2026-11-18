import unittest

from teams_lite.astro import AstroMachine, AstroState


class Clock:
    def __init__(self):
        self.now = 100.0

    def __call__(self):
        return self.now


class AstroMachineTests(unittest.TestCase):
    def setUp(self):
        self.clock = Clock()
        self.changes = []
        self.astro = AstroMachine(clock=self.clock, on_change=lambda: self.changes.append(self.astro.state))

    def wait(self, seconds):
        self.clock.now += seconds

    def test_waits_idle_until_a_real_event(self):
        self.assertIs(self.astro.state, AstroState.IDLE)
        self.assertEqual(self.astro.message, 'Listo para comenzar')
        self.wait(3600)
        self.assertIs(self.astro.state, AstroState.IDLE)  # nothing is simulated
        self.assertEqual(self.changes, [])

    def test_recording_then_stop_with_transcription_ends_in_success(self):
        astro = self.astro
        astro.recording_started()
        self.assertIs(astro.state, AstroState.RECORDING)
        self.assertEqual(astro.message, 'Grabando reunión')
        self.wait(600)
        self.assertIs(astro.state, AstroState.RECORDING)  # stays while recording

        astro.stop_requested()
        self.assertIs(astro.state, AstroState.STOPPING)
        astro.recording_stopped(processing=True)
        self.assertIs(astro.state, AstroState.PROCESSING)
        self.assertEqual(astro.message, 'Procesando grabación')
        astro.processing_progress(0.456)
        self.assertEqual(astro.message, 'Procesando grabación · 46 %')

        astro.processing_finished()
        self.assertIs(astro.state, AstroState.SUCCESS)
        self.assertEqual(astro.message, '¡Reunión guardada!')
        self.wait(5)
        self.assertIs(astro.state, AstroState.IDLE)
        self.assertEqual(self.changes, [AstroState.RECORDING, AstroState.STOPPING, AstroState.PROCESSING,
                                        AstroState.PROCESSING, AstroState.SUCCESS])

    def test_stop_without_transcription_confirms_the_save(self):
        self.astro.recording_started()
        self.astro.stop_requested()
        self.astro.recording_stopped(processing=False)
        self.assertIs(self.astro.state, AstroState.SUCCESS)
        self.assertEqual(self.astro.message, '¡Reunión guardada!')

    def test_capture_flashes_and_returns_to_recording(self):
        self.astro.recording_started()
        self.astro.capture_finished(True)
        self.assertIs(self.astro.state, AstroState.CAPTURING)
        self.assertEqual(self.astro.message, 'Captura realizada')
        self.wait(1)
        self.assertIs(self.astro.state, AstroState.CAPTURING)
        self.wait(2)
        self.assertIs(self.astro.state, AstroState.RECORDING)

    def test_a_successful_capture_can_say_what_it_captured(self):
        self.astro.recording_started()
        self.astro.capture_finished(True, 'OCR: 3 asistentes')
        self.assertIs(self.astro.state, AstroState.CAPTURING)
        self.assertEqual(self.astro.message, 'OCR: 3 asistentes')
        self.astro.capture_finished(True)
        self.assertEqual(self.astro.message, 'Captura realizada')

    def test_errors_show_a_readable_message_and_never_end_the_recording(self):
        astro = self.astro
        astro.recording_started()
        astro.capture_finished(False, 'Teams está minimizado: restaura su ventana.')
        self.assertIs(astro.state, AstroState.ERROR)
        self.assertEqual(astro.message, 'Teams está minimizado: restaura su ventana.')
        self.wait(10)
        self.assertIs(astro.state, AstroState.RECORDING)  # the recording carries on

        astro.error('')
        self.assertEqual(astro.message, 'Algo salió mal.')

    def test_failed_processing_ends_idle_after_the_error(self):
        self.astro.recording_started()
        self.astro.stop_requested()
        self.astro.recording_stopped(processing=True)
        self.astro.processing_finished(error='No se encontró el modelo de transcripción.')
        self.assertIs(self.astro.state, AstroState.ERROR)
        self.wait(10)
        self.assertIs(self.astro.state, AstroState.IDLE)

    def test_moving_lasts_while_dragged_then_returns_to_the_current_state(self):
        self.astro.recording_started()
        self.astro.move_started()
        self.assertIs(self.astro.state, AstroState.MOVING)
        self.wait(30)
        self.assertIs(self.astro.state, AstroState.MOVING)
        self.astro.move_finished()
        self.assertIs(self.astro.state, AstroState.RECORDING)

    def test_a_new_recording_during_processing_keeps_both_visible(self):
        astro = self.astro
        astro.recording_started()
        astro.stop_requested()
        astro.recording_stopped(processing=True)
        astro.recording_started()  # the next meeting starts while the previous one is transcribed
        self.assertIs(astro.state, AstroState.RECORDING)
        astro.processing_finished()
        self.assertIs(astro.state, AstroState.SUCCESS)
        self.wait(5)
        self.assertIs(astro.state, AstroState.RECORDING)


if __name__ == '__main__':
    unittest.main()
