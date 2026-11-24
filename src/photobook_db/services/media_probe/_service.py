# SPDX-License-Identifier: GPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 OesusDevelopmentStudios

import json
import logging
import shutil
import subprocess
from pathlib import Path

from photobook_db.services.media_probe._probe_output import VideoInfo, video_info_from

_logger = logging.getLogger(__name__)

# A damaged or huge file must not hold a request or an import forever.
PROBE_TIMEOUT_SECONDS = 30.0
FRAME_TIMEOUT_SECONDS = 60.0
_FILE_START_BYTES = 64  # enough for an EBML header's document type


class MediaProbe:
    """Runs ffprobe and ffmpeg as subprocesses, never through a shell, with the path as an argument."""

    def __init__(self, ffprobe: str = "ffprobe", ffmpeg: str = "ffmpeg") -> None:
        self._ffprobe = shutil.which(ffprobe)
        self._ffmpeg = shutil.which(ffmpeg)
        if self.available():
            _logger.info("FFmpeg found (%s, %s): videos are supported", self._ffprobe, self._ffmpeg)
        else:
            _logger.info("FFmpeg not found on PATH: videos are not supported")

    def available(self) -> bool:
        """Whether both ffprobe and ffmpeg were found when the server started."""
        return self._ffprobe is not None and self._ffmpeg is not None

    def probe(self, path: Path) -> VideoInfo | None:
        """None for anything that is not a video FFmpeg can read in a container the server takes, also when FFmpeg
        is missing or takes too long."""
        if self._ffprobe is None or self._ffmpeg is None:
            return None

        output = self._run(
            [self._ffprobe, "-v", "error", "-print_format", "json", "-show_format", "-show_streams", str(path)],
            PROBE_TIMEOUT_SECONDS,
        )
        if output is None:
            return None

        try:
            probe = json.loads(output)
            with path.open("rb") as file:
                file_start = file.read(_FILE_START_BYTES)
        except (ValueError, OSError):
            return None

        return video_info_from(probe, file_start)

    def frame(self, path: Path, at_ms: int, max_side: int) -> bytes | None:
        """The picture at that time as PNG, upright and fitting max_side; None if there is none (e.g. past the end)."""
        if self._ffprobe is None or self._ffmpeg is None:
            return None

        fit = f"scale=w='min(iw,{max_side})':h='min(ih,{max_side})':force_original_aspect_ratio=decrease"
        output = self._run(
            [
                self._ffmpeg, "-v", "error", "-nostdin",
                "-ss", f"{max(0, at_ms) / 1000:.3f}",  # before -i: seeks to the nearest key frame first, fast
                "-i", str(path),
                "-frames:v", "1", "-vf", fit, "-f", "image2pipe", "-c:v", "png", "-",
            ],
            FRAME_TIMEOUT_SECONDS,
        )
        return output or None

    def _run(self, arguments: list[str], timeout: float) -> bytes | None:
        try:
            completed = subprocess.run(arguments, capture_output=True, timeout=timeout, check=False)
        except subprocess.TimeoutExpired:
            _logger.warning("%s took longer than %s s, stopped", Path(arguments[0]).name, timeout)
            return None
        except OSError as error:
            _logger.warning("%s could not run: %s", Path(arguments[0]).name, error)
            return None

        return completed.stdout if completed.returncode == 0 else None
