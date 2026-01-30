import sys
import wave
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.agents.daily_reporter import radio


def _write_wav(path: Path, frames: int, rate: int = 24000) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(path), "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(rate)
        wf.writeframes(b"\x00\x00" * frames)


def test_parse_radio_script_json_valid() -> None:
    raw = '{"title":"Test","sections":[{"name":"opening","lines":[{"speaker":"Lab","text":"Hello"},{"speaker":"Yuki","text":"Hi"}]}]}'
    sections = radio.parse_radio_script_json(raw)
    assert len(sections) == 1
    assert sections[0].name == "opening"
    assert sections[0].lines[0].speaker == "Lab"
    assert sections[0].lines[0].text == "Hello"


def test_parse_radio_script_json_invalid_returns_empty() -> None:
    sections = radio.parse_radio_script_json("not json")
    assert sections == []


def test_parse_radio_script_json_with_code_fence() -> None:
    raw = """```json
    {"title":"Test","sections":[{"name":"opening","lines":[{"speaker":"Lab","text":"Hello"}]}]}
    ```"""
    sections = radio.parse_radio_script_json(raw)
    assert len(sections) == 1
    assert sections[0].lines[0].text == "Hello"


def test_render_section_for_tts_includes_speakers() -> None:
    section = radio.RadioSection(
        name="opening",
        lines=[
            radio.RadioLine(speaker="Lab", text="Hello"),
            radio.RadioLine(speaker="Yuki", text="Hi"),
        ],
    )
    rendered = radio.render_section_for_tts(section)
    assert "Lab: Hello" in rendered
    assert "Yuki: Hi" in rendered


def test_concat_wav_files_two_small_wavs(tmp_path: Path) -> None:
    wav1 = tmp_path / "a.wav"
    wav2 = tmp_path / "b.wav"
    out = tmp_path / "out.wav"

    _write_wav(wav1, frames=100)
    _write_wav(wav2, frames=200)

    radio.concat_wav_files([wav1, wav2], out)

    with wave.open(str(out), "rb") as wf:
        assert wf.getnframes() == 300


def test_resolve_radio_paths() -> None:
    base_dir = Path("data/radio")
    now_utc = datetime(2026, 1, 1, 0, 0, 0, tzinfo=timezone.utc)
    paths = radio.resolve_radio_paths(now_utc, base_dir)
    assert "script_path" in paths
    assert "audio_path" in paths
    assert "tmp_dir" in paths
    assert paths["script_path"].as_posix().startswith(base_dir.as_posix())
    assert "2026_01_01" in paths["script_path"].name
