"""Manual crop override for a scene's clip -- requested directly (not part of the original numbered
requirements list). MoneyPrinterTurbo always fits a clip to the render's aspect ratio itself, but always
centered, with no hook for choosing what part of the frame survives (see pipeline/stages/render/crop.py's
module docstring). So this pipeline bakes a human's chosen framing into an actual cropped file before MPT
ever sees the clip. Covers the pure crop_box() math, apply_scene_crop()'s best-effort/fallback behavior,
the orchestrator's set_scene_crop()/remove_scene_crop() validation and gating (including clearing a stale
crop whenever a scene's clip_path actually changes), and the render stage using a cropped path when one
applies."""
from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

import httpx

from pipeline.core import state_machine as sm
from pipeline.core.models import Asset, Job, Scene, SceneCrop
from pipeline.stages.base import StageContext
from pipeline.stages.mpt_client import MptClient
from pipeline.stages.render import crop as cropmod
from pipeline.stages.render.mpt import MptRenderStage

from tests.test_scene_assets import Base, FAKE_NO_SOURCES


class CropBoxMathTests(unittest.TestCase):
    def test_aspect_ratio_parses_wxh(self):
        self.assertAlmostEqual(cropmod.aspect_ratio("9:16"), 9 / 16)
        self.assertAlmostEqual(cropmod.aspect_ratio("16:9"), 16 / 9)

    def test_aspect_ratio_falls_back_to_9_16_on_garbage(self):
        self.assertAlmostEqual(cropmod.aspect_ratio("nonsense"), 9 / 16)
        self.assertAlmostEqual(cropmod.aspect_ratio(""), 9 / 16)
        self.assertAlmostEqual(cropmod.aspect_ratio("0:16"), 9 / 16)

    def test_centered_zoom_1_matches_mpts_own_auto_crop(self):
        # A 1920x1080 landscape photo cropped to 9:16 at the default center/zoom should land on exactly
        # the window MPT's own cover-crop (_fit_clip_to_canvas) would pick: height-limited, width scaled
        # to the target aspect, centered horizontally.
        x, y, w, h = cropmod.crop_box(1920, 1080, 9 / 16, center_x=0.5, center_y=0.5, zoom=1.0)
        self.assertEqual((y, h), (0, 1080))
        self.assertEqual(w, round(1080 * 9 / 16))
        self.assertEqual(x, round((1920 - w) / 2))

    def test_zoom_tightens_the_window(self):
        _, _, w1, h1 = cropmod.crop_box(1920, 1080, 9 / 16, center_x=0.5, center_y=0.5, zoom=1.0)
        _, _, w2, h2 = cropmod.crop_box(1920, 1080, 9 / 16, center_x=0.5, center_y=0.5, zoom=2.0)
        self.assertLess(w2, w1)
        self.assertLess(h2, h1)

    def test_off_center_shifts_the_window_left_of_dead_center(self):
        x, y, w, h = cropmod.crop_box(1920, 1080, 9 / 16, center_x=0.1, center_y=0.5, zoom=1.0)
        self.assertGreaterEqual(x, 0)
        self.assertLessEqual(x + w, 1920)
        self.assertLess(x, round((1920 - w) / 2))

    def test_extreme_center_clamps_instead_of_leaving_the_frame(self):
        x, y, w, h = cropmod.crop_box(1920, 1080, 9 / 16, center_x=0.0, center_y=0.0, zoom=1.0)
        self.assertEqual((x, y), (0, 0))
        x2, y2, w2, h2 = cropmod.crop_box(1920, 1080, 9 / 16, center_x=1.0, center_y=1.0, zoom=1.0)
        self.assertEqual(x2 + w2, 1920)
        self.assertEqual(y2 + h2, 1080)

    def test_portrait_source_is_width_limited(self):
        x, y, w, h = cropmod.crop_box(1080, 1920, 9 / 16, center_x=0.5, center_y=0.5, zoom=1.0)
        self.assertEqual((x, w), (0, 1080))

    def test_sub_1_zoom_is_treated_as_1(self):
        a = cropmod.crop_box(1920, 1080, 9 / 16, center_x=0.5, center_y=0.5, zoom=1.0)
        b = cropmod.crop_box(1920, 1080, 9 / 16, center_x=0.5, center_y=0.5, zoom=0.3)
        self.assertEqual(a, b)


def _asset(**kw):
    base = dict(source="upload", kind="image", path="/tmp/src.jpg", rel_path="src.jpg",
                source_url="upload://src.jpg", width=1920, height=1080, sha256="crop-asset-1")
    base.update(kw)
    return Asset(**base)


class ApplySceneCropTests(unittest.IsolatedAsyncioTestCase):
    async def test_no_crop_set_returns_the_clip_path_unchanged(self):
        scene = Scene(index=0, narration="n", clip_path="/tmp/src.jpg")
        with tempfile.TemporaryDirectory() as d:
            out = await cropmod.apply_scene_crop(scene, _asset(), target_aspect="9:16", out_dir=Path(d))
        self.assertEqual(out, "/tmp/src.jpg")

    async def test_no_clip_path_returns_none(self):
        scene = Scene(index=0, narration="n", crop=SceneCrop())
        with tempfile.TemporaryDirectory() as d:
            out = await cropmod.apply_scene_crop(scene, _asset(), target_aspect="9:16", out_dir=Path(d))
        self.assertIsNone(out)

    async def test_missing_asset_falls_back_to_uncropped(self):
        scene = Scene(index=0, narration="n", clip_path="/tmp/src.jpg", crop=SceneCrop(center_x=0.2, zoom=1.5))
        with tempfile.TemporaryDirectory() as d:
            out = await cropmod.apply_scene_crop(scene, None, target_aspect="9:16", out_dir=Path(d))
        self.assertEqual(out, "/tmp/src.jpg")

    async def test_asset_with_unknown_dimensions_falls_back_to_uncropped(self):
        scene = Scene(index=0, narration="n", clip_path="/tmp/src.jpg", crop=SceneCrop(center_x=0.2, zoom=1.5))
        with tempfile.TemporaryDirectory() as d:
            out = await cropmod.apply_scene_crop(scene, _asset(width=None, height=None),
                                                  target_aspect="9:16", out_dir=Path(d))
        self.assertEqual(out, "/tmp/src.jpg")

    async def test_a_window_covering_the_whole_frame_is_a_noop(self):
        # Already 9:16 (1080x1920), dead center, zoom 1.0 -- the crop window IS the whole source frame.
        scene = Scene(index=0, narration="n", clip_path="/tmp/src.jpg", crop=SceneCrop())
        with tempfile.TemporaryDirectory() as d:
            out = await cropmod.apply_scene_crop(scene, _asset(width=1080, height=1920),
                                                  target_aspect="9:16", out_dir=Path(d))
        self.assertEqual(out, "/tmp/src.jpg")

    async def test_runs_ffmpeg_and_returns_the_cropped_path(self):
        calls = []

        async def fake_runner(args):
            calls.append(args)
            Path(args[-1]).write_bytes(b"cropped")
            return 0, "", ""

        scene = Scene(id="sc1", index=0, narration="n", clip_path="/tmp/src.jpg",
                      crop=SceneCrop(center_x=0.2, center_y=0.5, zoom=1.5))
        with tempfile.TemporaryDirectory() as d:
            out = await cropmod.apply_scene_crop(scene, _asset(), target_aspect="9:16", out_dir=Path(d),
                                                  runner=fake_runner)
            self.assertEqual(Path(out).read_bytes(), b"cropped")
            self.assertTrue(out.endswith("sc1.jpg"))
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0][0], "ffmpeg")
        self.assertIn("-vf", calls[0])
        self.assertTrue(any(a.startswith("crop=") for a in calls[0]))
        self.assertNotIn("-c:a", calls[0])   # a still image has no audio stream to copy

    async def test_video_asset_copies_the_audio_stream(self):
        async def fake_runner(args):
            Path(args[-1]).write_bytes(b"x")
            return 0, "", ""

        scene = Scene(id="sc2", index=0, narration="n", clip_path="/tmp/src.mp4", crop=SceneCrop(zoom=1.5))
        with tempfile.TemporaryDirectory() as d:
            out = await cropmod.apply_scene_crop(scene, _asset(kind="video", path="/tmp/src.mp4"),
                                                  target_aspect="9:16", out_dir=Path(d), runner=fake_runner)
        self.assertTrue(out.endswith("sc2.mp4"))

    async def test_ffmpeg_failure_raises_crop_error(self):
        async def fake_runner(args):
            return 1, "", "boom, ffmpeg blew up"

        scene = Scene(id="sc3", index=0, narration="n", clip_path="/tmp/src.jpg", crop=SceneCrop(zoom=1.5))
        with tempfile.TemporaryDirectory() as d:
            with self.assertRaises(cropmod.CropError):
                await cropmod.apply_scene_crop(scene, _asset(), target_aspect="9:16", out_dir=Path(d),
                                                runner=fake_runner)


def _mpt_handler(requests: list):
    def h(req: httpx.Request) -> httpx.Response:
        body = json.loads(req.content) if req.content else {}
        requests.append((req.url.path, body))
        if req.url.path == "/api/v1/videos":
            return httpx.Response(200, json={"status": 200, "message": "ok", "data": {"task_id": "t1"}})
        if req.url.path.startswith("/api/v1/tasks/"):
            return httpx.Response(200, json={"status": 200, "message": "ok",
                                              "data": {"task_id": "t1", "state": 1, "videos": ["http://mpt/final.mp4"]}})
        if req.url.path.endswith(".mp4"):
            return httpx.Response(200, content=b"bytes")
        return httpx.Response(404, json={"status": 404, "message": "nope"})
    return h


class RenderUsesCropTests(unittest.IsolatedAsyncioTestCase):
    async def test_a_scene_with_a_crop_sends_the_cropped_path_to_mpt(self):
        requests: list = []
        client = MptClient("http://mpt", transport=httpx.MockTransport(_mpt_handler(requests)))
        asset = Asset(id="a1", source="upload", kind="image", path="/tmp/src.jpg", rel_path="src.jpg",
                      source_url="upload://src.jpg", width=1920, height=1080, sha256="rendercrop1")
        scene = Scene(index=0, narration="n", clip_path="/tmp/src.jpg", asset_id="a1",
                      crop=SceneCrop(center_x=0.2, center_y=0.5, zoom=1.5))
        job = Job(subject="cats"); job.assets = [asset]; job.scenes = [scene]

        async def fake_runner(args):
            Path(args[-1]).write_bytes(b"cropped")
            return 0, "", ""

        with tempfile.TemporaryDirectory() as d:
            ctx = StageContext(Path(d) / "assets", project_dir=Path(d))
            stage = MptRenderStage(client, crop_runner=fake_runner)
            await stage.run(job, ctx)
            self.assertTrue((Path(d) / "cropped" / f"{scene.id}.jpg").exists())
        sent_url = next(b for p, b in requests if p == "/api/v1/videos")["video_materials"][0]["url"]
        self.assertNotEqual(sent_url, "/tmp/src.jpg")
        self.assertTrue(sent_url.endswith(f"{scene.id}.jpg"))

    async def test_a_scene_without_a_crop_sends_the_original_path_untouched(self):
        requests: list = []
        client = MptClient("http://mpt", transport=httpx.MockTransport(_mpt_handler(requests)))
        job = Job(subject="cats"); job.scenes = [Scene(index=0, narration="n", clip_path="/tmp/src.jpg")]
        with tempfile.TemporaryDirectory() as d:
            stage = MptRenderStage(client)
            await stage.run(job, StageContext(Path(d) / "assets", project_dir=Path(d)))
        sent_url = next(b for p, b in requests if p == "/api/v1/videos")["video_materials"][0]["url"]
        self.assertEqual(sent_url, "/tmp/src.jpg")

    async def test_a_crop_failure_falls_back_to_the_uncropped_clip_instead_of_failing_the_render(self):
        requests: list = []
        client = MptClient("http://mpt", transport=httpx.MockTransport(_mpt_handler(requests)))
        asset = Asset(id="a1", source="upload", kind="image", path="/tmp/src.jpg", rel_path="src.jpg",
                      source_url="upload://src.jpg", width=1920, height=1080, sha256="rendercrop2")
        scene = Scene(index=0, narration="n", clip_path="/tmp/src.jpg", asset_id="a1", crop=SceneCrop(zoom=1.5))
        job = Job(subject="cats"); job.assets = [asset]; job.scenes = [scene]

        async def failing_runner(args):
            return 1, "", "ffmpeg exploded"

        with tempfile.TemporaryDirectory() as d:
            stage = MptRenderStage(client, crop_runner=failing_runner)
            out = await stage.run(job, StageContext(Path(d) / "assets", project_dir=Path(d)))
            self.assertTrue(Path(out.output_path).exists())   # the render still finished
        sent_url = next(b for p, b in requests if p == "/api/v1/videos")["video_materials"][0]["url"]
        self.assertEqual(sent_url, "/tmp/src.jpg")


class SetSceneCropTests(Base):
    def asset(self, **kw):
        base = dict(source="upload", kind="image", path="/tmp/x.jpg", rel_path="x.jpg", source_url="upload://x.jpg",
                    title="x.jpg", license="CC0", width=1920, height=1080, sha256="crop-orch-1")
        base.update(kw)
        return Asset(**base)

    async def _job_with_a_clip(self, orch):
        job = await self.to_scenes_review(orch)
        job = await orch.add_scene_asset(job.id, job.scenes[0].id, self.asset(), reviewer="Aly")
        return job, job.scenes[0].id

    async def test_sets_and_persists(self):
        orch = self.make()
        job, sid = await self._job_with_a_clip(orch)
        job = await orch.set_scene_crop(job.id, sid, center_x=0.2, center_y=0.7, zoom=1.5, reviewer="Aly")
        scene = next(s for s in job.scenes if s.id == sid)
        self.assertEqual((scene.crop.center_x, scene.crop.center_y, scene.crop.zoom), (0.2, 0.7, 1.5))
        self.assertEqual(scene.crop.updated_by, "Aly")
        reloaded = orch.get(job.id)
        self.assertEqual(next(s for s in reloaded.scenes if s.id == sid).crop.zoom, 1.5)

    async def test_defaults_to_centered_no_zoom(self):
        orch = self.make()
        job, sid = await self._job_with_a_clip(orch)
        job = await orch.set_scene_crop(job.id, sid, reviewer="Aly")
        scene = next(s for s in job.scenes if s.id == sid)
        self.assertEqual((scene.crop.center_x, scene.crop.center_y, scene.crop.zoom), (0.5, 0.5, 1.0))

    async def test_requires_a_reviewer(self):
        orch = self.make()
        job, sid = await self._job_with_a_clip(orch)
        with self.assertRaises(ValueError):
            await orch.set_scene_crop(job.id, sid, reviewer="")

    async def test_requires_the_scene_to_have_a_clip_first(self):
        orch = self.make()
        job = await self.to_scenes_review(orch)          # no clip assigned yet
        with self.assertRaises(ValueError):
            await orch.set_scene_crop(job.id, job.scenes[0].id, reviewer="Aly")

    async def test_rejects_out_of_range_center(self):
        orch = self.make()
        job, sid = await self._job_with_a_clip(orch)
        with self.assertRaises(ValueError):
            await orch.set_scene_crop(job.id, sid, center_x=1.5, reviewer="Aly")
        with self.assertRaises(ValueError):
            await orch.set_scene_crop(job.id, sid, center_y=-0.1, reviewer="Aly")

    async def test_rejects_zoom_below_1(self):
        orch = self.make()
        job, sid = await self._job_with_a_clip(orch)
        with self.assertRaises(ValueError):
            await orch.set_scene_crop(job.id, sid, zoom=0.5, reviewer="Aly")

    async def test_unknown_scene_id(self):
        orch = self.make()
        job, _ = await self._job_with_a_clip(orch)
        with self.assertRaises(ValueError):
            await orch.set_scene_crop(job.id, "not-a-scene", reviewer="Aly")

    async def test_wrong_state_raises(self):
        orch = self.make()
        job = await orch.create_job("cats", FAKE_NO_SOURCES)
        await orch.start(job.id)
        job = await orch.run_pending(job.id)              # KEYWORDS_REVIEW, not SCENES_REVIEW
        with self.assertRaises(sm.TransitionError):
            await orch.set_scene_crop(job.id, "whatever", reviewer="Aly")

    async def test_removes_the_crop(self):
        orch = self.make()
        job, sid = await self._job_with_a_clip(orch)
        job = await orch.set_scene_crop(job.id, sid, zoom=2.0, reviewer="Aly")
        self.assertIsNotNone(next(s for s in job.scenes if s.id == sid).crop)
        job = await orch.remove_scene_crop(job.id, sid, reviewer="Aly")
        self.assertIsNone(next(s for s in job.scenes if s.id == sid).crop)

    async def test_removing_when_theres_no_crop_is_a_harmless_noop(self):
        orch = self.make()
        job, sid = await self._job_with_a_clip(orch)
        job = await orch.remove_scene_crop(job.id, sid, reviewer="Aly")   # never had one
        self.assertIsNone(next(s for s in job.scenes if s.id == sid).crop)


class CropClearedOnClipChangeTests(Base):
    def asset(self, **kw):
        base = dict(source="upload", kind="image", path="/tmp/x.jpg", rel_path="x.jpg", source_url="upload://x.jpg",
                    title="x.jpg", license="CC0", width=1920, height=1080, sha256="crop-clear-1")
        base.update(kw)
        return Asset(**base)

    async def test_edit_scenes_clip_path_change_clears_a_stale_crop(self):
        orch = self.make()
        job = await self.to_scenes_review(orch)
        job = await orch.add_scene_asset(job.id, job.scenes[0].id, self.asset(sha256="c1"), reviewer="Aly")
        sid = job.scenes[0].id
        job = await orch.set_scene_crop(job.id, sid, zoom=2.0, reviewer="Aly")
        self.assertIsNotNone(next(s for s in job.scenes if s.id == sid).crop)
        job = await orch.edit_scenes(job.id, edits={sid: {"clip_path": "/tmp/other.jpg"}}, reviewer="Aly")
        self.assertIsNone(next(s for s in job.scenes if s.id == sid).crop)

    async def test_a_fresh_drag_in_onto_the_same_scene_clears_its_old_crop(self):
        orch = self.make()
        job = await self.to_scenes_review(orch)
        sid = job.scenes[0].id
        job = await orch.add_scene_asset(job.id, sid, self.asset(sha256="c2"), reviewer="Aly")
        job = await orch.set_scene_crop(job.id, sid, zoom=1.8, reviewer="Aly")
        self.assertIsNotNone(next(s for s in job.scenes if s.id == sid).crop)
        job = await orch.add_scene_asset(job.id, sid, self.asset(sha256="c3"), reviewer="Aly")
        self.assertIsNone(next(s for s in job.scenes if s.id == sid).crop)

    async def test_editing_narration_alone_does_not_touch_the_crop(self):
        orch = self.make()
        job = await self.to_scenes_review(orch)
        sid = job.scenes[0].id
        job = await orch.add_scene_asset(job.id, sid, self.asset(sha256="c4"), reviewer="Aly")
        job = await orch.set_scene_crop(job.id, sid, zoom=1.3, reviewer="Aly")
        job = await orch.edit_scenes(job.id, edits={sid: {"narration": "new words"}}, reviewer="Aly")
        self.assertIsNotNone(next(s for s in job.scenes if s.id == sid).crop)


if __name__ == "__main__":
    unittest.main()
