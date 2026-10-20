"""Detect the closed white lock marker, rather than the open aiming reticle."""

import cv2
import numpy as np


def white_pixels(rgb):
    low, high = rgb.min(axis=2), rgb.max(axis=2)
    return ((low >= 205) & (high.astype(int) - low.astype(int) <= 38)).astype(np.uint8) * 255


class LockDetector:
    def __init__(self, anchor_path):
        anchor = cv2.imread(str(anchor_path), cv2.IMREAD_COLOR)
        if anchor is None:
            raise RuntimeError("ไม่พบภาพอ้างอิงกรอบล็อกเป้า")
        self.anchor = white_pixels(cv2.cvtColor(anchor, cv2.COLOR_BGR2RGB))
        self.templates = {}

    def check(self, image, window_width=None):
        if image is None:
            return False, 0.0
        rgb = np.asarray(image.convert("RGB"))
        height, width = rgb.shape[:2]
        if window_width is None:
            search = rgb[int(height * 0.35):int(height * 0.65),
                         int(width * 0.35):int(width * 0.65)]
            window_width = width
        else:
            search = rgb
        screen = white_pixels(search)
        if window_width not in self.templates:
            scale = window_width / 1920
            self.templates[window_width] = [cv2.resize(
                self.anchor, None, fx=scale * factor, fy=scale * factor,
                interpolation=cv2.INTER_NEAREST,
            ) for factor in (0.90, 0.95, 1.00, 1.05, 1.10)]
        score = 0.0
        for template in self.templates[window_width]:
            if screen.shape[0] < template.shape[0] or screen.shape[1] < template.shape[1]:
                continue
            matches = cv2.matchTemplate(screen, template, cv2.TM_CCOEFF_NORMED)
            score = max(score, float(cv2.minMaxLoc(matches)[1]))
        return score >= 0.78, score
