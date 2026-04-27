import pytest

from app.state import AppStatus, StateMachine


def test_happy_path_transitions() -> None:
    state = StateMachine()

    state.transition(AppStatus.RECORDING)
    state.transition(AppStatus.SILENCE_COUNTDOWN)
    state.transition(AppStatus.RECORDING)
    state.transition(AppStatus.PROCESSING_TRANSCRIPTION)
    state.transition(AppStatus.PROCESSING_FORMATTING)
    state.transition(AppStatus.COPIED)
    state.transition(AppStatus.IDLE)

    assert state.current == AppStatus.IDLE


def test_bugreel_processing_path_transitions() -> None:
    state = StateMachine()

    state.transition(AppStatus.AWAITING_BUGREEL_UPLOAD)
    state.transition(AppStatus.AWAITING_BUGREEL_PUBLICATION)
    state.transition(AppStatus.PROCESSING_BUGREEL_ASSETS)
    state.transition(AppStatus.PROCESSING_BUGREEL_FALLBACK)
    state.transition(AppStatus.PROCESSING_FORMATTING)
    state.transition(AppStatus.COPIED)

    assert state.current == AppStatus.COPIED


def test_invalid_transition_raises() -> None:
    state = StateMachine()

    with pytest.raises(ValueError):
        state.transition(AppStatus.COPIED)


def test_error_state_can_reset() -> None:
    state = StateMachine()

    state.transition(AppStatus.RECORDING)
    state.transition(AppStatus.ERROR, error_message="falha")
    state.reset()

    assert state.current == AppStatus.IDLE
    assert state.last_error is None


def test_recording_can_return_to_idle_for_discard() -> None:
    state = StateMachine()

    state.transition(AppStatus.RECORDING)
    state.transition(AppStatus.IDLE)

    assert state.current == AppStatus.IDLE
