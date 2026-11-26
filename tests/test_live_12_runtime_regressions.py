"""Regressions found by running the Remote Script against Ableton Live 12.4.

The shared fakes in ``test_live_backend`` are permissive where Live is strict:
Live raises ``RuntimeError`` (not ``AttributeError``) for properties a track
kind does not have, ``Clip.apply_note_modifications`` only accepts the
``MidiNoteVector`` returned by ``get_notes_extended``, undo/redo live on
``Song``, and warp markers are ``WarpMarker`` objects. These fakes model that
strictness so the fixes stay covered without a running Live.
"""

from __future__ import annotations

import json
import sys
import types
from dataclasses import dataclass
from typing import Any

import pytest
from test_live_backend import _Clip, _Device, _Parameter, _SurfaceStub, _Track

from remote_script.AbletonCliRemote.command_backend import CommandError, dispatch_command
from remote_script.AbletonCliRemote.live_backend import LiveBackend


class _GroupTrack(_Track):
    """A group track: Live raises RuntimeError for arm and arrangement_clips."""

    can_be_armed = False
    is_foldable = True

    @property
    def arm(self) -> bool:  # type: ignore[override]
        raise RuntimeError("Main, Group and Return Tracks have no 'Arm' state!")

    @arm.setter
    def arm(self, _value: bool) -> None:
        pass

    @property
    def arrangement_clips(self) -> list[Any]:  # type: ignore[override]
        raise RuntimeError("Main, Group and Return Tracks have no arrangement clips")

    @arrangement_clips.setter
    def arrangement_clips(self, _value: list[Any]) -> None:
        pass


def _group_backend() -> LiveBackend:
    backend = LiveBackend(_SurfaceStub())
    song = backend.context._song()
    song.tracks.insert(0, _GroupTrack(name="DRUMS", has_audio_input=True, has_midi_input=False))
    return backend


def test_tracks_list_and_track_info_tolerate_group_tracks() -> None:
    backend = _group_backend()

    listed = backend.tracks_list()["tracks"]
    info = backend.get_track_info(0)

    assert listed[0]["name"] == "DRUMS"
    assert listed[0]["arm"] is False
    assert info["arm"] is False


def test_arm_on_group_track_is_a_clean_invalid_argument() -> None:
    backend = _group_backend()

    with pytest.raises(CommandError) as exc_info:
        backend.track_arm_get(0)
    assert exc_info.value.code == "INVALID_ARGUMENT"


def test_arrangement_clip_list_for_all_tracks_skips_group_tracks() -> None:
    backend = _group_backend()

    result = backend.arrangement_clip_list(None)

    assert result["clip_count"] == 0
    assert {clip["track"] for clip in result["clips"]} <= {1, 2}


class _MidiNoteVector(list):
    """Stand-in for Live.Clip.MidiNoteVector."""


class _StrictNoteClip(_Clip):
    """apply_note_modifications rejects anything but the vector it handed out."""

    def get_notes_extended(self, *args: Any, **kwargs: Any) -> _MidiNoteVector:
        return _MidiNoteVector(super().get_notes_extended(*args, **kwargs))

    def apply_note_modifications(self, notes: Any) -> None:  # type: ignore[override]
        if not isinstance(notes, _MidiNoteVector):
            raise TypeError(
                "Python argument types in Clip.apply_note_modifications(Clip, "
                f"{type(notes).__name__}) did not match C++ signature"
            )
        super().apply_note_modifications(tuple(notes))


def _strict_note_backend() -> LiveBackend:
    backend = LiveBackend(_SurfaceStub())
    clip = _StrictNoteClip(length=4.0)
    clip.set_notes(((60, 0.0, 0.5, 100, False), (64, 1.0, 0.5, 90, False)))
    backend.context._song().tracks[0].clip_slots[0].clip = clip
    return backend


def _notes(backend: LiveBackend) -> list[dict[str, Any]]:
    return backend.get_clip_notes(0, 0, None, None, None)["notes"]


def test_note_update_passes_the_live_note_vector() -> None:
    backend = _strict_note_backend()
    note_id = _notes(backend)[0]["note_id"]

    backend.update_clip_notes(0, 0, [{"note_id": note_id, "velocity": 70, "probability": 0.75}])

    updated = next(note for note in _notes(backend) if note["note_id"] == note_id)
    assert updated["velocity"] == 70
    assert updated["probability"] == 0.75


def test_note_transforms_pass_the_live_note_vector() -> None:
    backend = _strict_note_backend()

    backend.clip_notes_transpose(0, 0, 2, None, None, None)
    assert sorted(note["pitch"] for note in _notes(backend)) == [62, 66]

    backend.clip_notes_velocity_scale(0, 0, 0.5, 0, None, None, None)
    assert sorted(note["velocity"] for note in _notes(backend)) == [45, 50]


def test_undo_and_redo_use_song_api() -> None:
    surface = _SurfaceStub()
    calls: list[str] = []
    surface.song().undo = lambda: calls.append("undo")
    surface.song().redo = lambda: calls.append("redo")
    backend = LiveBackend(surface)

    assert backend.song_undo() == {"undone": True}
    assert backend.song_redo() == {"redone": True}
    assert calls == ["undo", "redo"]


class _MidiOutputTrack(_Track):
    """Live only exposes output_meter_level on tracks with MIDI output."""

    output_meter_level = 0.4

    @property
    def output_meter_left(self) -> float:
        raise RuntimeError("Tracks with MIDI output have no 'output_meter_left' property!")

    @property
    def output_meter_right(self) -> float:
        raise RuntimeError("Tracks with MIDI output have no 'output_meter_right' property!")


def test_meter_on_midi_output_track_falls_back_to_level() -> None:
    backend = LiveBackend(_SurfaceStub())
    backend.context._song().tracks[0] = _MidiOutputTrack(
        name="MIDI", has_audio_input=False, has_midi_input=True
    )

    result = dispatch_command(
        backend, "track_meter_get", {"track_ref": {"mode": "index", "index": 0}}
    )

    assert result["left"] == result["right"] == result["level"] == 0.4


@pytest.mark.parametrize(
    "names",
    [
        # Live 12.2+ redesigned Auto Filter
        ("Frequency", "Resonance", "Env Amount", "LFO Amount", "LFO Freq"),
        # Auto Filter Legacy
        ("Frequency", "Resonance", "Env. Modulation", "LFO Amount", "LFO Frequency"),
    ],
)
def test_auto_filter_wrapper_matches_live_12_parameter_names(names: tuple[str, ...]) -> None:
    surface = _SurfaceStub()
    surface.song().tracks[0].devices[0] = _Device(
        name="Auto Filter",
        class_name="AutoFilter",
        parameters=[_Parameter(name, 0.5, min=0.0, max=1.0) for name in names],
        class_display_name="Auto Filter",
    )
    backend = LiveBackend(surface)

    observed = backend.observe_standard_effect_state(effect_type="auto_filter", track=0, device=0)

    assert set(observed["state"]) >= {"env_amount", "lfo_rate"}


@dataclass
class _WarpMarker:
    beat_time: float
    sample_time: float


def test_warp_marker_list_is_json_serializable() -> None:
    surface = _SurfaceStub()
    slot = surface.song().tracks[1].clip_slots[0]
    slot.clip = _Clip(length=4.0, is_audio_clip=True, is_midi_clip=False, file_path="/tmp/a.wav")
    slot.clip.warping = True
    slot.clip.warp_markers = (_WarpMarker(0.0, 0.0), _WarpMarker(0.03125, 0.0161))
    backend = LiveBackend(surface)

    result = backend.clip_warp_marker_list(1, 0)

    json.dumps(result)
    assert result["warp_markers"] == [
        {"beat_time": 0.0, "sample_time": 0.0},
        {"beat_time": 0.03125, "sample_time": 0.0161},
    ]


class _LiveWarpMarker:
    """Live.Clip.WarpMarker: the constructor requires (sample_time, beat_time)."""

    def __init__(self, sample_time: float, beat_time: float) -> None:
        self.sample_time = sample_time
        self.beat_time = beat_time


def test_warp_marker_add_builds_live_warp_marker_on_current_timing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake_live = types.ModuleType("Live")
    fake_live.Clip = types.SimpleNamespace(WarpMarker=_LiveWarpMarker)  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "Live", fake_live)
    surface = _SurfaceStub()
    slot = surface.song().tracks[1].clip_slots[0]
    slot.clip = _Clip(length=12.0, is_audio_clip=True, is_midi_clip=False, file_path="/tmp/a.wav")
    slot.clip.warping = True
    # Live's default markers on a fresh warped clip at 116 BPM.
    slot.clip.warp_markers = (_WarpMarker(0.0, 0.0), _WarpMarker(0.03125, 0.03125 * 60 / 116))
    added: list[Any] = []
    slot.clip.add_warp_marker = added.append
    backend = LiveBackend(surface)

    backend.clip_warp_marker_add(1, 0, beat_time=4.0, sample_time=None)
    backend.clip_warp_marker_add(1, 0, beat_time=8.0, sample_time=3.5)

    assert all(isinstance(marker, _LiveWarpMarker) for marker in added)
    assert added[0].beat_time == 4.0
    assert added[0].sample_time == pytest.approx(4.0 * 60 / 116)
    assert (added[1].beat_time, added[1].sample_time) == (8.0, 3.5)
