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
from test_live_backend import (
    HIP_HOP_GROOVE_PATH,
    _Clip,
    _Device,
    _Groove,
    _Parameter,
    _SurfaceStub,
    _Track,
)

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


def test_clip_warp_get_reports_sample_duration_in_seconds() -> None:
    surface = _SurfaceStub()
    slot = surface.song().tracks[1].clip_slots[0]
    slot.clip = _Clip(length=12.0, is_audio_clip=True, is_midi_clip=False, file_path="/tmp/a.wav")
    slot.clip.sample_length = 250_774
    slot.clip.sample_rate = 44_100.0
    backend = LiveBackend(surface)

    assert backend.clip_warp_get(1, 0)["sample_duration"] == pytest.approx(5.6865, abs=1e-4)


class _StrictMidiClip(_Clip):
    """A MIDI clip: Live raises RuntimeError for audio-only clip properties."""

    @property
    def gain(self) -> float:
        raise RuntimeError("Only audio clips have a gain")

    @gain.setter
    def gain(self, _value: float) -> None:
        raise RuntimeError("Only audio clips have a gain")

    @property
    def pitch_coarse(self) -> int:
        raise RuntimeError("Only audio clips have a pitch_coarse")

    @pitch_coarse.setter
    def pitch_coarse(self, _value: int) -> None:
        raise RuntimeError("Only audio clips have a pitch_coarse")


@pytest.mark.parametrize(
    ("command", "args"),
    [
        ("clip_gain_set", {"track": 1, "clip": 0, "db": -3.0}),
        ("clip_transpose_set", {"track": 1, "clip": 0, "semitones": 2}),
        ("arrangement_clip_gain_set", {"track": 1, "index": 0, "db": -3.0}),
        ("arrangement_clip_transpose_set", {"track": 1, "index": 0, "semitones": 2}),
    ],
)
def test_audio_only_clip_props_on_midi_clip_are_invalid_argument(
    command: str, args: dict[str, Any]
) -> None:
    surface = _SurfaceStub()
    track = surface.song().tracks[1]
    track.clip_slots[0].clip = _StrictMidiClip(length=4.0)
    track.arrangement_clips = [_StrictMidiClip(length=4.0)]
    backend = LiveBackend(surface)

    with pytest.raises(CommandError) as exc_info:
        dispatch_command(backend, command, args)

    assert exc_info.value.code == "INVALID_ARGUMENT"
    assert "audio clip" in exc_info.value.message


def test_audio_clip_gain_and_transpose_still_apply() -> None:
    surface = _SurfaceStub()
    track = surface.song().tracks[1]
    session_clip = _Clip(length=4.0, is_audio_clip=True, is_midi_clip=False, file_path="/a.wav")
    arrangement_clip = _Clip(length=4.0, is_audio_clip=True, is_midi_clip=False, file_path="/a.wav")
    for clip in (session_clip, arrangement_clip):
        clip.gain = 1.0
        clip.pitch_coarse = 0
    track.clip_slots[0].clip = session_clip
    track.arrangement_clips = [arrangement_clip]
    backend = LiveBackend(surface)

    backend.clip_gain_set(1, 0, 0.0)
    backend.clip_transpose_set(1, 0, 3)
    backend.arrangement_clip_gain_set(1, 0, 0.0)
    backend.arrangement_clip_transpose_set(1, 0, -5)

    assert session_clip.gain == pytest.approx(1.0)
    assert session_clip.pitch_coarse == 3
    assert arrangement_clip.gain == pytest.approx(1.0)
    assert arrangement_clip.pitch_coarse == -5


class _StrictGrooveClip(_Clip):
    """Live's Clip.groove setter takes only a Groove Pool entry: strings and None are rejected."""

    @property
    def groove(self) -> Any:
        return self.__dict__.get("_groove")

    @groove.setter
    def groove(self, value: Any) -> None:
        initializing = "_groove" not in self.__dict__  # _Clip.__init__ starts with None
        if not isinstance(value, _Groove) and not (initializing and value is None):
            raise TypeError(
                "Python argument types in None.None(Clip, "
                f"{type(value).__name__}) did not match C++ signature"
            )
        self.__dict__["_groove"] = value


def _strict_groove_backend() -> tuple[LiveBackend, Any]:
    surface = _SurfaceStub()
    surface.song().tracks[0].clip_slots[0].clip = _StrictGrooveClip(length=4.0)
    return LiveBackend(surface), surface


def test_clip_groove_set_assigns_a_groove_pool_entry() -> None:
    backend, surface = _strict_groove_backend()
    song = surface.song()
    previous_track = song.view.selected_track = song.tracks[1]

    result = backend.clip_groove_set(0, 0, HIP_HOP_GROOVE_PATH)

    clip = song.tracks[0].clip_slots[0].clip
    assert [groove.name for groove in song.groove_pool.grooves] == ["Hip Hop Boom Bap 16ths 90 bpm"]
    assert clip.groove is song.groove_pool.grooves[0]
    assert result["has_groove"] is True
    assert result["groove_name"] == "Hip Hop Boom Bap 16ths 90 bpm"
    assert result["groove_path"] == HIP_HOP_GROOVE_PATH
    assert song.view.selected_track is previous_track
    # Live may make a newly loaded groove the Set's default for new clips.
    assert result["added_to_groove_pool"] is True
    assert result["warnings"] == ["groove_may_be_default_for_new_clips"]


def test_clip_groove_set_reuses_a_groove_already_in_the_pool() -> None:
    backend, surface = _strict_groove_backend()
    existing = _Groove("Hip Hop Boom Bap 16ths 90 bpm")
    surface.song().groove_pool.grooves.append(existing)

    result = backend.clip_groove_set(0, 0, HIP_HOP_GROOVE_PATH)

    assert result["added_to_groove_pool"] is False
    assert result["warnings"] == []
    assert surface.song().tracks[0].clip_slots[0].clip.groove is existing
    assert surface.song().groove_pool.grooves == [existing]
    assert surface.application().browser.loaded_items == []


def test_clip_groove_set_accepts_a_pack_uri_containing_a_slash() -> None:
    backend, surface = _strict_groove_backend()
    pack_uri = "query:LivePacks#www.ableton.com/0:Grooves:Hip%20Hop%20Boom%20Bap%2016ths%2090%20bpm.agr"
    surface.application().browser.user_library.children[0].children[0].uri = pack_uri

    result = backend.clip_groove_set(0, 0, pack_uri)

    assert result["groove_name"] == "Hip Hop Boom Bap 16ths 90 bpm"
    assert result["groove_uri"] == pack_uri
    assert result["groove_path"] == HIP_HOP_GROOVE_PATH


def test_clip_groove_get_reads_the_live_groove() -> None:
    backend, _ = _strict_groove_backend()
    backend.clip_groove_set(0, 0, HIP_HOP_GROOVE_PATH)

    current = backend.clip_groove_get(0, 0)

    assert current["has_groove"] is True
    assert current["groove_name"] == "Hip Hop Boom Bap 16ths 90 bpm"
    assert current["groove_path"] is None


def test_clip_groove_clear_is_not_supported_when_a_groove_is_assigned() -> None:
    backend, surface = _strict_groove_backend()
    backend.clip_groove_set(0, 0, HIP_HOP_GROOVE_PATH)

    with pytest.raises(CommandError) as exc_info:
        backend.clip_groove_clear(0, 0)

    assert exc_info.value.details == {"reason": "not_supported_by_live_api"}
    assert surface.song().tracks[0].clip_slots[0].clip.groove is not None


def test_clip_groove_clear_without_a_groove_is_a_no_op() -> None:
    backend, _ = _strict_groove_backend()

    cleared = backend.clip_groove_clear(0, 0)

    assert cleared["has_groove"] is False


def test_clip_groove_set_fails_when_live_does_not_add_the_groove(monkeypatch) -> None:
    from remote_script.AbletonCliRemote.live_backend_parts import browser_load

    monkeypatch.setattr(browser_load, "_BROWSER_IMPORT_SETTLE_TIMEOUT_SECONDS", 0.0)
    backend, surface = _strict_groove_backend()
    surface.application().browser.load_item = lambda item: None  # type: ignore[method-assign]

    with pytest.raises(CommandError) as exc_info:
        backend.clip_groove_set(0, 0, HIP_HOP_GROOVE_PATH)

    assert exc_info.value.code == "INTERNAL_ERROR"
    assert surface.song().tracks[0].clip_slots[0].clip.groove is None


def test_live_12_browser_has_no_grooves_root() -> None:
    backend, _ = _strict_groove_backend()

    with pytest.raises(CommandError) as exc_info:
        backend.clip_groove_set(0, 0, "grooves/Hip Hop Boom Bap 16ths 90 bpm.agr")

    assert exc_info.value.code == "INVALID_ARGUMENT"
    assert "Unknown or unavailable category: grooves" in exc_info.value.message


def test_scenes_delete_uses_song_delete_scene() -> None:
    backend = LiveBackend(_SurfaceStub())

    result = dispatch_command(backend, "scenes_delete", {"scene": 0})

    assert result == {"scene": 0, "deleted": True, "scene_count": 1}
    assert [scene["name"] for scene in backend.scenes_list()["scenes"]] == ["Scene 2"]


def test_scenes_delete_refuses_the_only_scene() -> None:
    backend = LiveBackend(_SurfaceStub())
    dispatch_command(backend, "scenes_delete", {"scene": 1})

    with pytest.raises(CommandError) as exc_info:
        dispatch_command(backend, "scenes_delete", {"scene": 0})

    assert exc_info.value.code == "INVALID_ARGUMENT"
    assert len(backend.scenes_list()["scenes"]) == 1


def test_scene_name_can_be_cleared() -> None:
    backend = LiveBackend(_SurfaceStub())

    result = dispatch_command(backend, "set_scene_name", {"scene": 0, "name": ""})

    assert result == {"scene": 0, "name": ""}
