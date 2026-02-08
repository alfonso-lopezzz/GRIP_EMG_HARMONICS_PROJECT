"""Tests for EMG processing primitives."""

from emg_app.processing_core import EMGProcessor


def test_midi_smoothing_reaches_max_when_above_mvc():
    processor = EMGProcessor()
    processor.midi_norm = 0.25
    result = processor._apply_midi_smoothing(1.5)
    assert result == 1.0


def test_midi_smoothing_reaches_zero_at_rest():
    processor = EMGProcessor()
    processor.midi_norm = 0.75
    result = processor._apply_midi_smoothing(-0.2)
    assert result == 0.0


def test_midi_smoothing_still_blends_within_range():
    processor = EMGProcessor(midi_alpha=0.5)
    processor.midi_norm = 0.2
    result = processor._apply_midi_smoothing(0.8)
    assert result == (0.5 * 0.8) + (0.5 * 0.2)
