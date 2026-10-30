"""Non-destructive source development and camera-space gray sampling."""
import numpy as np
import rawpy
import tifffile


DEVELOP_FIELDS = ("exposure", "contrast", "saturation", "warmth", "tint", "wb_multipliers", "wb_camera")


def recipe_key(frame):
    if frame is None:
        return (0, 0, 0, 0, 0, None, "")
    return tuple(tuple(value) if isinstance(value, list) else value
                 for value in (getattr(frame, field) for field in DEVELOP_FIELDS))


def camera_identity(path):
    try:
        with tifffile.TiffFile(path) as image:
            tags = image.pages[0].tags
            return " ".join(str(tags[name].value).strip() for name in ("Make", "Model") if name in tags)
    except (OSError, ValueError):
        return ""


def white_balance_kwargs(raw, frame=None):
    if frame is None or (frame.wb_multipliers is None and frame.warmth == 0 and frame.tint == 0):
        return {"use_camera_wb": True}
    base = list(frame.wb_multipliers or raw.camera_whitebalance)
    daylight = raw.daylight_whitebalance
    for index in range(4):
        if base[index] <= 0:
            base[index] = base[1] if index == 3 else daylight[index]
        if base[index] <= 0:
            raise ValueError("This RAW does not provide usable white-balance metadata.")
    colors = raw.color_desc.decode("ascii", errors="ignore")
    for index, color in enumerate(colors[:4]):
        if color == "R":
            base[index] *= 2 ** (frame.warmth / 100)
        elif color == "B":
            base[index] *= 2 ** (-frame.warmth / 100)
        elif color == "G":
            base[index] *= 1.5 ** (-frame.tint / 100)
    return {"use_camera_wb": False, "user_wb": base}


def srgb_to_linear(rgb):
    return np.where(rgb <= 0.04045, rgb / 12.92, ((rgb + 0.055) / 1.055) ** 2.4)


def develop_pixels(rgb, frame):
    from .imaging import linear_to_srgb
    exposed = np.clip(rgb * (2 ** frame.exposure), 0, 1)
    if frame.contrast == 0 and frame.saturation == 0:
        return exposed
    display = linear_to_srgb(exposed)
    display = np.clip(0.5 + (display - 0.5) * (2 ** (frame.contrast / 100)), 0, 1)
    if frame.saturation != 0:
        luminance = np.sum(display * np.array([0.2126, 0.7152, 0.0722], np.float32), axis=-1, keepdims=True)
        display = np.clip(luminance + (display - luminance) * (1 + frame.saturation / 100), 0, 1)
    return srgb_to_linear(display).astype(np.float32)


def pick_gray(path, expected_sha256, x, y):
    from .imaging import fingerprint
    if fingerprint(path) != expected_sha256:
        raise ValueError("The original photograph has changed. Relink it before sampling.")
    with rawpy.imread(str(path)) as raw:
        if raw.raw_image_visible.ndim != 2:
            raise ValueError("Gray picking currently requires a mosaic RAW photograph.")
        if raw.sizes.flip == 3:
            x, y = 1 - x, 1 - y
        elif raw.sizes.flip == 5:
            x, y = 1 - y, x
        elif raw.sizes.flip == 6:
            x, y = y, 1 - x
        height, width = raw.raw_image_visible.shape
        radius = max(6, round(min(width, height) * 0.004))
        px, py = round(x * (width - 1)), round(y * (height - 1))
        bounds = (slice(max(0, py - radius), min(height, py + radius + 1)),
                  slice(max(0, px - radius), min(width, px + radius + 1)))
        pixels = raw.raw_image_visible[bounds].astype(np.float32)
        channels = raw.raw_colors_visible[bounds]
        levels = raw.camera_white_level_per_channel or [raw.white_level] * 4
        means = []
        for channel in range(4):
            values = pixels[channels == channel] - raw.black_level_per_channel[channel]
            if not values.size:
                means.append(None)
                continue
            ceiling = (levels[channel] or raw.white_level) - raw.black_level_per_channel[channel]
            if np.mean(values >= ceiling * 0.98) > 0.05 or np.median(values) < ceiling * 0.01:
                raise ValueError("Choose a neutral midtone patch away from clipped highlights and deep shadows.")
            low, high = np.percentile(values, [10, 90])
            means.append(float(np.mean(values[(values >= low) & (values <= high)])))
        colors = raw.color_desc.decode("ascii", errors="ignore")
        green = [value for index, value in enumerate(means) if value and colors[index] == "G"]
        if not green:
            raise ValueError("Could not sample usable green-channel data.")
        target = float(np.mean(green))
        gains = [target / value if value else 1.0 for value in means]
        if any(not 0.05 <= value <= 32 for value in gains):
            raise ValueError("The selected patch is not suitable for neutral white balance.")
    return {"wb_multipliers": gains, "wb_camera": camera_identity(path), "warmth": 0.0, "tint": 0.0}
