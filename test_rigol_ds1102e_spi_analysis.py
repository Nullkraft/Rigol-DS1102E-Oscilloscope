import unittest

from rigol_ds1102e_spi_analysis import (
    decode_spi_data_words,
    detect_rising_edge_sample_indexes,
)


EXPECTED_WORD = 0x001B8000


def synthetic_spi_capture(word=EXPECTED_WORD, amplitude=60, leading_partial_pulse=False):
    clock = []
    data = []

    if leading_partial_pulse:
        clock.extend([amplitude, amplitude, amplitude, amplitude // 6])
        data.extend([0, 0, 0, 0])

    idle = [0, amplitude // 5, amplitude // 10, amplitude // 4, 0]
    pulse = [
        amplitude // 6,
        amplitude // 3,
        (amplitude * 3) // 4,
        amplitude,
        amplitude,
        (amplitude * 4) // 5,
        amplitude // 2,
        amplitude // 6,
    ]

    clock.extend(idle)
    data.extend([0] * len(idle))
    for bit_index in range(31, -1, -1):
        bit = (word >> bit_index) & 1
        clock.extend(pulse)
        data.extend([amplitude if bit else 0] * len(pulse))
        clock.extend(idle)
        data.extend([amplitude if bit else 0] * len(idle))

    return clock, data


class HysteresisClockDetectorCase(unittest.TestCase):
    def test_detects_one_edge_per_clock_pulse(self):
        clock, _ = synthetic_spi_capture()

        indexes = detect_rising_edge_sample_indexes(clock)

        self.assertEqual(len(indexes), 32)

    def test_tolerates_clock_amplitude_changes(self):
        for amplitude in (30, 60, 100, 180):
            with self.subTest(amplitude=amplitude):
                clock, _ = synthetic_spi_capture(amplitude=amplitude)

                indexes = detect_rising_edge_sample_indexes(clock)

                self.assertEqual(len(indexes), 32)

    def test_ignores_noise_and_flat_tops(self):
        clock, _ = synthetic_spi_capture()

        indexes = detect_rising_edge_sample_indexes(clock)

        self.assertEqual(len(indexes), 32)

    def test_ignores_partial_pulse_at_start_of_capture(self):
        clock, _ = synthetic_spi_capture(leading_partial_pulse=True)

        indexes = detect_rising_edge_sample_indexes(clock)

        self.assertEqual(len(indexes), 32)

    def test_detected_edges_decode_expected_word(self):
        clock, data = synthetic_spi_capture()
        indexes = detect_rising_edge_sample_indexes(clock)

        decoded = decode_spi_data_words(data, indexes)

        self.assertEqual(decoded["decoded_words"][0]["hex"], "0x001B8000")


if __name__ == "__main__":
    unittest.main()
