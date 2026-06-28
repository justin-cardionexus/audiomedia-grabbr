"""Project page: live pipeline progress + per-segment media results."""

from __future__ import annotations

import reflex as rx

from ..components.auth import require_login
from ..components.navbar import layout
from ..components.pipeline_status import pipeline_status
from ..components.segment_card import segment_card
from ..state.pipeline_state import PipelineState


@require_login
def project_page() -> rx.Component:
    return layout(
        rx.vstack(
            rx.heading(PipelineState.project_filename, size="7"),
            rx.cond(
                PipelineState.audio_url != "",
                rx.audio(
                    src=PipelineState.audio_url,
                    width="100%",
                    height="54px",
                ),
            ),
            pipeline_status(),
            rx.cond(
                PipelineState.has_error,
                rx.callout(
                    PipelineState.error,
                    icon="triangle_alert",
                    color_scheme="red",
                ),
            ),
            rx.cond(
                PipelineState.has_video,
                rx.card(
                    rx.vstack(
                        rx.hstack(
                            rx.heading("Final video", size="5"),
                            rx.spacer(),
                            rx.button(
                                rx.icon("download", size=16),
                                "Download video",
                                on_click=rx.download(
                                    url=PipelineState.video_url,
                                    filename=PipelineState.video_download_name,
                                ),
                                variant="soft",
                                size="2",
                            ),
                            align="center",
                            width="100%",
                        ),
                        rx.video(
                            src=PipelineState.video_url,
                            width="100%",
                            height="480px",
                        ),
                        spacing="3",
                        width="100%",
                    ),
                    width="100%",
                ),
            ),
            rx.foreach(PipelineState.segments, segment_card),
            spacing="4",
            width="100%",
        )
    )
