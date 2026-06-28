"""State for listing a user's projects and handling new uploads."""

from __future__ import annotations

import base64
import os
import uuid

import reflex as rx
from sqlmodel import select

from .. import config
from ..models import AudioProject, MediaResult, Segment, Status
from ..schemas import ProjectVM
from ..services import storage
from .base import AppState
from .pipeline_state import PipelineState


def _fmt_dt(dt) -> str:
    try:
        return dt.strftime("%Y-%m-%d %H:%M")
    except Exception:
        return ""


# Recorder JS is passed straight to rx.call_script (eval'd in global scope each
# call). We do NOT pre-define functions via rx.script: React renders injected
# <script> tags inertly, so those functions never reach global scope.
_START_RECORDING_JS = """
(async () => {
    window.__rec = {mr: null, chunks: []};
    const stream = await navigator.mediaDevices.getUserMedia({audio: true});
    const mr = new MediaRecorder(stream);
    window.__rec.mr = mr;
    mr.ondataavailable = (e) => { if (e.data && e.data.size > 0) window.__rec.chunks.push(e.data); };
    mr.start();
})()
"""

# Returns a Promise resolving to a data URL; rx.call_script awaits it and passes
# the value to the `on_recorded` callback.
_STOP_RECORDING_JS = """
(() => new Promise((resolve) => {
    const r = window.__rec;
    const mr = r && r.mr;
    if (!mr) { resolve(""); return; }
    mr.onstop = () => {
        const blob = new Blob(r.chunks, {type: 'audio/webm'});
        try { mr.stream.getTracks().forEach((t) => t.stop()); } catch (e) {}
        const fr = new FileReader();
        fr.onloadend = () => resolve(fr.result);
        fr.readAsDataURL(blob);
    };
    mr.stop();
}))()
"""


class ProjectsState(AppState):
    """Dashboard listing + upload form."""

    # Upload form controls
    media_type: str = "both"
    segment_seconds: int = config.DEFAULT_SEGMENT_SECONDS
    uploading: bool = False
    upload_error: str = ""

    # In-app recording
    recording: bool = False
    recorded_data: str = ""  # data URL of the recorded audio (for preview/submit)

    # Dashboard data
    projects: list[ProjectVM] = []

    def _query_projects(self) -> list[ProjectVM]:
        """Fetch the current user's projects (newest first)."""
        if not self.is_authenticated:
            return []
        with rx.session() as session:
            rows = session.exec(
                select(AudioProject)
                .where(AudioProject.user_id == self.user_id)
                .order_by(AudioProject.created_at.desc())  # type: ignore[attr-defined]
            ).all()
            return [
                ProjectVM(
                    id=p.id,
                    filename=p.filename,
                    media_type=p.media_type,
                    segment_seconds=p.segment_seconds,
                    status=p.status,
                    created_at=_fmt_dt(p.created_at),
                )
                for p in rows
            ]

    @rx.event
    def load_projects(self):
        """Load the current user's projects into state."""
        self.projects = self._query_projects()

    @rx.event
    def delete_project(self, project_id: int):
        """Delete a run (rows + media + files), scoped to the owner."""
        if not self.is_authenticated:
            return
        with rx.session() as session:
            p = session.get(AudioProject, project_id)
            if p is None or p.user_id != self.user_id:
                return
            seg_ids = [
                s.id
                for s in session.exec(
                    select(Segment).where(Segment.project_id == project_id)
                ).all()
            ]
            if seg_ids:
                for m in session.exec(
                    select(MediaResult).where(MediaResult.segment_id.in_(seg_ids))  # type: ignore[attr-defined]
                ).all():
                    session.delete(m)
            for s in session.exec(
                select(Segment).where(Segment.project_id == project_id)
            ).all():
                session.delete(s)
            # Remove media objects from storage (original audio + rendered video).
            for key in (p.stored_path, p.video_path):
                if key:
                    storage.delete(key)
            session.delete(p)
            session.commit()
        self.projects = self._query_projects()

    @rx.event
    def set_media_type(self, value: str):
        """Setter for the media-type radio."""
        if value in config.MEDIA_TYPES:
            self.media_type = value

    @rx.event
    def set_segment_seconds(self, value: list[float]):
        """Setter for the segment-length slider (slider passes a list)."""
        v = value[0] if isinstance(value, list) else value
        self.segment_seconds = max(
            config.MIN_SEGMENT_SECONDS, min(config.MAX_SEGMENT_SECONDS, int(v))
        )

    @rx.event
    async def handle_upload(self, files: list[rx.UploadFile]):
        """Save the uploaded audio, create a project, and kick off the pipeline."""
        self.upload_error = ""
        if not self.is_authenticated:
            self.upload_error = "You must be logged in to upload."
            return
        if not files:
            self.upload_error = "No file selected."
            return

        file = files[0]
        filename = file.filename or "audio"
        ext = os.path.splitext(filename)[1].lower()
        if ext and ext not in config.AUDIO_EXTENSIONS:
            self.upload_error = f"Unsupported file type: {ext}"
            return

        self.uploading = True
        yield  # flush the spinner state to the UI

        data = await file.read()
        project_id = self._persist_audio(data, filename)

        self.uploading = False
        # Navigate to the project page and start the background pipeline.
        yield [
            rx.redirect(f"/project/{project_id}"),
            PipelineState.run(project_id),
        ]

    def _persist_audio(self, data: bytes, filename: str) -> int:
        """Upload audio to object storage and create an AudioProject row.

        Returns the new project id. Shared by file upload and in-app recording.
        The object key (`stored_path`) is what every instance uses to fetch it.
        """
        stored_path = f"{self.user_id}__{uuid.uuid4().hex}__{filename}"
        storage.put_bytes(stored_path, data)

        with rx.session() as session:
            project = AudioProject(
                user_id=self.user_id,
                filename=filename,
                stored_path=stored_path,
                media_type=self.media_type,
                segment_seconds=self.segment_seconds,
                status=Status.UPLOADED,
            )
            session.add(project)
            session.commit()
            session.refresh(project)
            return project.id

    # ---- in-app recording ----

    @rx.event
    def start_recording(self):
        """Begin (or restart) a microphone recording in the browser."""
        self.upload_error = ""
        self.recorded_data = ""
        self.recording = True
        return rx.call_script(_START_RECORDING_JS)

    @rx.event
    def stop_recording(self):
        """Stop recording; the resolved data URL is sent back to on_recorded."""
        self.recording = False
        return rx.call_script(
            _STOP_RECORDING_JS, callback=ProjectsState.on_recorded
        )

    @rx.event
    def on_recorded(self, data: str):
        """Receive the recorded audio (data URL) for preview/submit."""
        self.recorded_data = data or ""
        if not self.recorded_data:
            self.upload_error = "Recording failed — please allow microphone access."

    @rx.event
    def clear_recording(self):
        self.recorded_data = ""

    @rx.event
    async def submit_recording(self):
        """Persist the recorded audio and start the pipeline."""
        if not self.is_authenticated:
            self.upload_error = "You must be logged in."
            return
        if not self.recorded_data:
            self.upload_error = "Nothing recorded yet."
            return
        _, _, b64 = self.recorded_data.partition(",")
        try:
            raw = base64.b64decode(b64)
        except (ValueError, TypeError):
            self.upload_error = "Could not read the recording."
            return
        if not raw:
            self.upload_error = "The recording is empty."
            return
        project_id = self._persist_audio(raw, "recording.webm")
        self.recorded_data = ""
        yield [
            rx.redirect(f"/project/{project_id}"),
            PipelineState.run(project_id),
        ]
