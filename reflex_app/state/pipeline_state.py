"""Background pipeline: transcribe -> LLM segment queries -> media search.

Progress is mirrored to the DB at each stage, so a page reload mid-run still
reflects the current state (see `load_project`).
"""

from __future__ import annotations

import os
import sys
import tempfile

import reflex as rx
from reflex.utils.misc import run_in_thread
from sqlmodel import select


def _plog(msg: str) -> None:
    """Print a pipeline stage line to the server console (visible in the log)."""
    print(f"[pipeline] {msg}", file=sys.stdout, flush=True)

from ..models import AudioProject, MediaResult, Segment, Status
from ..schemas import MediaVM, SegmentVM
from ..services import llm, media_search, storage, transcription, video_compose
from .base import AppState

_STATUS_LABEL = {
    Status.UPLOADED: "Queued",
    Status.TRANSCRIBING: "Transcribing audio…",
    Status.ANALYZING: "Analyzing transcript with Claude…",
    Status.SEARCHING: "Searching for media…",
    Status.COMPOSING: "Composing final video…",
    Status.COMPLETE: "Complete",
    Status.ERROR: "Error",
}

# Ordinal position of each stage, used to drive the UI stepper.
_STATUS_ORDER = {
    Status.UPLOADED: 0,
    Status.TRANSCRIBING: 1,
    Status.ANALYZING: 2,
    Status.SEARCHING: 3,
    Status.COMPOSING: 4,
    Status.COMPLETE: 5,
}


def _time_label(start: float, end: float) -> str:
    def mmss(t: float) -> str:
        t = int(t)
        return f"{t // 60:02d}:{t % 60:02d}"

    return f"{mmss(start)}–{mmss(end)}"


class PipelineState(AppState):
    """Drives and displays a single project's pipeline run."""

    loaded_project_id: int = -1
    project_filename: str = ""
    stored_path: str = ""
    media_type: str = "both"
    status: str = ""
    progress: int = 0
    transcript: str = ""
    error: str = ""
    video_path: str = ""
    detail: str = ""
    # Presigned media URLs (regenerated on each page load).
    audio_url: str = ""
    video_url: str = ""
    segments: list[SegmentVM] = []

    @rx.var
    def status_label(self) -> str:
        return _STATUS_LABEL.get(self.status, self.status or "")

    @rx.var
    def status_index(self) -> int:
        """Ordinal of the current stage (for the stepper); -1 on error."""
        return _STATUS_ORDER.get(self.status, -1)

    @rx.var
    def in_pipeline(self) -> bool:
        """True once a run has started (any non-empty, non-error status)."""
        return self.status not in ("", Status.ERROR)

    @rx.var
    def is_busy(self) -> bool:
        return self.status in (
            Status.TRANSCRIBING,
            Status.ANALYZING,
            Status.SEARCHING,
            Status.COMPOSING,
        )

    @rx.var
    def has_video(self) -> bool:
        return bool(self.video_path)

    @rx.var
    def video_download_name(self) -> str:
        """A friendly download filename derived from the original audio name."""
        name = self.project_filename or "final"
        base = name.rsplit(".", 1)[0] if "." in name else name
        return f"{base}.mp4"

    @rx.var
    def is_complete(self) -> bool:
        return self.status == Status.COMPLETE

    @rx.var
    def has_error(self) -> bool:
        return self.status == Status.ERROR

    # ---- helpers (plain methods; DB-only, no state mutation) ----

    def _load_segments(self, project_id: int) -> list[SegmentVM]:
        with rx.session() as session:
            segs = session.exec(
                select(Segment)
                .where(Segment.project_id == project_id)
                .order_by(Segment.index)  # type: ignore[arg-type]
            ).all()
            seg_ids = [s.id for s in segs]
            media_by_seg: dict[int, list[MediaResult]] = {sid: [] for sid in seg_ids}
            if seg_ids:
                for m in session.exec(
                    select(MediaResult).where(MediaResult.segment_id.in_(seg_ids))  # type: ignore[attr-defined]
                ).all():
                    media_by_seg.setdefault(m.segment_id, []).append(m)

            def to_vm(m: MediaResult) -> MediaVM:
                return MediaVM(
                    url=m.url,
                    thumbnail_url=m.thumbnail_url,
                    source=m.source,
                    attribution=m.attribution,
                )

            out: list[SegmentVM] = []
            for s in segs:
                media = media_by_seg.get(s.id, [])
                out.append(
                    SegmentVM(
                        id=s.id,
                        index=s.index,
                        time_label=_time_label(s.start_sec, s.end_sec),
                        text=s.text,
                        search_query=s.search_query,
                        images=[to_vm(m) for m in media if m.kind == "image"],
                        videos=[to_vm(m) for m in media if m.kind == "video"],
                    )
                )
            return out

    def _set_db_status(self, project_id: int, status: str, error: str = "") -> None:
        with rx.session() as session:
            p = session.get(AudioProject, project_id)
            if p is not None:
                p.status = status
                if error:
                    p.error = error
                session.add(p)
                session.commit()

    # ---- page load (reload-safe) ----

    @rx.event
    def load_project(self):
        """Hydrate state from the DB for the /project/[project_id] route."""
        self.error = ""
        # `project_id` is the auto-attached dynamic route var for /project/[project_id].
        pid = getattr(self, "project_id", "")
        if not pid:
            return
        try:
            project_id = int(pid)
        except (TypeError, ValueError):
            return
        with rx.session() as session:
            p = session.get(AudioProject, project_id)
            if p is None or p.user_id != self.user_id:
                self.status = Status.ERROR
                self.error = "Project not found."
                return
            self.loaded_project_id = project_id
            self.project_filename = p.filename
            self.stored_path = p.stored_path
            self.media_type = p.media_type
            self.status = p.status
            self.transcript = p.transcript or ""
            self.error = p.error or ""
            self.video_path = p.video_path or ""
        # Short-lived presigned URLs so the browser can load the media directly
        # from object storage (works regardless of which instance served us).
        self.audio_url = storage.presigned_url(self.stored_path) if self.stored_path else ""
        self.video_url = storage.presigned_url(self.video_path) if self.video_path else ""
        self.segments = self._load_segments(project_id)
        self.progress = 100 if self.status == Status.COMPLETE else 0

    # ---- the background pipeline ----

    @rx.event(background=True)
    async def run(self, project_id: int):
        with rx.session() as session:
            project = session.get(AudioProject, project_id)
            if project is None:
                return
            stored_path = project.stored_path
            media_type = project.media_type
            window_seconds = project.segment_seconds
        # Pull the audio object to a local temp file (any instance can do this).
        ext = os.path.splitext(stored_path)[1] or ".bin"
        audio_path = storage.download_to_temp(stored_path, suffix=ext)
        _plog(f"start project={project_id} media={media_type} window={window_seconds}s")

        try:
            # 1. Transcribe
            async with self:
                self.status = Status.TRANSCRIBING
                self.progress = 5
                self.error = ""
                self.detail = "Transcribing the audio with faster-whisper…"
            self._set_db_status(project_id, Status.TRANSCRIBING)

            _plog("transcribing (faster-whisper)…")
            raw = await run_in_thread(lambda: transcription.transcribe(audio_path))
            windows = transcription.bucket_into_windows(raw, window_seconds)
            _plog(f"transcribed: {len(raw)} raw segments -> {len(windows)} windows")
            if not windows:
                raise RuntimeError("No speech detected in the audio.")

            full_transcript = " ".join(w.text for w in windows)
            with rx.session() as session:
                p = session.get(AudioProject, project_id)
                p.transcript = full_transcript
                session.add(p)
                for w in windows:
                    session.add(
                        Segment(
                            project_id=project_id,
                            index=w.index,
                            start_sec=w.start,
                            end_sec=w.end,
                            text=w.text,
                        )
                    )
                session.commit()

            async with self:
                self.transcript = full_transcript
                self.status = Status.ANALYZING
                self.progress = 25
                self.detail = (
                    f"Found {len(windows)} segments. "
                    "Claude is writing a visual search query for each…"
                )
                self.segments = self._load_segments(project_id)
            self._set_db_status(project_id, Status.ANALYZING)

            # 2. Analyze (LLM -> one search query per window)
            _plog(f"analyzing {len(windows)} windows with Claude…")
            payload = [{"index": w.index, "text": w.text} for w in windows]
            queries = await run_in_thread(lambda: llm.segment_queries(payload))
            _plog(f"received {len(queries)} search queries from Claude")
            with rx.session() as session:
                for seg in session.exec(
                    select(Segment).where(Segment.project_id == project_id)
                ).all():
                    q = queries.get(seg.index)
                    # Fall back to the transcript text if the LLM skipped a window.
                    seg.search_query = q or seg.text[:60]
                    session.add(seg)
                session.commit()

            async with self:
                self.status = Status.SEARCHING
                self.progress = 35
                self.detail = "Searching Pexels & Pixabay for matching media…"
                self.segments = self._load_segments(project_id)
            self._set_db_status(project_id, Status.SEARCHING)

            # 3. Search media per segment
            with rx.session() as session:
                search_targets = [
                    (s.id, s.search_query)
                    for s in session.exec(
                        select(Segment)
                        .where(Segment.project_id == project_id)
                        .order_by(Segment.index)  # type: ignore[arg-type]
                    ).all()
                ]
            total = len(search_targets)
            _plog(f"searching media for {total} segments (media={media_type})…")
            for i, (seg_id, query) in enumerate(search_targets):
                results = await media_search.search(query, media_type)
                _plog(f"  segment {i + 1}/{total} '{query}' -> {len(results)} items")
                if results:
                    with rx.session() as session:
                        for r in results:
                            session.add(MediaResult(segment_id=seg_id, **r))
                        session.commit()
                async with self:
                    # Search spans 35% -> 80% of the overall bar.
                    self.progress = 35 + int((i + 1) / max(1, total) * 45)
                    self.detail = f"Segment {i + 1}/{total}: “{query}”"
                    self.segments = self._load_segments(project_id)

            # 4. Compose the final video (media synced to the original audio)
            async with self:
                self.status = Status.COMPOSING
                self.progress = 85
                self.detail = (
                    "Rendering the final video — downloading media and encoding…"
                )
            self._set_db_status(project_id, Status.COMPOSING)
            # Free the resident Whisper model before the memory-heavy render.
            await run_in_thread(transcription.unload_model)
            _plog("composing final video…")

            seg_media: list[video_compose.SegmentMedia] = []
            with rx.session() as session:
                for s in session.exec(
                    select(Segment)
                    .where(Segment.project_id == project_id)
                    .order_by(Segment.index)  # type: ignore[arg-type]
                ).all():
                    media = session.exec(
                        select(MediaResult).where(MediaResult.segment_id == s.id)
                    ).all()
                    items = [
                        video_compose.MediaItem(kind=m.kind, url=m.url)
                        for m in sorted(media, key=lambda m: 0 if m.kind == "image" else 1)
                    ]
                    seg_media.append(
                        video_compose.SegmentMedia(start=s.start_sec, media=items)
                    )

            # Render to a local temp file, then upload the result to storage.
            video_name = f"{stored_path.rsplit('.', 1)[0]}.final.mp4"
            out_fd, out_path = tempfile.mkstemp(suffix=".mp4", prefix="compose_out_")
            os.close(out_fd)
            try:
                await run_in_thread(
                    lambda: video_compose.compose(audio_path, seg_media, out_path)
                )
                await run_in_thread(
                    lambda: storage.put_file(video_name, out_path, "video/mp4")
                )
            finally:
                try:
                    os.remove(out_path)
                except OSError:
                    pass
            with rx.session() as session:
                p = session.get(AudioProject, project_id)
                p.video_path = video_name
                session.add(p)
                session.commit()
            async with self:
                self.video_path = video_name
                self.video_url = storage.presigned_url(video_name)
            _plog(f"composed -> {video_name}")

            async with self:
                self.status = Status.COMPLETE
                self.progress = 100
                self.detail = "Done! Your video is ready below."
            self._set_db_status(project_id, Status.COMPLETE)
            _plog(f"complete project={project_id}")

        except Exception as e:  # noqa: BLE001 - surface any failure to the UI
            _plog(f"ERROR project={project_id}: {e}")
            async with self:
                self.status = Status.ERROR
                self.error = str(e)
                self.detail = ""
            self._set_db_status(project_id, Status.ERROR, str(e))
        finally:
            # Clean up the downloaded audio temp file.
            try:
                os.remove(audio_path)
            except OSError:
                pass
