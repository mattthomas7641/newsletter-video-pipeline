"""Soundtrack evidence for a reference: is there speech, is there music and
at what tempo, where the beats and voice onsets fall, and whether the
picture's cuts and element entrances lock to them. numpy only; the tempo
and on-grid checks come from rea-video (third_party/rea_video/primitives.py)."""
from __future__ import annotations

import numpy as np

from third_party.rea_video.primitives import on_grid_ratio, tempo_from_onsets

SR = 22050
N_FFT = 1024
HOP = 256
HOP_S = HOP / SR


def _stft(x: np.ndarray) -> np.ndarray:
    if len(x) < N_FFT:
        x = np.pad(x, (0, N_FFT - len(x)))
    n = 1 + (len(x) - N_FFT) // HOP
    idx = np.arange(N_FFT)[None, :] + HOP * np.arange(n)[:, None]
    return np.abs(np.fft.rfft(x[idx] * np.hanning(N_FFT)[None, :], axis=1)).astype(np.float32)


def _peaks(env: np.ndarray, min_gap: int, thr: float) -> np.ndarray:
    if len(env) < 3:
        return np.array([], int)
    c = np.where((env[1:-1] > env[:-2]) & (env[1:-1] >= env[2:]) & (env[1:-1] > thr))[0] + 1
    picked: list[int] = []
    for i in c[np.argsort(-env[c])]:
        if all(abs(i - p) >= min_gap for p in picked):
            picked.append(int(i))
    return np.array(sorted(picked), int)


def analyze_audio(samples: np.ndarray | None, cut_times: list[float], event_times: list[float] | None = None) -> dict:
    """Evidence from mono float samples at SR: speech / music presence, tempo
    and beat grid, loudness curve (per 0.5 s), and how cuts and element
    entrances relate to beats and voice onsets."""
    if samples is None or len(samples) < SR * 0.3:
        return {"has_audio": False}
    x = samples.astype(np.float32)
    S = _stft(x)
    freqs = np.fft.rfftfreq(N_FFT, 1 / SR)
    frame_db = 10 * np.log10((S ** 2).mean(axis=1) + 1e-12)
    peak_db = float(np.percentile(frame_db, 99))
    silent = frame_db < peak_db - 45
    # spectral flux onset envelope (positive differences of log magnitude)
    flux = np.maximum(0, np.diff(np.log1p(S), axis=0, prepend=np.log1p(S[:1]))).sum(axis=1)
    # voice band modulation: syllables at 3-8 Hz
    band = (freqs > 300) & (freqs < 3400)
    sb = S[:, band].sum(axis=1)
    win = int(round(1.0 / HOP_S))
    step = int(round(0.5 / HOP_S))
    speech_w = []
    for s in range(0, max(1, len(sb) - win + 1), step):
        seg = sb[s:s + win]
        if seg.std() < 1e-6:
            speech_w.append(False)
            continue
        z = (seg - seg.mean()) / (seg.std() + 1e-9)
        mod = np.abs(np.fft.rfft(z))
        mf = np.fft.rfftfreq(len(z), HOP_S)
        syl = float(mod[(mf > 3) & (mf < 8)].sum() / (mod[(mf > 0.5) & (mf < 20)].sum() + 1e-9))
        voiced = float((~silent[s:s + win]).mean())
        speech_w.append(syl > 0.36 and voiced > 0.4)
    speech_ratio = float(np.mean(speech_w)) if speech_w else 0.0
    # tempo from the onset envelope
    tempo = tempo_from_onsets(flux, HOP_S)
    music_present = bool(tempo and tempo.get("beat_strength", 0) > 0.28)
    # loudness curve per 0.5 s
    per = int(round(0.5 / HOP_S))
    loud = [round(float(frame_db[i:i + per].mean()), 1) for i in range(0, len(frame_db), per)]
    # voice onsets: peaks of the voice-band envelope
    v_env = np.maximum(0, np.diff(np.log1p(sb), prepend=np.log1p(sb[:1])))
    v_peaks = _peaks(v_env, int(0.12 / HOP_S), float(np.percentile(v_env, 85)) if len(v_env) else 0.0) * HOP_S
    out = {
        "has_audio": True,
        "duration": round(len(x) / SR, 3),
        "speech_ratio": round(speech_ratio, 3),
        "speech": speech_ratio > 0.25,
        "music": music_present,
        "tempo_bpm": tempo.get("tempo_bpm") if music_present else None,
        "beat_strength": tempo.get("beat_strength", 0.0),
        "beats": tempo.get("beats", [])[:400] if music_present else [],
        "loudness": loud,
        "voice_onsets": [round(float(t), 3) for t in v_peaks[:400]],
    }
    if cut_times:
        if out["beats"]:
            out["cuts_on_beat"] = on_grid_ratio(cut_times, out["beats"], tolerance=0.1)
        if len(v_peaks) > 3:
            out["cuts_on_voice"] = on_grid_ratio(cut_times, list(v_peaks), tolerance=0.15)
    if event_times and out["beats"]:
        out["events_on_beat"] = on_grid_ratio(event_times, out["beats"], tolerance=0.1)
    out["sync"] = ("beat-synced" if out.get("cuts_on_beat", {}).get("locked") or out.get("events_on_beat", {}).get("locked")
                   else ("voice-synced" if out.get("cuts_on_voice", {}).get("locked") else "free"))
    return out
