from typing import Literal

from pydantic import BaseModel, Field


class ProjectCreate(BaseModel):
    title: str = Field(min_length=1, max_length=120)
    source_text: str = Field(min_length=5)
    style: str = Field(default="cinematic clean realism")
    aspect_ratio: str = Field(default="16:9")
    duration_seconds: int = Field(default=5, ge=5, le=10)
    shot_count_mode: str = Field(default="auto")
    requested_shot_count: int | None = Field(default=None, ge=1, le=12)
    review_mode: bool = Field(default=False)


class FeedbackCreate(BaseModel):
    user_text: str = Field(min_length=1, max_length=1000)


class VideoGenerateRequest(BaseModel):
    video_mode: Literal["t2v", "i2v", "keyframes"] = "t2v"
    provider: str | None = Field(default=None, max_length=40)
    model: str | None = Field(default=None, max_length=120)
    duration_seconds: int | None = Field(default=None, ge=1, le=30)
    version_id: str | None = Field(default=None, max_length=40)
    description: str | None = Field(default=None, max_length=2000)
    camera_motion: str | None = Field(default=None, max_length=240)
    visual_prompt: str | None = Field(default=None, max_length=4000)
    first_frame_path: str | None = None
    last_frame_path: str | None = None
    reference_frame_path: str | None = None


class ShotDraftUpdate(BaseModel):
    description: str | None = Field(default=None, max_length=2000)
    camera_motion: str | None = Field(default=None, max_length=240)
    visual_prompt: str | None = Field(default=None, max_length=4000)
    negative_prompt: str | None = Field(default=None, max_length=2000)
    audio_prompt: str | None = Field(default=None, max_length=2000)
    video_mode: Literal["t2v", "i2v", "keyframes"] | None = None
    provider: str | None = Field(default=None, max_length=40)
    model: str | None = Field(default=None, max_length=120)
    duration_seconds: int | None = Field(default=None, ge=1, le=30)
    first_frame_path: str | None = None
    last_frame_path: str | None = None
    reference_frame_path: str | None = None


class KeyframeSelectRequest(BaseModel):
    first_frame_path: str | None = None
    last_frame_path: str | None = None


class KeyframeRedrawRequest(BaseModel):
    target: Literal["first", "last", "both"] = "both"


class DemoCleanupRequest(BaseModel):
    keep_project_id: str | None = None
    archive_failed: bool = True
    remove_invalid_videos: bool = True


class ProjectSummary(BaseModel):
    id: str
    title: str
    status: str
    style: str
    aspect_ratio: str
    updated_at: str
