import base64
import json
import os
import struct
import subprocess
import wave
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable


@dataclass
class RadioLine:
    speaker: str
    text: str


@dataclass
class RadioSection:
    name: str
    lines: list[RadioLine]


def resolve_radio_paths(now_utc: datetime, base_dir: Path) -> dict[str, Path]:
    jst = timezone(timedelta(hours=9))
    date_str = now_utc.astimezone(jst).strftime("%Y_%m_%d")
    scripts_dir = base_dir / "scripts"
    audio_dir = base_dir / "audio"
    tmp_dir = base_dir / "tmp" / date_str
    script_path = scripts_dir / f"radio_script_{date_str}.json"
    audio_path = audio_dir / f"radio_audio_{date_str}.wav"
    return {
        "script_path": script_path,
        "audio_path": audio_path,
        "tmp_dir": tmp_dir,
    }


def load_radio_knowledge(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8").strip()
    except FileNotFoundError:
        return ""


def _extract_json_text(raw: str) -> str:
    start = raw.find("{")
    end = raw.rfind("}")
    if start != -1 and end != -1 and end > start:
        return raw[start : end + 1]
    return raw


def _strip_code_fences(raw: str) -> str:
    cleaned = raw.strip()
    if cleaned.startswith("```"):
        cleaned = cleaned.split("\n", 1)[1] if "\n" in cleaned else ""
    if cleaned.endswith("```"):
        cleaned = cleaned.rsplit("```", 1)[0].rstrip()
    return cleaned


def parse_radio_script_json(raw: str) -> list[RadioSection]:
    if not raw:
        return []
    raw = _strip_code_fences(raw)
    json_text = _extract_json_text(raw)
    try:
        payload = json.loads(json_text)
    except json.JSONDecodeError:
        return []

    sections = payload.get("sections")
    if not isinstance(sections, list):
        return []

    parsed_sections: list[RadioSection] = []
    for section in sections:
        if not isinstance(section, dict):
            continue
        name = str(section.get("name", "")).strip() or "section"
        lines_raw = section.get("lines")
        if not isinstance(lines_raw, list):
            continue
        lines: list[RadioLine] = []
        for line in lines_raw:
            if not isinstance(line, dict):
                continue
            speaker = str(line.get("speaker", "")).strip()
            text = str(line.get("text", "")).strip()
            if not speaker or not text:
                continue
            lines.append(RadioLine(speaker=speaker, text=text))
        if lines:
            parsed_sections.append(RadioSection(name=name, lines=lines))

    return parsed_sections


def _base_tts_header() -> str:
    return (
        "Please read aloud the following in a podcast interview style:\n"
        "Read aloud in Japanese with energetic and friendly tone. "
        "ラボちゃん is an energetic girl who uses 'ッス' ending. "
        "ユウキ is a polite young boy assistant. "
        "Avoid adding extra syllables like 'スッスー' or elongating endings; "
        "use a single natural 'ッス' when appropriate."
    )


def render_section_for_tts(section: RadioSection) -> str:
    header = _base_tts_header()
    lines = [header, ""]
    for line in section.lines:
        lines.append(f"{line.speaker}: {line.text}")
    return "\n".join(lines).strip()


def render_full_script_for_tts(
    sections: list[RadioSection], max_chars: int | None = None
) -> str:
    header = _base_tts_header()
    lines: list[str] = [header, ""]
    for section in sections:
        for line in section.lines:
            lines.append(f"{line.speaker}: {line.text}")
        lines.append("")
    text = "\n".join(lines).strip()
    if max_chars and len(text) > max_chars:
        return text[:max_chars].rstrip()
    return text


def save_radio_script(raw_json: str, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(_strip_code_fences(raw_json), encoding="utf-8")


def _convert_to_wav(audio_data: bytes, mime_type: str) -> bytes:
    parameters = _parse_audio_mime_type(mime_type)
    bits_per_sample = parameters["bits_per_sample"]
    sample_rate = parameters["rate"]
    num_channels = 1
    data_size = len(audio_data)
    bytes_per_sample = bits_per_sample // 8
    block_align = num_channels * bytes_per_sample
    byte_rate = sample_rate * block_align
    chunk_size = 36 + data_size

    header = struct.pack(
        "<4sI4s4sIHHIIHH4sI",
        b"RIFF",
        chunk_size,
        b"WAVE",
        b"fmt ",
        16,
        1,
        num_channels,
        sample_rate,
        byte_rate,
        block_align,
        bits_per_sample,
        b"data",
        data_size,
    )
    return header + audio_data


def _parse_audio_mime_type(mime_type: str) -> dict[str, int]:
    bits_per_sample = 16
    rate = 24000

    parts = mime_type.split(";")
    for param in parts:
        param = param.strip()
        if param.lower().startswith("rate="):
            try:
                rate = int(param.split("=", 1)[1])
            except (ValueError, IndexError):
                pass
        elif param.lower().startswith("audio/l"):
            try:
                # Examples: audio/L16, audio/l24
                bits_per_sample = int(param.lower().split("audio/l", 1)[1])
            except (ValueError, IndexError):
                pass

    return {"bits_per_sample": bits_per_sample, "rate": rate}


_WAV_MIME_TYPES = {
    "audio/wav",
    "audio/x-wav",
    "audio/wave",
    "audio/x-wave",
    "audio/vnd.wave",
}


def _looks_like_wav(audio_data: bytes) -> bool:
    return (
        len(audio_data) >= 12
        and audio_data[0:4] == b"RIFF"
        and audio_data[8:12] == b"WAVE"
    )


def _ffmpeg_decode_to_wav(audio_data: bytes) -> bytes:
    # Best-effort decode to WAV using ffmpeg. This supports many formats (mp3, wav, etc.).
    proc = subprocess.run(
        [
            "ffmpeg",
            "-hide_banner",
            "-loglevel",
            "error",
            "-i",
            "pipe:0",
            "-f",
            "wav",
            "pipe:1",
        ],
        input=audio_data,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=True,
    )
    return proc.stdout


def _ensure_wav_bytes(audio_data: bytes, mime_type: str | None) -> bytes:
    if not audio_data:
        return b""
    if _looks_like_wav(audio_data):
        return audio_data

    base_mime = (mime_type or "").split(";", 1)[0].strip().lower()

    # Some SDKs return a MIME like audio/wav but Python's mimetypes may not recognize it.
    # If it's already a WAV, we returned above; otherwise, try decoding (or fall back).
    if base_mime in _WAV_MIME_TYPES:
        try:
            return _ffmpeg_decode_to_wav(audio_data)
        except (FileNotFoundError, subprocess.CalledProcessError):
            return _convert_to_wav(audio_data, mime_type or "audio/L16;rate=24000")

    # Raw PCM in Lxx format: wrap with a WAV header using the declared rate/bit-depth.
    if base_mime.startswith("audio/l") or base_mime in {"audio/pcm"}:
        return _convert_to_wav(audio_data, mime_type or "audio/L16;rate=24000")

    # Unknown/encoded audio: try ffmpeg, otherwise fall back to treating bytes as PCM.
    try:
        return _ffmpeg_decode_to_wav(audio_data)
    except (FileNotFoundError, subprocess.CalledProcessError):
        return _convert_to_wav(audio_data, mime_type or "audio/L16;rate=24000")


def _synthesize_section_audio(
    client: Any,
    text: str,
    model: str,
    voice_labchan: str,
    voice_yuki: str,
    temperature: float,
) -> tuple[bytes, str | None]:
    from google.genai import types

    generate_content_config = types.GenerateContentConfig(
        temperature=temperature,
        response_modalities=["audio"],
        speech_config=types.SpeechConfig(
            multi_speaker_voice_config=types.MultiSpeakerVoiceConfig(
                speaker_voice_configs=[
                    types.SpeakerVoiceConfig(
                        speaker="ラボちゃん",
                        voice_config=types.VoiceConfig(
                            prebuilt_voice_config=types.PrebuiltVoiceConfig(
                                voice_name=voice_labchan
                            )
                        ),
                    ),
                    types.SpeakerVoiceConfig(
                        speaker="ユウキ",
                        voice_config=types.VoiceConfig(
                            prebuilt_voice_config=types.PrebuiltVoiceConfig(
                                voice_name=voice_yuki
                            )
                        ),
                    ),
                ]
            )
        ),
    )

    contents = [
        types.Content(
            role="user",
            parts=[types.Part.from_text(text=text)],
        )
    ]

    audio_chunks: list[bytes] = []
    mime_type: str | None = None

    for chunk in client.models.generate_content_stream(
        model=model,
        contents=contents,
        config=generate_content_config,
    ):
        candidates = getattr(chunk, "candidates", None)
        if not candidates:
            continue

        content = candidates[0].content
        if content is None or not getattr(content, "parts", None):
            continue

        for part in content.parts:
            inline_data = getattr(part, "inline_data", None)
            if not inline_data or not getattr(inline_data, "data", None):
                continue

            if mime_type is None:
                mime_type = getattr(inline_data, "mime_type", None)

            data = inline_data.data
            if isinstance(data, str):
                try:
                    audio_chunks.append(base64.b64decode(data))
                except Exception:
                    continue
            else:
                audio_chunks.append(bytes(data))

    if not audio_chunks:
        return b"", mime_type

    return b"".join(audio_chunks), mime_type


def generate_radio_audio(
    sections: list[RadioSection],
    *,
    tts_model: str,
    voice_labchan: str,
    voice_yuki: str,
    temperature: float,
    single_pass: bool,
    max_chars: int | None,
    output_path: Path,
    tmp_dir: Path,
) -> Path:
    api_key = os.environ.get("GOOGLE_API_KEY")
    if not api_key:
        raise RuntimeError("GOOGLE_API_KEY is not set")

    from google import genai

    client = genai.Client(api_key=api_key)
    tmp_dir.mkdir(parents=True, exist_ok=True)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    def _run_single_pass() -> Path:
        full_text = render_full_script_for_tts(sections, max_chars)
        audio_bytes, mime_type = _synthesize_section_audio(
            client,
            full_text,
            model=tts_model,
            voice_labchan=voice_labchan,
            voice_yuki=voice_yuki,
            temperature=temperature,
        )
        if not audio_bytes:
            raise RuntimeError("No audio bytes from single-pass TTS")
        mime_type = mime_type or "audio/L16;rate=24000"
        audio_bytes = _ensure_wav_bytes(audio_bytes, mime_type)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_bytes(audio_bytes)
        return output_path

    if single_pass:
        try:
            return _run_single_pass()
        except Exception:
            try:
                return _run_single_pass()
            except Exception:
                print("Single-pass TTS failed twice; falling back to multi-pass.")

    section_paths: list[Path] = []

    for idx, section in enumerate(sections):
        text = render_section_for_tts(section)
        audio_bytes, mime_type = _synthesize_section_audio(
            client,
            text,
            model=tts_model,
            voice_labchan=voice_labchan,
            voice_yuki=voice_yuki,
            temperature=temperature,
        )
        if not audio_bytes:
            continue

        mime_type = mime_type or "audio/L16;rate=24000"
        audio_bytes = _ensure_wav_bytes(audio_bytes, mime_type)

        section_path = tmp_dir / f"section_{idx:02d}_{section.name}.wav"
        section_path.write_bytes(audio_bytes)
        section_paths.append(section_path)

    if not section_paths:
        raise RuntimeError("No audio sections generated")

    concat_wav_files(section_paths, output_path)
    return output_path


def concat_wav_files(inputs: Iterable[Path], output: Path) -> None:
    inputs = list(inputs)
    if not inputs:
        raise ValueError("No input WAV files to concatenate")

    with wave.open(str(inputs[0]), "rb") as first:
        params = first.getparams()
        match_key = (
            params.nchannels,
            params.sampwidth,
            params.framerate,
            params.comptype,
            params.compname,
        )

    output.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(output), "wb") as out_wave:
        out_wave.setparams(params)
        for path in inputs:
            with wave.open(str(path), "rb") as in_wave:
                current = in_wave.getparams()
                current_key = (
                    current.nchannels,
                    current.sampwidth,
                    current.framerate,
                    current.comptype,
                    current.compname,
                )
                if current_key != match_key:
                    raise ValueError("WAV parameters mismatch")
                out_wave.writeframes(in_wave.readframes(in_wave.getnframes()))


def convert_wav_to_mp3(wav_path: Path, mp3_path: Path, bitrate: str) -> Path:
    mp3_path.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        [
            "ffmpeg",
            "-y",
            "-i",
            str(wav_path),
            "-codec:a",
            "libmp3lame",
            "-b:a",
            bitrate,
            str(mp3_path),
        ],
        check=True,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    return mp3_path
