"""Tests for AudioDeviceService."""

from reachy_mini_conversation_app.audio_service import AudioDeviceService


def test_audio_device_service_singleton() -> None:
    """Verify AudioDeviceService returns a singleton instance."""
    s1 = AudioDeviceService.get_instance()
    s2 = AudioDeviceService.get_instance()
    assert s1 is s2


def test_list_devices_contains_auto() -> None:
    """Verify list_devices includes the auto device option."""
    service = AudioDeviceService()
    devices = service.list_devices()
    assert len(devices) >= 1
    assert devices[0]["id"] == "auto"
    assert "기본 마이크" in devices[0]["name"]


def test_select_device_auto() -> None:
    """Verify selecting auto device works and reports auto active."""
    service = AudioDeviceService()
    ok = service.select_device("auto")
    assert ok is True
    assert service.get_active_device_id() == "auto"
    assert service.get_audio_sample() is None


def test_select_device_invalid_fallback() -> None:
    """Verify selecting an invalid device id falls back to auto gracefully."""
    service = AudioDeviceService()
    service.select_device("invalid_device_identifier_xyz")
    assert service.get_active_device_id() == "auto"


def test_close_cleans_up() -> None:
    """Verify close() runs safely without throwing."""
    service = AudioDeviceService()
    service.close()
    assert service.get_active_device_id() == "auto"


def test_noise_gate_threshold_get_set() -> None:
    """Verify setting and clamping noise gate threshold."""
    service = AudioDeviceService()
    assert service.get_noise_gate_threshold() == 0.0
    service.set_noise_gate_threshold(0.25)
    assert service.get_noise_gate_threshold() == 0.25
    service.set_noise_gate_threshold(-0.1)
    assert service.get_noise_gate_threshold() == 0.0
    service.set_noise_gate_threshold(1.5)
    assert service.get_noise_gate_threshold() == 1.0


def test_process_noise_gate() -> None:
    """Verify process_noise_gate gates low amplitude audio and passes loud audio."""
    import numpy as np

    service = AudioDeviceService()
    service.set_noise_gate_threshold(0.20)

    # Low amplitude noise chunk (RMS ~ 0.005, normalized ~ 0.03 < 0.20)
    noise = np.full((160, 2), 0.005, dtype=np.float32)
    processed, is_gated = service.process_noise_gate(noise)
    assert is_gated is True
    assert np.all(processed == 0)

    # Loud speech chunk (RMS ~ 0.1, normalized ~ 0.60 >= 0.20)
    speech = np.full((160, 2), 0.1, dtype=np.float32)
    processed_speech, is_gated_speech = service.process_noise_gate(speech)
    assert is_gated_speech is False
    assert np.array_equal(processed_speech, speech)

    # Threshold set to 0.0 (OFF) disables gating
    service.set_noise_gate_threshold(0.0)
    processed_off, is_gated_off = service.process_noise_gate(noise)
    assert is_gated_off is False
    assert np.array_equal(processed_off, noise)
