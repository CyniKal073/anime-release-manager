"""MKVToolNix 封装层（离线测试，不真的调用工具）。"""

from __future__ import annotations

import json

import pytest

from src.mkv.tools import (
    MkvToolError,
    MkvTools,
    normalize_language,
    subtitle_extension,
)

PROBE_JSON = json.dumps(
    {
        "container": {"type": "Matroska", "properties": {"duration": 1440000}},
        "tracks": [
            {
                "id": 0,
                "type": "video",
                "codec": "V_MPEG4/ISO/AVC",
                "properties": {"language": "und"},
            },
            {
                "id": 1,
                "type": "audio",
                "codec": "A_AAC",
                "properties": {"language": "jpn"},
            },
            {
                "id": 2,
                "type": "subtitles",
                "codec": "S_TEXT/ASS",
                "properties": {
                    "language": "chi",
                    "track_name": "简体中文",
                    "default_track": True,
                },
            },
            {
                "id": 3,
                "type": "subtitles",
                "codec": "S_TEXT/UTF8",
                "properties": {"language": "eng", "track_name": "English"},
            },
        ],
    }
)


class RecordingMkv(MkvTools):
    """记录实际拼出的命令行，不真的执行。"""

    def __init__(self, outputs=None):
        super().__init__(tools_dir="")
        self._outputs = list(outputs or [])
        self.calls = []

    @property
    def paths(self):
        return {"mkvmerge": "mkvmerge", "mkvextract": "mkvextract"}

    def _run(self, args):
        self.calls.append([str(a) for a in args])
        return self._outputs.pop(0) if self._outputs else (0, "")


@pytest.mark.parametrize(
    "codec,expected",
    [
        ("S_TEXT/ASS", ".ass"),
        ("S_TEXT/SSA", ".ssa"),
        ("S_TEXT/UTF8", ".srt"),
        ("S_HDMV/PGS", ".sup"),
        ("S_UNKNOWN", ".ass"),  # 未知编码退化成 ass
        # mkvmerge 的 codec 字段是人名，必须也认得
        ("SubRip/SRT", ".srt"),
        ("ASS", ".ass"),
        ("WebVTT", ".vtt"),
        ("HDMV PGS", ".sup"),
    ],
)
def test_subtitle_extension(codec, expected):
    assert subtitle_extension(codec) == expected


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("zh", "chi"),
        ("zh-hans", "chi"),
        ("简中", "chi"),
        ("cht", "chi"),
        ("en", "eng"),
        ("ja", "jpn"),
        ("jpn", "jpn"),
        (None, "und"),
        ("", "und"),
    ],
)
def test_normalize_language(raw, expected):
    assert normalize_language(raw) == expected


def test_probe_parses_tracks(tmp_path):
    video = tmp_path / "a.mkv"
    video.write_bytes(b"x")

    mkv = RecordingMkv([(0, PROBE_JSON)])
    info = mkv.probe(str(video))

    assert info["container"] == "Matroska"
    assert len(info["tracks"]) == 4
    assert len(info["subtitle_tracks"]) == 2
    chinese = info["subtitle_tracks"][0]
    assert chinese["name"] == "简体中文"
    assert chinese["language"] == "chi"
    assert chinese["default"] is True
    assert chinese["extension"] == ".ass"
    assert mkv.calls[0][-1] == str(video)


def test_probe_rejects_missing_file(tmp_path):
    with pytest.raises(MkvToolError):
        RecordingMkv().probe(str(tmp_path / "nope.mkv"))


def test_extract_rejects_non_subtitle_track(tmp_path):
    video = tmp_path / "a.mkv"
    video.write_bytes(b"x")
    mkv = RecordingMkv([(0, PROBE_JSON)])

    with pytest.raises(MkvToolError) as excinfo:
        mkv.extract(str(video), [0], str(tmp_path))
    assert "不是字幕轨" in str(excinfo.value)


def test_extract_builds_expected_args(tmp_path):
    video = tmp_path / "a.mkv"
    video.write_bytes(b"x")
    out_dir = tmp_path / "out"
    mkv = RecordingMkv([(0, PROBE_JSON), (0, "")])

    files = mkv.extract(str(video), [2], str(out_dir))

    assert files == [str(out_dir / "a.track2.ass")]
    args = mkv.calls[1]
    assert args[0] == "mkvextract"
    assert args[1] == "tracks"
    assert args[2] == str(video)
    assert args[3].endswith("a.track2.ass")


def test_mux_builds_expected_args(tmp_path):
    target = tmp_path / "video.mkv"
    target.write_bytes(b"x")
    subtitle = tmp_path / "zh.ass"
    subtitle.write_text("[Script Info]", encoding="utf-8")
    output = tmp_path / "merged.mkv"

    mkv = RecordingMkv([(0, "ok")])
    result = mkv.mux_subtitles(
        str(target),
        [str(subtitle)],
        str(output),
        language="zh-hans",
        track_name="简体中文",
    )

    args = mkv.calls[0]
    assert args[0] == "mkvmerge"
    assert "-o" in args and str(output) in args
    assert str(target) in args
    assert "0:chi" in args  # zh-hans 归一成 chi
    assert "0:简体中文" in args
    assert "0:yes" in args
    assert args[-1] == str(subtitle)
    assert result["output"] == str(output)


def test_mux_refuses_to_overwrite_target(tmp_path):
    target = tmp_path / "video.mkv"
    target.write_bytes(b"x")
    subtitle = tmp_path / "zh.ass"
    subtitle.write_text("x", encoding="utf-8")

    with pytest.raises(MkvToolError) as excinfo:
        RecordingMkv().mux_subtitles(str(target), [str(subtitle)], str(target))
    assert "不能覆盖" in str(excinfo.value)


def test_mux_rejects_missing_subtitle(tmp_path):
    target = tmp_path / "video.mkv"
    target.write_bytes(b"x")
    with pytest.raises(MkvToolError):
        RecordingMkv().mux_subtitles(str(target), [str(tmp_path / "nope.ass")], str(tmp_path / "o.mkv"))


def test_run_raises_on_failure_code():
    class Failing(MkvTools):
        @property
        def paths(self):
            return {"mkvmerge": "mkvmerge", "mkvextract": "mkvextract"}

        def _run(self, args):  # pragma: no cover - 这里直接测包装逻辑
            raise MkvToolError("模拟失败")

    with pytest.raises(MkvToolError):
        Failing().probe(__file__)
