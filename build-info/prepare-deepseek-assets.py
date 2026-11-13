import hashlib
import importlib.util
import io
import json
import zipfile
from pathlib import Path

from PIL import Image, ImageDraw

task_directory = Path(__file__).resolve().parent
repository = task_directory / "xiaozhi-esp32"
specification = importlib.util.spec_from_file_location("assets_builder", repository / "scripts/build_default_assets.py")
assets_builder = importlib.util.module_from_spec(specification)
specification.loader.exec_module(assets_builder)
asset_files = assets_builder.read_assets_entries(repository / "build/generated_assets.bin")
asset_index = json.loads(asset_files["index.json"][0])
original_names = [entry["name"] for entry in asset_index["emoji_collection"]]
for entry in asset_index["emoji_collection"]:
    asset_files.pop(entry["file"])

source_package = task_directory / "deepseek-chan.codex-pet.zip"
with zipfile.ZipFile(source_package) as package:
    if package.testzip() is not None:
        raise RuntimeError("Source package integrity failure")
    pet_manifest = json.loads(package.read("pet.json"))
    if pet_manifest["id"] != "deepseek-chan" or pet_manifest["spriteVersionNumber"] != 2:
        raise RuntimeError("Unexpected pet source")
    source_sheet_bytes = package.read("spritesheet.webp")
    source_sheet = Image.open(io.BytesIO(source_sheet_bytes)).convert("RGBA")
if source_sheet.size != (1536, 2288):
    raise RuntimeError("Unexpected source atlas dimensions")

animation_directory = task_directory / "deepseek-animations"
animation_directory.mkdir(exist_ok=True)
frame_size = (48, 52)
actions = {
    "idle": {"row": 0, "frames": 6, "duration_ms": 260},
    "waving": {"row": 3, "frames": 4, "duration_ms": 180},
    "jumping": {"row": 4, "frames": 5, "duration_ms": 180},
    "failed": {"row": 5, "frames": 8, "duration_ms": 240},
    "waiting": {"row": 6, "frames": 6, "duration_ms": 260},
}
preview_frames = []
for action_name, action in actions.items():
    frames = []
    for frame_index in range(action["frames"]):
        source_frame = source_sheet.crop((frame_index * 192, action["row"] * 208, (frame_index + 1) * 192, (action["row"] + 1) * 208))
        if source_frame.getchannel("A").getbbox() is None:
            raise RuntimeError(f"Empty source frame: {action_name}/{frame_index}")
        frames.append(source_frame.resize(frame_size, Image.Resampling.LANCZOS))
    palette_source = Image.new("RGB", (frame_size[0], frame_size[1] * len(frames)))
    for frame_index, frame in enumerate(frames):
        palette_source.paste(frame.convert("RGB"), (0, frame_index * frame_size[1]))
    shared_palette = palette_source.quantize(colors=127, method=Image.Quantize.MEDIANCUT)
    palette_bytes = [0, 0, 0] + shared_palette.getpalette()[:127 * 3]
    palette_bytes += [0] * (768 - len(palette_bytes))
    indexed_frames = []
    for frame in frames:
        quantized = frame.convert("RGB").quantize(palette=shared_palette, dither=Image.Dither.NONE)
        alpha_values = frame.getchannel("A").tobytes()
        indexed_bytes = bytes(color + 1 if alpha >= 96 else 0 for color, alpha in zip(quantized.tobytes(), alpha_values))
        indexed_frame = Image.frombytes("P", frame_size, indexed_bytes)
        indexed_frame.putpalette(palette_bytes)
        indexed_frames.append(indexed_frame)
    animation_path = animation_directory / f"{action_name}.gif"
    indexed_frames[0].save(animation_path, save_all=True, append_images=indexed_frames[1:], duration=action["duration_ms"], loop=0, transparency=0, background=0, disposal=2, optimize=False)
    with Image.open(animation_path) as verification:
        if verification.size != frame_size or verification.n_frames != action["frames"]:
            raise RuntimeError(f"Animation dimensions/frame count mismatch: {action_name}")
        for frame_index in range(verification.n_frames):
            verification.seek(frame_index)
            if verification.convert("RGBA").getchannel("A").getbbox() is None:
                raise RuntimeError(f"Empty exported frame: {action_name}/{frame_index}")
    animation_bytes = animation_path.read_bytes()
    asset_files[animation_path.name] = (animation_bytes, *frame_size)
    action["file_size"] = len(animation_bytes)
    preview_frames.append(frames[1])

emotion_actions = {
    "angry": "failed", "confident": "waving", "confused": "waiting",
    "cool": "idle", "crying": "failed", "delicious": "waving",
    "embarrassed": "waiting", "funny": "jumping", "happy": "waving",
    "kissy": "waving", "laughing": "jumping", "loving": "waving",
    "neutral": "idle", "relaxed": "idle", "sad": "failed",
    "shocked": "jumping", "silly": "jumping", "sleepy": "idle",
    "surprised": "jumping", "thinking": "waiting", "winking": "waving",
}
if set(emotion_actions) != set(original_names):
    raise RuntimeError("Emotion mapping does not cover the original collection")
asset_index["emoji_collection"] = [{"name": emotion, "file": f"{action}.gif"} for emotion, action in emotion_actions.items()]
asset_files["index.json"] = (json.dumps(asset_index, ensure_ascii=False, indent=2).encode(), 0, 0)
custom_pack = task_directory / "deepseek-assets.bin"
custom_pack.write_bytes(assets_builder.pack_assets_entries(asset_files))
if custom_pack.stat().st_size > 2 * 1024 * 1024:
    raise RuntimeError("Custom pack exceeds the resource partition")
roundtrip_files = assets_builder.read_assets_entries(custom_pack)
if roundtrip_files != asset_files:
    raise RuntimeError("Custom pack roundtrip failed")

preview = Image.new("RGB", (480, 140), "#101820")
preview_drawing = ImageDraw.Draw(preview)
for action_index, (action_name, frame) in enumerate(zip(actions, preview_frames)):
    preview.paste(frame, (action_index * 96 + 24, 18), frame)
    preview_drawing.text((action_index * 96 + 12, 95), action_name, fill="white")
preview.save(animation_directory / "preview.png")
metadata = {
    "pet_id": "deepseek-chan", "author": "Dullsaw",
    "source_url": "https://codex-pets.net/#/?q=deepseek",
    "source_download_url": "https://codex-pets.net/api/pets/deepseek-chan/download?v=1791020401654",
    "source_package_sha256": hashlib.sha256(source_package.read_bytes()).hexdigest(),
    "source_sheet_sha256": hashlib.sha256(source_sheet_bytes).hexdigest(),
    "frame_width": frame_size[0], "frame_height": frame_size[1],
    "pixel_buffer_bytes": 5 * frame_size[0] * frame_size[1],
    "actions": actions, "emotion_actions": emotion_actions,
    "assets_size": custom_pack.stat().st_size,
    "assets_sha256": hashlib.sha256(custom_pack.read_bytes()).hexdigest(),
}
(animation_directory / "animation-info.json").write_text(json.dumps(metadata, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
print(json.dumps(metadata, ensure_ascii=False, indent=2))
